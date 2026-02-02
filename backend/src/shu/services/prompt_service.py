"""Generalized Prompt Service for Shu.

This service provides comprehensive prompt management functionality
for the unified prompt system supporting multiple entity types.
"""

import logging
import uuid

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..core.exceptions import ConflictError, NotFoundError, ShuException, ValidationError
from ..models.model_configuration_kb_prompt import ModelConfigurationKBPrompt
from ..models.prompt import EntityType, Prompt, PromptAssignment
from ..schemas.prompt import (
    PromptAssignmentCreate,
    PromptAssignmentResponse,
    PromptCreate,
    PromptListResponse,
    PromptQueryParams,
    PromptResponse,
    PromptSystemStats,
    PromptUpdate,
)
from ..utils.prompt_utils import (
    get_citation_conflict_info,
    get_effective_reference_setting,
    has_citation_instructions,
)

logger = logging.getLogger(__name__)


class PromptNotFoundError(NotFoundError):
    """Raised when a prompt is not found."""

    pass


class PromptAssignmentNotFoundError(NotFoundError):
    """Raised when a prompt assignment is not found."""

    pass


class PromptAlreadyExistsError(ConflictError):
    """Raised when trying to create a prompt that already exists."""

    pass


class PromptService:
    """Service for managing prompts and their assignments."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_prompt(self, prompt_data: PromptCreate) -> PromptResponse:
        """Create a new prompt.

        Args:
            prompt_data: Prompt creation data

        Returns:
            Created prompt

        Raises:
            PromptAlreadyExistsError: If prompt with same name and entity type exists
            ValidationError: If entity type is invalid

        """
        # Validate entity type
        if not EntityType.validate(prompt_data.entity_type.value):
            raise ValidationError(f"Invalid entity type: {prompt_data.entity_type.value}")

        # Check for existing prompt with same name and entity type
        existing = await self._get_prompt_by_name_and_type(prompt_data.name, prompt_data.entity_type.value)
        if existing:
            raise PromptAlreadyExistsError(
                f"Prompt '{prompt_data.name}' already exists for entity type '{prompt_data.entity_type.value}'"
            )

        # Create the prompt
        prompt = Prompt(
            id=str(uuid.uuid4()),
            name=prompt_data.name,
            description=prompt_data.description,
            content=prompt_data.content,
            entity_type=prompt_data.entity_type.value,
            is_active=prompt_data.is_active,
        )

        self.db.add(prompt)
        await self.db.commit()
        await self.db.refresh(prompt, ["assignments"])

        logger.info(f"Created prompt '{prompt.name}' for entity type '{prompt.entity_type}'")
        return PromptResponse.from_orm(prompt)

    async def get_prompt(self, prompt_id: str) -> PromptResponse | None:
        """Get a prompt by ID.

        Args:
            prompt_id: Prompt ID

        Returns:
            Prompt if found, None otherwise

        """
        stmt = select(Prompt).options(selectinload(Prompt.assignments)).where(Prompt.id == prompt_id)
        result = await self.db.execute(stmt)
        prompt = result.scalar_one_or_none()

        if not prompt:
            return None

        return PromptResponse.from_orm(prompt)

    async def update_prompt(self, prompt_id: str, prompt_data: PromptUpdate) -> PromptResponse:
        """Update an existing prompt.

        Args:
            prompt_id: Prompt ID
            prompt_data: Update data

        Returns:
            Updated prompt

        Raises:
            PromptNotFoundError: If prompt not found
            PromptAlreadyExistsError: If name conflicts with existing prompt

        """
        prompt = await self._get_prompt_by_id(prompt_id)
        if not prompt:
            raise PromptNotFoundError(f"Prompt {prompt_id} not found")

        # Check for name conflicts if name is being updated
        if prompt_data.name and prompt_data.name != prompt.name:
            existing = await self._get_prompt_by_name_and_type(prompt_data.name, prompt.entity_type)
            if existing and existing.id != prompt_id:
                raise PromptAlreadyExistsError(
                    f"Prompt '{prompt_data.name}' already exists for entity type '{prompt.entity_type}'"
                )

        # Update fields
        update_data = prompt_data.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(prompt, field, value)

        # Increment version if content changed
        if prompt_data.content:
            prompt.increment_version()

        await self.db.commit()
        await self.db.refresh(prompt, ["assignments"])

        logger.info(f"Updated prompt '{prompt.name}' (ID: {prompt_id})")
        return PromptResponse.from_orm(prompt)

    async def delete_prompt(self, prompt_id: str) -> bool:
        """Delete a prompt and all its assignments.

        Args:
            prompt_id: Prompt ID

        Returns:
            True if deleted, False if not found

        """
        prompt = await self._get_prompt_by_id(prompt_id)
        if not prompt:
            return False

        await self.db.delete(prompt)
        await self.db.commit()

        logger.info(f"Deleted prompt '{prompt.name}' (ID: {prompt_id})")
        return True

    async def list_prompts(self, params: PromptQueryParams) -> PromptListResponse:
        """List prompts with filtering and pagination.

        Args:
            params: Query parameters

        Returns:
            List of prompts with metadata

        """
        stmt = select(Prompt).options(selectinload(Prompt.assignments))

        # Apply filters
        if params.entity_type:
            stmt = stmt.where(Prompt.entity_type == params.entity_type.value)

        if params.is_active is not None:
            stmt = stmt.where(Prompt.is_active == params.is_active)

        if params.search:
            search_term = f"%{params.search}%"
            stmt = stmt.where(or_(Prompt.name.ilike(search_term), Prompt.description.ilike(search_term)))

        if params.entity_id:
            # Filter by entity assignment
            stmt = stmt.join(PromptAssignment).where(
                and_(
                    PromptAssignment.entity_id == params.entity_id,
                    PromptAssignment.is_active,
                )
            )

        # Get total count
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_result = await self.db.execute(count_stmt)
        total = total_result.scalar()

        # Apply pagination
        stmt = stmt.offset(params.offset).limit(params.limit)
        stmt = stmt.order_by(Prompt.name)

        result = await self.db.execute(stmt)
        prompts = result.scalars().all()

        prompt_responses = [PromptResponse.from_orm(prompt) for prompt in prompts]

        return PromptListResponse(items=prompt_responses, total=total, entity_type=params.entity_type)

    async def assign_prompt(self, prompt_id: str, assignment_data: PromptAssignmentCreate) -> PromptAssignmentResponse:
        """Assign a prompt to an entity.

        Args:
            prompt_id: Prompt ID
            assignment_data: Assignment data

        Returns:
            Created assignment

        Raises:
            PromptNotFoundError: If prompt not found
            ConflictError: If assignment already exists
            ValidationError: If assignment is invalid

        """
        # Verify prompt exists
        prompt = await self._get_prompt_by_id(prompt_id)
        if not prompt:
            raise PromptNotFoundError(f"Prompt {prompt_id} not found")

        # Validate entity type compatibility
        if prompt.entity_type != assignment_data.entity_type.value:
            raise ValidationError(
                f"Cannot assign prompt of type '{prompt.entity_type}' to entity of type '{assignment_data.entity_type.value}'"
            )

        # Block direct assignment to knowledge bases (they should use model configurations)
        if assignment_data.entity_type.value == EntityType.KNOWLEDGE_BASE:
            raise ValidationError(
                "Direct assignment to knowledge bases is not supported. "
                "Use model configuration KB prompt assignments instead."
            )

        # Check for existing assignment
        existing = await self._get_assignment(prompt_id, assignment_data.entity_id)
        if existing:
            raise ConflictError(f"Prompt already assigned to entity {assignment_data.entity_id}")

        # Create assignment
        assignment = PromptAssignment(
            id=str(uuid.uuid4()),
            prompt_id=prompt_id,
            entity_id=assignment_data.entity_id,
            is_active=assignment_data.is_active,
        )

        self.db.add(assignment)
        await self.db.commit()
        await self.db.refresh(assignment)

        logger.info(f"Assigned prompt {prompt_id} to entity {assignment_data.entity_id}")
        return PromptAssignmentResponse.from_orm(assignment)

    async def unassign_prompt(self, prompt_id: str, entity_id: str) -> bool:
        """Remove a prompt assignment.

        Args:
            prompt_id: Prompt ID
            entity_id: Entity ID

        Returns:
            True if unassigned, False if assignment not found

        """
        assignment = await self._get_assignment(prompt_id, entity_id)
        if not assignment:
            return False

        await self.db.delete(assignment)
        await self.db.commit()

        logger.info(f"Unassigned prompt {prompt_id} from entity {entity_id}")
        return True

    async def get_entity_prompts(
        self, entity_id: str, entity_type: str, active_only: bool = True
    ) -> list[PromptResponse]:
        """Get all prompts assigned to a specific entity.

        Args:
            entity_id: Entity ID
            entity_type: Entity type
            active_only: Whether to return only active prompts

        Returns:
            List of assigned prompts

        """
        stmt = (
            select(Prompt)
            .options(selectinload(Prompt.assignments))
            .join(PromptAssignment)
            .where(and_(Prompt.entity_type == entity_type, PromptAssignment.entity_id == entity_id))
        )

        if active_only:
            stmt = stmt.where(and_(Prompt.is_active, PromptAssignment.is_active))

        result = await self.db.execute(stmt)
        prompts = result.scalars().all()

        return [PromptResponse.from_orm(prompt) for prompt in prompts]

    async def get_system_stats(self) -> PromptSystemStats:
        """Get system-wide prompt statistics.

        Returns:
            System statistics

        """
        # Total prompts
        total_prompts_result = await self.db.execute(select(func.count(Prompt.id)))
        total_prompts = total_prompts_result.scalar()

        # Active prompts
        active_prompts_result = await self.db.execute(select(func.count(Prompt.id)).where(Prompt.is_active))
        active_prompts = active_prompts_result.scalar()

        # Total assignments
        total_assignments_result = await self.db.execute(select(func.count(PromptAssignment.id)))
        total_assignments = total_assignments_result.scalar()

        # Active assignments
        active_assignments_result = await self.db.execute(
            select(func.count(PromptAssignment.id)).where(PromptAssignment.is_active)
        )
        active_assignments = active_assignments_result.scalar()

        # Prompts by entity type
        prompts_by_type_result = await self.db.execute(
            select(Prompt.entity_type, func.count(Prompt.id)).group_by(Prompt.entity_type)
        )
        prompts_by_entity_type = dict(prompts_by_type_result.fetchall())

        # Assignments by entity type
        assignments_by_type_result = await self.db.execute(
            select(Prompt.entity_type, func.count(PromptAssignment.id))
            .join(PromptAssignment)
            .group_by(Prompt.entity_type)
        )
        assignments_by_entity_type = dict(assignments_by_type_result.fetchall())

        return PromptSystemStats(
            total_prompts=total_prompts,
            active_prompts=active_prompts,
            total_assignments=total_assignments,
            active_assignments=active_assignments,
            prompts_by_entity_type=prompts_by_entity_type,
            assignments_by_entity_type=assignments_by_entity_type,
        )

    # Private helper methods
    async def _get_prompt_by_id(self, prompt_id: str) -> Prompt | None:
        """Get prompt by ID."""
        stmt = select(Prompt).where(Prompt.id == prompt_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_prompt_by_name_and_type(self, name: str, entity_type: str) -> Prompt | None:
        """Get prompt by name and entity type."""
        stmt = select(Prompt).where(and_(Prompt.name == name, Prompt.entity_type == entity_type))
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_assignment(self, prompt_id: str, entity_id: str) -> PromptAssignment | None:
        """Get assignment by prompt and entity ID."""
        stmt = select(PromptAssignment).where(
            and_(PromptAssignment.prompt_id == prompt_id, PromptAssignment.entity_id == entity_id)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_kb_prompts_with_reference_info(
        self, entity_id: str, kb_include_references: bool = True
    ) -> list[dict]:
        """Get knowledge base prompts with citation conflict analysis.

        Args:
            entity_id: Knowledge base ID
            kb_include_references: Whether KB has reference inclusion enabled

        Returns:
            List of prompt data with citation analysis

        """
        # Get regular KB prompts
        prompts = await self.get_entity_prompts(entity_id, EntityType.KNOWLEDGE_BASE)

        # Analyze each prompt for citation handling
        analyzed_prompts = []
        for prompt in prompts:
            citation_info = get_citation_conflict_info(prompt.content, kb_include_references)
            effective_references, reason = get_effective_reference_setting(kb_include_references, prompt.content)

            analyzed_prompts.append(
                {
                    "prompt": prompt,
                    "citation_info": citation_info,
                    "effective_references": effective_references,
                    "reference_reason": reason,
                    "prompt_handles_citations": has_citation_instructions(prompt.content),
                }
            )

        return analyzed_prompts

    def analyze_citation_handling(self, prompt_content: str, kb_include_references: bool) -> dict:
        """Analyze how citations should be handled for a prompt and KB combination.

        Args:
            prompt_content: The prompt content to analyze
            kb_include_references: Whether KB has references enabled

        Returns:
            Dictionary with citation analysis and recommendations

        """
        citation_info = get_citation_conflict_info(prompt_content, kb_include_references)
        effective_references, reason = get_effective_reference_setting(kb_include_references, prompt_content)

        return {
            "citation_info": citation_info,
            "effective_references": effective_references,
            "reference_reason": reason,
            "prompt_handles_citations": has_citation_instructions(prompt_content),
            "kb_configured_references": kb_include_references,
        }

    # Model Configuration KB Prompt Methods

    async def get_model_config_kb_prompts(self, model_config_id: str) -> dict[str, PromptResponse]:
        """Get all KB-specific prompts for a model configuration.

        Args:
            model_config_id: Model configuration ID

        Returns:
            Dictionary mapping knowledge_base_id to PromptResponse

        """
        try:
            # Get all active KB prompt assignments for the model configuration
            stmt = (
                select(ModelConfigurationKBPrompt)
                .options(
                    selectinload(ModelConfigurationKBPrompt.prompt),
                    selectinload(ModelConfigurationKBPrompt.knowledge_base),
                )
                .where(
                    and_(
                        ModelConfigurationKBPrompt.model_configuration_id == model_config_id,
                        ModelConfigurationKBPrompt.is_active,
                    )
                )
            )

            result = await self.db.execute(stmt)
            assignments = result.scalars().all()

            # Build response dictionary
            kb_prompts = {}
            for assignment in assignments:
                if assignment.prompt:  # Ensure prompt relationship is loaded
                    kb_prompts[assignment.knowledge_base_id] = PromptResponse.from_orm(assignment.prompt)

            logger.debug(f"Retrieved {len(kb_prompts)} KB prompts for model config {model_config_id}")
            return kb_prompts

        except Exception as e:
            logger.error(f"Failed to get model config KB prompts: {e}", exc_info=True)
            raise ShuException(f"Failed to get model config KB prompts: {e!s}", "GET_MODEL_CONFIG_KB_PROMPTS_ERROR")

    async def assign_prompt_to_model_config_kb(
        self, model_config_id: str, knowledge_base_id: str, prompt_id: str
    ) -> bool:
        """Assign a prompt to a specific KB for a model configuration.

        This is a convenience method that delegates to ModelConfigurationService
        but provides a consistent interface from the PromptService.

        Args:
            model_config_id: Model configuration ID
            knowledge_base_id: Knowledge base ID
            prompt_id: Prompt ID

        Returns:
            True if assignment was successful

        Raises:
            ShuException: If assignment fails

        """
        try:
            # Import here to avoid circular imports
            from .model_configuration_service import ModelConfigurationService

            model_config_service = ModelConfigurationService(self.db)
            assignment = await model_config_service.assign_kb_prompt(
                model_config_id=model_config_id,
                knowledge_base_id=knowledge_base_id,
                prompt_id=prompt_id,
            )

            logger.info(f"Assigned prompt {prompt_id} to KB {knowledge_base_id} for model config {model_config_id}")
            return assignment is not None

        except Exception as e:
            logger.error(f"Failed to assign prompt to model config KB: {e}", exc_info=True)
            raise ShuException(
                f"Failed to assign prompt to model config KB: {e!s}",
                "ASSIGN_MODEL_CONFIG_KB_PROMPT_ERROR",
            )

    async def remove_prompt_from_model_config_kb(self, model_config_id: str, knowledge_base_id: str) -> bool:
        """Remove a prompt assignment from a specific KB for a model configuration.

        This is a convenience method that delegates to ModelConfigurationService
        but provides a consistent interface from the PromptService.

        Args:
            model_config_id: Model configuration ID
            knowledge_base_id: Knowledge base ID

        Returns:
            True if assignment was removed, False if it didn't exist

        Raises:
            ShuException: If removal fails

        """
        try:
            # Import here to avoid circular imports
            from .model_configuration_service import ModelConfigurationService

            model_config_service = ModelConfigurationService(self.db)
            removed = await model_config_service.remove_kb_prompt(
                model_config_id=model_config_id, knowledge_base_id=knowledge_base_id
            )

            if removed:
                logger.info(f"Removed prompt assignment from KB {knowledge_base_id} for model config {model_config_id}")
            else:
                logger.debug(f"No prompt assignment found for KB {knowledge_base_id} in model config {model_config_id}")

            return removed

        except Exception as e:
            logger.error(f"Failed to remove prompt from model config KB: {e}", exc_info=True)
            raise ShuException(
                f"Failed to remove prompt from model config KB: {e!s}",
                "REMOVE_MODEL_CONFIG_KB_PROMPT_ERROR",
            )
