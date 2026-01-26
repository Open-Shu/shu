"""
Model Configuration service for Shu.

This service manages the ModelConfiguration entity - the foundational abstraction
that combines base models + prompts + optional knowledge bases into user-facing
configurations that users select for chat and other interactions.
"""

import logging
from typing import List, Optional, Dict, Any, TYPE_CHECKING
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from sqlalchemy.orm import selectinload

if TYPE_CHECKING:
    from ..auth.models import User

from shu.services.providers.adapter_base import get_adapter_from_provider
from shu.services.providers.parameter_definitions import serialize_parameter_mapping

from ..models.model_configuration import ModelConfiguration
from ..models.model_configuration_kb_prompt import ModelConfigurationKBPrompt
from ..models.llm_provider import LLMProvider, LLMModel
from ..models.prompt import Prompt
from ..models.knowledge_base import KnowledgeBase
from ..auth.models import User
from ..schemas.model_configuration import (
    ModelConfigurationCreate,
    ModelConfigurationUpdate,
    ModelConfigurationResponse,
    ModelConfigurationList
)
from ..core.exceptions import ShuException, ValidationError
from ..llm.param_mapping import build_provider_params

logger = logging.getLogger(__name__)


class ModelConfigurationService:
    """Service for managing model configurations."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def create_model_configuration(
        self, 
        config_data: ModelConfigurationCreate,
        created_by: str
    ) -> ModelConfiguration:
        """Create a new model configuration.
        
        Args:
            config_data: The configuration data from the request.
            created_by: The user ID of the creator (from authenticated user).
        
        Returns:
            The created ModelConfiguration.
        """
        try:
            provider = await self._get_and_validate_llm_provider(config_data.llm_provider_id)
            
            # Validate that the model exists for this provider
            model_result = await self.db.execute(
                select(LLMModel).where(
                    and_(
                        LLMModel.provider_id == config_data.llm_provider_id,
                        LLMModel.model_name == config_data.model_name,
                        LLMModel.is_active == True
                    )
                )
            )
            model = model_result.scalar_one_or_none()
            if not model:
                raise ShuException(
                    f"Model {config_data.model_name} not found for provider {config_data.llm_provider_id}",
                    "MODEL_NOT_FOUND"
                )
            
            # Validate prompt if provided
            if config_data.prompt_id:
                prompt_result = await self.db.execute(
                    select(Prompt).where(
                        and_(
                            Prompt.id == config_data.prompt_id,
                            Prompt.is_active == True
                        )
                    )
                )
                prompt = prompt_result.scalar_one_or_none()
                if not prompt:
                    raise ShuException(
                        f"Prompt {config_data.prompt_id} not found or inactive",
                        "PROMPT_NOT_FOUND"
                    )
            
            # Validate knowledge bases if provided
            knowledge_bases = []
            if config_data.knowledge_base_ids:
                kb_result = await self.db.execute(
                    select(KnowledgeBase).where(
                        and_(
                            KnowledgeBase.id.in_(config_data.knowledge_base_ids),
                            KnowledgeBase.status == "active"
                        )
                    )
                )
                knowledge_bases = kb_result.scalars().all()
                
                found_kb_ids = {kb.id for kb in knowledge_bases}
                missing_kb_ids = set(config_data.knowledge_base_ids) - found_kb_ids
                if missing_kb_ids:
                    raise ShuException(
                        f"Knowledge bases not found: {missing_kb_ids}",
                        "KNOWLEDGE_BASES_NOT_FOUND"
                    )
            
            # Check for duplicate configuration names
            existing_result = await self.db.execute(
                select(ModelConfiguration).where(
                    and_(
                        ModelConfiguration.name == config_data.name,
                        ModelConfiguration.is_active == True
                    )
                )
            )
            existing = existing_result.scalar_one_or_none()
            if existing:
                raise ShuException(
                    f"Model configuration with name '{config_data.name}' already exists",
                    "DUPLICATE_NAME"
                )

            # Create the model configuration
            model_config = ModelConfiguration(
                name=config_data.name,
                description=config_data.description,
                llm_provider_id=config_data.llm_provider_id,
                model_name=config_data.model_name,
                prompt_id=config_data.prompt_id,
                is_active=config_data.is_active,
                parameter_overrides=await self._create_model_configuration_parameter_overrides(config_data, provider),
                created_by=created_by,
                functionalities=config_data.functionalities,
            )
            logger.debug("ModelConfiguration overrides keys saved: %s",list(model_config.parameter_overrides.keys()))

            # Add knowledge bases
            model_config.knowledge_bases = knowledge_bases

            self.db.add(model_config)
            await self.db.commit()
            await self.db.refresh(model_config)

            # Handle KB prompt assignments if provided
            if config_data.kb_prompt_assignments:
                for assignment_data in config_data.kb_prompt_assignments:
                    try:
                        await self.assign_kb_prompt(
                            model_config_id=model_config.id,
                            knowledge_base_id=assignment_data.knowledge_base_id,
                            prompt_id=assignment_data.prompt_id
                        )
                        logger.debug(f"Assigned KB prompt: KB {assignment_data.knowledge_base_id} -> Prompt {assignment_data.prompt_id}")
                    except Exception as e:
                        logger.warning(f"Failed to assign KB prompt during creation: {e}")
                        # Continue with other assignments rather than failing the entire creation

            logger.info(f"Created model configuration: {model_config.name} (ID: {model_config.id})")
            return model_config
            
        except ShuException:
            await self.db.rollback()
            raise
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to create model configuration: {e}", exc_info=True)
            raise ShuException(f"Failed to create model configuration: {str(e)}", "CREATE_ERROR")

    async def get_model_configuration(
        self, 
        config_id: str, 
        include_relationships: bool = True,
        current_user: Optional[User] = None
    ) -> Optional[ModelConfiguration]:
        """Get a model configuration by ID."""
        try:
            query = select(ModelConfiguration).where(ModelConfiguration.id == config_id)

            # Determine which relationships to eager-load.
            #
            # We always need knowledge_bases when current_user is provided so that
            # RBAC checks can run without triggering async lazy-loading (which
            # causes MissingGreenlet under AsyncSession). For callers that do
            # want full relationship graphs, we include all related entities.
            relationship_options = []
            if include_relationships:
                relationship_options.extend(
                    [
                        selectinload(ModelConfiguration.llm_provider),
                        selectinload(ModelConfiguration.prompt),
                        selectinload(ModelConfiguration.knowledge_bases),
                        selectinload(ModelConfiguration.kb_prompt_assignments).selectinload(
                            ModelConfigurationKBPrompt.prompt
                        ),
                    ]
                )
            elif current_user:
                # Even when relationships are excluded from the API payload,
                # we still need knowledge_bases preloaded for RBAC checks.
                relationship_options.append(selectinload(ModelConfiguration.knowledge_bases))

            if relationship_options:
                query = query.options(*relationship_options)
            
            result = await self.db.execute(query)
            config = result.scalar_one_or_none()
            
            # Check permissions if current_user is provided and config exists
            if config and current_user:
                from ..auth.rbac import rbac
                
                # Check if user has access to all knowledge bases in this configuration
                if hasattr(config, 'knowledge_bases') and config.knowledge_bases:
                    for kb in config.knowledge_bases:
                        if not await rbac.can_access_knowledge_base(current_user, kb.id, self.db):
                            logger.warning(f"User {current_user.email} denied access to KB {kb.id} in config {config.id}")
                            return None  # Return None to indicate access denied
                
                logger.debug(f"User {current_user.email} has access to config {config.id}")
            
            return config
            
        except Exception as e:
            logger.error(f"Failed to get model configuration {config_id}: {e}", exc_info=True)
            raise ShuException(f"Failed to get model configuration: {str(e)}", "GET_ERROR")
    
    async def list_model_configurations(
        self,
        page: int = 1,
        per_page: int = 50,
        active_only: bool = True,
        is_active_filter: Optional[bool] = None,
        created_by: Optional[str] = None,
        include_relationships: bool = True,
        current_user: Optional[User] = None
    ) -> ModelConfigurationList:
        """List model configurations with pagination."""
        try:
            # Build base query
            query = select(ModelConfiguration)
            count_query = select(func.count(ModelConfiguration.id))
            
            # Apply filters
            filters = []

            # Handle is_active filtering
            if is_active_filter is not None:
                # When is_active_filter is specified, filter by exact value
                filters.append(ModelConfiguration.is_active == is_active_filter)
            elif active_only:
                # Fall back to active_only behavior (only show active)
                filters.append(ModelConfiguration.is_active == True)
            # If neither is specified or active_only=False, show all

            if created_by:
                filters.append(ModelConfiguration.created_by == created_by)
            
            if filters:
                query = query.where(and_(*filters))
                count_query = count_query.where(and_(*filters))
            
            # Add relationships if requested
            relationship_options = []
            if include_relationships:
                relationship_options.extend(
                    [
                        selectinload(ModelConfiguration.llm_provider),
                        selectinload(ModelConfiguration.prompt),
                        selectinload(ModelConfiguration.knowledge_bases),
                        selectinload(ModelConfiguration.kb_prompt_assignments).selectinload(
                            ModelConfigurationKBPrompt.prompt
                        ),
                    ]
                )
            elif current_user:
                # For RBAC filtering we must know which KBs are attached to each
                # configuration. Eager-load knowledge_bases even when the caller
                # has requested relationships to be excluded from the response,
                # to avoid async lazy-loading (MissingGreenlet) with AsyncSession.
                relationship_options.append(selectinload(ModelConfiguration.knowledge_bases))

            if relationship_options:
                query = query.options(*relationship_options)
            
            # Apply pagination
            offset = (page - 1) * per_page
            query = (
                query
                    .order_by(ModelConfiguration.name.asc())
                    .offset(offset)
                    .limit(per_page)
            )
            
            # Execute queries
            result = await self.db.execute(query)
            configurations = result.scalars().all()

            # Filter by user permissions if current_user is provided
            if current_user:
                from ..auth.rbac import rbac
                accessible_configurations = []
                
                for config in configurations:
                    # Check if user has access to all knowledge bases in this configuration
                    has_access = True
                    if hasattr(config, 'knowledge_bases') and config.knowledge_bases:
                        for kb in config.knowledge_bases:
                            if not await rbac.can_access_knowledge_base(current_user, kb.id, self.db):
                                has_access = False
                                logger.debug(f"User {current_user.email} denied access to KB {kb.id} in config {config.id}")
                                break
                    
                    if has_access:
                        accessible_configurations.append(config)
                        logger.debug(f"User {current_user.email} has access to config {config.id}")
                    else:
                        logger.debug(f"User {current_user.email} denied access to config {config.id}")
                
                configurations = accessible_configurations

            count_result = await self.db.execute(count_query)
            total = count_result.scalar()
            
            # Calculate pagination info
            pages = (total + per_page - 1) // per_page
            
            return ModelConfigurationList(
                items=[
                    self._to_response(config) for config in configurations
                ],
                total=total,
                page=page,
                per_page=per_page,
                pages=pages
            )
            
        except Exception as e:
            logger.error(f"Failed to list model configurations: {e}", exc_info=True)
            raise ShuException(f"Failed to list model configurations: {str(e)}", "LIST_ERROR")

    def _to_response(self, config: ModelConfiguration) -> ModelConfigurationResponse:
        """Convert ModelConfiguration to response schema with proper serialization."""
        # Serialize relationships to dictionaries
        llm_provider_dict = None
        try:
            if hasattr(config, 'llm_provider') and config.llm_provider:
                llm_provider_dict = {
                    "id": config.llm_provider.id,
                    "name": config.llm_provider.name,
                    "provider_type": config.llm_provider.provider_type,
                    "api_endpoint": config.llm_provider.api_endpoint,
                    "is_active": config.llm_provider.is_active
                }
        except Exception:
            # Relationship not loaded, skip
            pass

        prompt_dict = None
        try:
            if hasattr(config, 'prompt') and config.prompt:
                prompt_dict = {
                    "id": config.prompt.id,
                    "name": config.prompt.name,
                    "content": config.prompt.content,
                    "entity_type": config.prompt.entity_type
                }
        except Exception:
            # Relationship not loaded, skip
            pass

        knowledge_bases_list = []
        try:
            if hasattr(config, 'knowledge_bases') and config.knowledge_bases:
                knowledge_bases_list = [
                    {
                        "id": kb.id,
                        "name": kb.name,
                        "description": kb.description,
                        "status": kb.status,
                        "is_active": kb.is_active
                    } for kb in config.knowledge_bases
                ]
        except Exception as e:
            logger.error(f"Failed to serialize knowledge bases for config {config.id}: {e}", exc_info=True)
            # Relationship not loaded, skip
            pass

        # Serialize KB prompt assignments
        kb_prompts_dict = {}
        try:
            if hasattr(config, 'kb_prompt_assignments') and config.kb_prompt_assignments:
                for assignment in config.kb_prompt_assignments:
                    if assignment.is_active and hasattr(assignment, 'prompt') and assignment.prompt:
                        kb_prompts_dict[assignment.knowledge_base_id] = {
                            "id": assignment.prompt.id,
                            "name": assignment.prompt.name,
                            "description": assignment.prompt.description,
                            "content": assignment.prompt.content,
                            "assigned_at": assignment.assigned_at
                        }
        except Exception as e:
            logger.error(f"Failed to serialize KB prompts for config {config.id}: {e}", exc_info=True)
            # Relationship not loaded, skip
            pass

        return ModelConfigurationResponse(
            id=config.id,
            name=config.name,
            description=config.description,
            llm_provider_id=config.llm_provider_id,
            model_name=config.model_name,
            prompt_id=config.prompt_id,
            is_active=config.is_active,
            created_by=config.created_by,
            created_at=config.created_at,
            updated_at=config.updated_at,
            functionalities=(getattr(config, "functionalities", None) or {}),
            llm_provider=llm_provider_dict,
            parameter_overrides=(getattr(config, "parameter_overrides", None) or {}),
            prompt=prompt_dict,
            knowledge_bases=knowledge_bases_list,
            kb_prompts=kb_prompts_dict,
            has_knowledge_bases=len(knowledge_bases_list) > 0,
            knowledge_base_count=len(knowledge_bases_list)
        )

    async def update_model_configuration(
        self,
        config_id: str,
        update_data: ModelConfigurationUpdate
    ) -> Optional[ModelConfiguration]:
        """Update a model configuration."""
        try:
            # Get existing configuration
            config = await self.get_model_configuration(config_id, include_relationships=True)
            if not config:
                raise ShuException(f"Model configuration {config_id} not found", "NOT_FOUND")

            # Update fields
            update_dict = update_data.dict(exclude_unset=True)

            # Get the new provider id, and default to old one if missing. Set it on the config.
            provider = await self._get_and_validate_llm_provider(update_dict.get('llm_provider_id', config.llm_provider_id))
            config.llm_provider_id = provider.id

            # Handle knowledge base updates
            if 'knowledge_base_ids' in update_dict:
                kb_ids = update_dict.pop('knowledge_base_ids')
                if kb_ids is not None:
                    # Validate knowledge bases
                    kb_result = await self.db.execute(
                        select(KnowledgeBase).where(
                            and_(
                                KnowledgeBase.id.in_(kb_ids),
                                KnowledgeBase.status == "active"
                            )
                        )
                    )
                    knowledge_bases = kb_result.scalars().all()

                    found_kb_ids = {kb.id for kb in knowledge_bases}
                    missing_kb_ids = set(kb_ids) - found_kb_ids
                    if missing_kb_ids:
                        raise ShuException(
                            f"Knowledge bases not found: {missing_kb_ids}",
                            "KNOWLEDGE_BASES_NOT_FOUND"
                        )

                    config.knowledge_bases = knowledge_bases

            # Handle KB prompt assignment updates
            if 'kb_prompt_assignments' in update_dict:
                kb_prompt_assignments = update_dict.pop('kb_prompt_assignments')
                if kb_prompt_assignments is not None:
                    # Clear existing assignments for this model config
                    existing_assignments = await self.db.execute(
                        select(ModelConfigurationKBPrompt).where(
                            ModelConfigurationKBPrompt.model_configuration_id == config_id
                        )
                    )
                    for assignment in existing_assignments.scalars().all():
                        assignment.is_active = False

                    # Add new assignments
                    for assignment_data in kb_prompt_assignments:
                        try:
                            # Accept either dicts (from update_dict) or Pydantic objects
                            if isinstance(assignment_data, dict):
                                kb_id_val = assignment_data.get('knowledge_base_id')
                                prompt_id_val = assignment_data.get('prompt_id')
                            else:
                                kb_id_val = getattr(assignment_data, 'knowledge_base_id', None)
                                prompt_id_val = getattr(assignment_data, 'prompt_id', None)
                            if not kb_id_val or not prompt_id_val:
                                raise ShuException("invalid_kb_prompt_assignment", "VALIDATION_ERROR")
                            await self.assign_kb_prompt(
                                model_config_id=config_id,
                                knowledge_base_id=kb_id_val,
                                prompt_id=prompt_id_val,
                            )
                            logger.debug(f"Updated KB prompt: KB {kb_id_val} -> Prompt {prompt_id_val}")
                        except Exception as e:
                            logger.warning(f"Failed to assign KB prompt during update: {e}")

            # Handle parameter_overrides validation/update
            config.parameter_overrides = await self._update_model_configuration_parameter_overrides(update_dict, provider) or {}
            logger.debug("ModelConfiguration overrides keys updated: %s", list(config.parameter_overrides.keys()))

            # Update other fields
            for field, value in update_dict.items():
                setattr(config, field, value)

            await self.db.commit()
            await self.db.refresh(config)

            logger.info(f"Updated model configuration: {config.name} (ID: {config.id})")
            return config

        except ShuException:
            await self.db.rollback()
            raise
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to update model configuration {config_id}: {e}", exc_info=True)
            raise ShuException(f"Failed to update model configuration: {str(e)}", "UPDATE_ERROR")

    async def delete_model_configuration(self, config_id: str) -> bool:
        """Delete a model configuration."""
        try:
            config = await self.get_model_configuration(config_id, include_relationships=False)
            if not config:
                return False

            await self.db.delete(config)
            await self.db.commit()

            logger.info(f"Deleted model configuration: {config.name} (ID: {config.id})")
            return True

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to delete model configuration {config_id}: {e}", exc_info=True)
            raise ShuException(f"Failed to delete model configuration: {str(e)}", "DELETE_ERROR")

    async def get_active_configurations_for_user(self, user_id: str) -> List[ModelConfiguration]:
        """Get active model configurations for a specific user."""
        try:
            result = await self.db.execute(
                select(ModelConfiguration)
                .where(
                    and_(
                        ModelConfiguration.is_active == True,
                        or_(
                            ModelConfiguration.created_by == user_id,
                            # Add logic here for shared configurations if needed
                        )
                    )
                )
                .options(
                    selectinload(ModelConfiguration.llm_provider),
                    selectinload(ModelConfiguration.prompt),
                    selectinload(ModelConfiguration.knowledge_bases),
                    selectinload(ModelConfiguration.kb_prompt_assignments).selectinload(ModelConfigurationKBPrompt.prompt)
                )
            )
            return result.scalars().all()

        except Exception as e:
            logger.error(f"Failed to get configurations for user {user_id}: {e}", exc_info=True)
            raise ShuException(f"Failed to get user configurations: {str(e)}", "GET_USER_CONFIGS_ERROR")

    # KB Prompt Management Methods

    async def assign_kb_prompt(
        self,
        model_config_id: str,
        knowledge_base_id: str,
        prompt_id: str
    ) -> ModelConfigurationKBPrompt:
        """
        Assign a prompt to a specific knowledge base for a model configuration.

        Args:
            model_config_id: Model configuration ID
            knowledge_base_id: Knowledge base ID
            prompt_id: Prompt ID

        Returns:
            Created ModelConfigurationKBPrompt assignment

        Raises:
            ShuException: If validation fails or assignment already exists
        """
        try:
            # Validate model configuration exists
            model_config = await self.get_model_configuration(model_config_id)
            if not model_config:
                raise ShuException(f"Model configuration {model_config_id} not found", "MODEL_CONFIG_NOT_FOUND")

            # Validate knowledge base exists and is associated with model config
            kb_associated = any(kb.id == knowledge_base_id for kb in model_config.knowledge_bases)
            if not kb_associated:
                raise ShuException(
                    f"Knowledge base {knowledge_base_id} is not associated with model configuration {model_config_id}",
                    "KB_NOT_ASSOCIATED"
                )

            # Validate prompt exists and is active (any entity type can be used for KB prompts)
            prompt_result = await self.db.execute(
                select(Prompt).where(
                    and_(
                        Prompt.id == prompt_id,
                        Prompt.is_active == True
                    )
                )
            )
            prompt = prompt_result.scalar_one_or_none()
            if not prompt:
                raise ShuException(f"Prompt {prompt_id} not found or inactive", "PROMPT_NOT_FOUND")

            # Check if assignment already exists
            existing_result = await self.db.execute(
                select(ModelConfigurationKBPrompt).where(
                    and_(
                        ModelConfigurationKBPrompt.model_configuration_id == model_config_id,
                        ModelConfigurationKBPrompt.knowledge_base_id == knowledge_base_id
                    )
                )
            )
            existing = existing_result.scalar_one_or_none()

            if existing:
                # Update existing assignment
                existing.prompt_id = prompt_id
                existing.is_active = True
                await self.db.commit()
                await self.db.refresh(existing)
                logger.info(f"Updated KB prompt assignment: model_config={model_config_id}, kb={knowledge_base_id}, prompt={prompt_id}")
                return existing
            else:
                # Create new assignment
                assignment = ModelConfigurationKBPrompt(
                    model_configuration_id=model_config_id,
                    knowledge_base_id=knowledge_base_id,
                    prompt_id=prompt_id,
                    is_active=True
                )

                self.db.add(assignment)
                await self.db.commit()
                await self.db.refresh(assignment)

                logger.info(f"Created KB prompt assignment: model_config={model_config_id}, kb={knowledge_base_id}, prompt={prompt_id}")
                return assignment

        except Exception as e:
            logger.error(f"Failed to assign KB prompt: {e}", exc_info=True)
            raise ShuException(f"Failed to assign KB prompt: {str(e)}", "ASSIGN_KB_PROMPT_ERROR")

    async def remove_kb_prompt(self, model_config_id: str, knowledge_base_id: str) -> bool:
        """
        Remove a KB prompt assignment from a model configuration.

        Args:
            model_config_id: Model configuration ID
            knowledge_base_id: Knowledge base ID

        Returns:
            True if assignment was removed, False if it didn't exist

        Raises:
            ShuException: If validation fails
        """
        try:
            # Validate model configuration exists
            model_config = await self.get_model_configuration(model_config_id)
            if not model_config:
                raise ShuException(f"Model configuration {model_config_id} not found", "MODEL_CONFIG_NOT_FOUND")

            # Find existing assignment
            assignment_result = await self.db.execute(
                select(ModelConfigurationKBPrompt).where(
                    and_(
                        ModelConfigurationKBPrompt.model_configuration_id == model_config_id,
                        ModelConfigurationKBPrompt.knowledge_base_id == knowledge_base_id,
                        ModelConfigurationKBPrompt.is_active == True
                    )
                )
            )
            assignment = assignment_result.scalar_one_or_none()

            if not assignment:
                return False

            # Deactivate the assignment (soft delete)
            assignment.is_active = False
            await self.db.commit()

            logger.info(f"Removed KB prompt assignment: model_config={model_config_id}, kb={knowledge_base_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to remove KB prompt: {e}", exc_info=True)
            raise ShuException(f"Failed to remove KB prompt: {str(e)}", "REMOVE_KB_PROMPT_ERROR")

    async def get_kb_prompts(self, model_config_id: str) -> Dict[str, Dict[str, Any]]:
        """
        Get all KB prompt assignments for a model configuration.

        Args:
            model_config_id: Model configuration ID

        Returns:
            Dictionary mapping knowledge_base_id to prompt information

        Raises:
            ShuException: If validation fails
        """
        try:
            # Validate model configuration exists
            model_config = await self.get_model_configuration(model_config_id)
            if not model_config:
                raise ShuException(f"Model configuration {model_config_id} not found", "MODEL_CONFIG_NOT_FOUND")

            # Get all active KB prompt assignments with relationships
            assignments_result = await self.db.execute(
                select(ModelConfigurationKBPrompt)
                .options(
                    selectinload(ModelConfigurationKBPrompt.knowledge_base),
                    selectinload(ModelConfigurationKBPrompt.prompt)
                )
                .where(
                    and_(
                        ModelConfigurationKBPrompt.model_configuration_id == model_config_id,
                        ModelConfigurationKBPrompt.is_active == True
                    )
                )
            )
            assignments = assignments_result.scalars().all()

            # Build response dictionary
            kb_prompts = {}
            for assignment in assignments:
                kb_prompts[assignment.knowledge_base_id] = {
                    "knowledge_base": {
                        "id": assignment.knowledge_base.id,
                        "name": assignment.knowledge_base.name,
                        "description": assignment.knowledge_base.description
                    },
                    "prompt": {
                        "id": assignment.prompt.id,
                        "name": assignment.prompt.name,
                        "description": assignment.prompt.description,
                        "content": assignment.prompt.content
                    },
                    "assigned_at": assignment.assigned_at
                }

            logger.debug(f"Retrieved {len(kb_prompts)} KB prompt assignments for model config {model_config_id}")
            return kb_prompts

        except Exception as e:
            logger.error(f"Failed to get KB prompts: {e}", exc_info=True)
            raise ShuException(f"Failed to get KB prompts: {str(e)}", "GET_KB_PROMPTS_ERROR")

    async def _get_and_validate_llm_provider(self, llm_provider_id: str) -> LLMProvider:
        provider_result = await self.db.execute(
            select(LLMProvider).where(
                and_(
                    LLMProvider.id == llm_provider_id,
                    LLMProvider.is_active == True
                )
            )
            .options(selectinload(LLMProvider.provider_definition))
        )
        provider = provider_result.scalar_one_or_none()
        if not provider:
            raise ShuException(
                f"LLM provider {llm_provider_id} not found or inactive",
                "PROVIDER_NOT_FOUND"
            )
        return provider

    async def _create_model_configuration_parameter_overrides(self, config_data: ModelConfigurationCreate, provider: LLMProvider | None) -> Dict[str, Any]:
        raw_overrides = getattr(config_data, "parameter_overrides", None) or {}
        try:
            adapter = get_adapter_from_provider(self.db, provider)
            return build_provider_params(
                serialize_parameter_mapping(adapter.get_parameter_mapping()) or {},
                raw_overrides,
                None,
            )
        except ValidationError as ve:
            raise ShuException(str(ve), "PARAM_VALIDATION_ERROR")

    async def validate_model_configuration_for_use(
        self,
        model_configuration_id: str,
        current_user: Optional['User'] = None,
        include_relationships: bool = True
    ) -> ModelConfiguration:
        """
        Validate that model configuration exists, is active, and user has access.
        
        This is a shared validation method used by both ExperienceService and ExperienceExecutor
        to ensure consistent validation logic across the codebase.
        
        Args:
            model_configuration_id: Model configuration ID to validate
            current_user: Current user for access validation
            include_relationships: Whether to load provider relationships for validation
            
        Returns:
            ModelConfiguration: Loaded and validated model configuration
            
        Raises:
            ModelConfigurationNotFoundError: If configuration is not found
            ModelConfigurationInactiveError: If configuration is inactive
            ModelConfigurationProviderInactiveError: If underlying provider is inactive
        """
        from ..core.exceptions import (
            ModelConfigurationNotFoundError,
            ModelConfigurationInactiveError,
            ModelConfigurationProviderInactiveError
        )
        
        # Load model configuration with optional relationships
        model_config = await self.get_model_configuration(
            model_configuration_id,
            include_relationships=include_relationships,
            current_user=current_user
        )
        
        if not model_config:
            details = {"user_id": str(current_user.id)} if current_user else {}
            raise ModelConfigurationNotFoundError(
                config_id=model_configuration_id,
                details=details
            )
        
        if not model_config.is_active:
            details = {
                "description": model_config.description
            }
            if current_user:
                details["user_id"] = str(current_user.id)
                
            raise ModelConfigurationInactiveError(
                config_name=model_config.name,
                config_id=model_config.id,
                details=details
            )
        
        # Validate that the underlying provider is active (only if relationships are loaded)
        if include_relationships and (not model_config.llm_provider or not model_config.llm_provider.is_active):
            provider_name = model_config.llm_provider.name if model_config.llm_provider else "Unknown"
            details = {
                "config_id": model_config.id,
                "provider_id": model_config.llm_provider_id
            }
            if current_user:
                details["user_id"] = str(current_user.id)
                
            raise ModelConfigurationProviderInactiveError(
                config_name=model_config.name,
                provider_name=provider_name,
                details=details
            )
        
        logger.debug(
            "Successfully validated model configuration | config=%s provider=%s model=%s user=%s",
            model_config.name,
            model_config.llm_provider.name if model_config.llm_provider else "Unknown",
            model_config.model_name,
            current_user.email if current_user else "None",
        )
        
        return model_config

    async def _update_model_configuration_parameter_overrides(self, update_dict: Dict[str, Any], provider: LLMProvider | None) -> Dict[str, Any]:
        # Handle parameter_overrides validation/update
        if 'parameter_overrides' in update_dict:
            overrides_candidate = update_dict.pop('parameter_overrides')
            if overrides_candidate is not None:
                try:
                    adapter = get_adapter_from_provider(self.db, provider)
                    return build_provider_params(
                        serialize_parameter_mapping(adapter.get_parameter_mapping()) or {},
                        overrides_candidate or {},
                        None,
                    )
                except ValidationError as ve:
                    raise ShuException(str(ve), "PARAM_VALIDATION_ERROR")
