"""
Experience Service for Shu.

This service provides business logic for managing experiences,
including CRUD operations, template validation, and required scopes computation.
"""

from typing import List, Optional, Dict, Any, Tuple, TypeVar, Callable
from datetime import datetime
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, inspect as sa_inspect
from sqlalchemy.orm import selectinload


try:
    from croniter import croniter
except ImportError:
    croniter = None

import zoneinfo

from jinja2 import BaseLoader, TemplateSyntaxError, UndefinedError
from jinja2.sandbox import SandboxedEnvironment

from ..models.experience import Experience, ExperienceStep, ExperienceRun
from ..models.user_preferences import UserPreferences
from ..schemas.experience import (
    ExperienceCreate, ExperienceUpdate, ExperienceResponse,
    ExperienceList, ExperienceStepCreate, ExperienceStepResponse,
    ExperienceRunResponse, ExperienceRunList,
    ExperienceVisibility, StepType, RunStatus, TriggerType,
    ExperienceResultSummary, UserExperienceResults
)
from ..core.exceptions import NotFoundError, ValidationError, ConflictError
from ..core.logging import get_logger

logger = get_logger(__name__)



class ExperienceService:
    """Service for managing experiences and their execution."""

    def __init__(self, db: AsyncSession):
        self.db = db
        # Create sandboxed Jinja2 environment for template validation
        self._jinja_env = SandboxedEnvironment(
            loader=BaseLoader(),
            autoescape=False,
        )
        # Lazily-initialized plugin loader (created on first use)
        self._plugin_loader: Optional['PluginLoader'] = None

    def _validate_trigger_config(
        self,
        trigger_type: TriggerType,
        trigger_config: Optional[Dict[str, Any]]
    ) -> None:
        """
        Validate trigger configuration for scheduled/cron types.
        
        Args:
            trigger_type: Type of trigger (scheduled, cron, manual)
            trigger_config: Dictionary of trigger settings
            
        Raises:
            ValidationError: If configuration is invalid
        """
        if trigger_type == TriggerType.SCHEDULED:
            if not trigger_config or not trigger_config.get("scheduled_at"):
                raise ValidationError("Trigger type 'scheduled' requires 'scheduled_at' in trigger_config")
            try:
                datetime.fromisoformat(trigger_config["scheduled_at"])
            except ValueError:
                raise ValidationError("Invalid 'scheduled_at' format. Expected ISO 8601 (YYYY-MM-DDTHH:MM:SS)")
        
        elif trigger_type == TriggerType.CRON:
            if not trigger_config or not trigger_config.get("cron"):
                raise ValidationError("Trigger type 'cron' requires 'cron' expression in trigger_config")
            
            if croniter:
                try:
                    if not croniter.is_valid(trigger_config["cron"]):
                        raise ValidationError(f"Invalid cron expression: {trigger_config['cron']}")
                except Exception as e:
                    # croniter.is_valid might raise or return False depending on version/input
                    raise ValidationError(f"Invalid cron expression: {trigger_config['cron']}")
            else:
                logger.warning("croniter not installed, skipping strict cron validation")

        # Validate timezone if provided (for both scheduled and cron)
        if trigger_config and trigger_config.get("timezone"):
            try:
                zoneinfo.ZoneInfo(trigger_config["timezone"])
            except Exception:
                raise ValidationError(f"Invalid timezone: {trigger_config['timezone']}")

    # =========================================================================
    # CRUD Operations
    # =========================================================================

    async def create_experience(
        self,
        experience_data: ExperienceCreate,
        created_by: str
    ) -> ExperienceResponse:
        """
        Create a new experience with steps.

        Args:
            experience_data: Experience creation data including steps
            created_by: User ID of the creator (from authenticated user)

        Returns:
            Created experience

        Raises:
            ExperienceValidationError: If validation fails
            ConflictError: If experience with same name already exists
        """
        # Check for existing experience with same name
        existing = await self._get_experience_by_name(experience_data.name)
        if existing:
            raise ConflictError(f"Experience '{experience_data.name}' already exists")

        # Validate trigger config
        self._validate_trigger_config(experience_data.trigger_type, experience_data.trigger_config)

        # Validate templates before creating
        if experience_data.inline_prompt_template:
            self._validate_template_syntax(
                experience_data.inline_prompt_template,
                "inline_prompt_template"
            )

        # Validate step configurations
        await self._validate_steps(experience_data.steps)

        # Create the experience
        experience = Experience(
            id=str(uuid.uuid4()),
            name=experience_data.name,
            description=experience_data.description,
            created_by=created_by,
            visibility=experience_data.visibility.value,
            trigger_type=experience_data.trigger_type.value,
            trigger_config=experience_data.trigger_config,
            include_previous_run=experience_data.include_previous_run,
            llm_provider_id=experience_data.llm_provider_id,
            model_name=experience_data.model_name,
            prompt_id=experience_data.prompt_id,
            inline_prompt_template=experience_data.inline_prompt_template,
            max_run_seconds=experience_data.max_run_seconds,
            token_budget=experience_data.token_budget,
            version=1,
            is_active_version=True,
        )

        self.db.add(experience)

        # Create steps
        for step_data in experience_data.steps:
            step = await self._create_step(experience.id, step_data)
            self.db.add(step)

        await self.db.commit()
        await self.db.refresh(experience, ['steps', 'llm_provider', 'prompt'])

        logger.info(f"Created experience '{experience.name}' with {len(experience_data.steps)} steps")
        return self._experience_to_response(experience)

    async def get_experience(
        self,
        experience_id: str,
        user_id: Optional[str] = None,
        is_admin: bool = False
    ) -> Optional[ExperienceResponse]:
        """
        Get an experience by ID with visibility check.

        Args:
            experience_id: Experience ID
            user_id: Current user ID (for visibility check)
            is_admin: Whether current user is admin

        Returns:
            Experience if found and visible, None otherwise
        """
        stmt = self._base_experience_query(include_prompt=True).where(
            Experience.id == experience_id
        )
        result = await self.db.execute(stmt)
        experience = result.scalar_one_or_none()

        if not experience:
            return None

        # Visibility check
        if not self._check_visibility(experience, user_id, is_admin):
            return None

        return self._experience_to_response(experience)

    async def update_experience(
        self,
        experience_id: str,
        update_data: ExperienceUpdate
    ) -> ExperienceResponse:
        """
        Update an existing experience.

        Args:
            experience_id: Experience ID
            update_data: Update data

        Returns:
            Updated experience

        Raises:
            NotFoundError: If experience not found
            ConflictError: If name conflicts with existing experience
            ValidationError: If validation fails
        """
        experience = await self._get_experience_by_id(experience_id)
        if not experience:
            raise NotFoundError(f"Experience {experience_id} not found")

        # Check for name conflicts if name is being updated
        if update_data.name and update_data.name != experience.name:
            existing = await self._get_experience_by_name(update_data.name)
            if existing and existing.id != experience_id:
                raise ConflictError(f"Experience '{update_data.name}' already exists")

        # Validate templates if being updated
        if update_data.inline_prompt_template:
            self._validate_template_syntax(
                update_data.inline_prompt_template,
                "inline_prompt_template"
            )

        # Update scalar fields
        update_dict = update_data.model_dump(exclude_unset=True, exclude={'steps'})
        trigger_changed = False
        for field, value in update_dict.items():
            if field == 'visibility' and value:
                setattr(experience, field, value.value if hasattr(value, 'value') else value)
            elif field == 'trigger_type' and value:
                setattr(experience, field, value.value if hasattr(value, 'value') else value)
                trigger_changed = True
            elif field == 'trigger_config':
                setattr(experience, field, value)
                trigger_changed = True
            else:
                setattr(experience, field, value)

        # Validate trigger config if changed
        if trigger_changed:
            # Ensure the configuration is valid for the (potentially new) type
            try:
                # Trigger type in DB is string, convert to Enum for consistency if needed, 
                # but our validator handles the logic based on values.
                current_type = TriggerType(experience.trigger_type)
                self._validate_trigger_config(current_type, experience.trigger_config)
            except ValueError:
                logger.exception("Incorrect validation for experience %s", experience.id)
                # Fallback if DB has invalid string (unlikely)
                pass

        # Recalculate next_run_at if trigger configuration changed
        if trigger_changed:
            # Get user's timezone preference for scheduling
            user_tz = None
            try:
                prefs_result = await self.db.execute(
                    select(UserPreferences).where(UserPreferences.user_id == experience.created_by)
                )
                prefs = prefs_result.scalar_one_or_none()
                if prefs:
                    user_tz = prefs.timezone
            except Exception:
                logger.exception("Failed to load user preferences for scheduling experience %s", experience.id)
                # Fallback to None (which falls back to UTC in schedule_next)
                pass
            experience.schedule_next(user_timezone=user_tz)

        # Replace steps if provided
        if update_data.steps is not None:
            # Validate new steps
            await self._validate_steps(update_data.steps)

            # Delete existing steps
            for step in experience.steps:
                await self.db.delete(step)
            
            # Flush deletes before inserting to avoid unique constraint violations
            await self.db.flush()

            # Create new steps
            for step_data in update_data.steps:
                step = await self._create_step(experience.id, step_data)
                self.db.add(step)

        await self.db.commit()
        await self.db.refresh(experience, ['steps', 'llm_provider', 'prompt'])

        logger.info(f"Updated experience '{experience.name}' (ID: {experience_id})")
        return self._experience_to_response(experience)

    async def delete_experience(self, experience_id: str) -> bool:
        """
        Delete an experience and all its steps and runs.

        Args:
            experience_id: Experience ID

        Returns:
            True if deleted, False if not found
        """
        experience = await self._get_experience_by_id(experience_id)
        if not experience:
            return False

        await self.db.delete(experience)
        await self.db.commit()

        logger.info(f"Deleted experience '{experience.name}' (ID: {experience_id})")
        return True

    async def list_experiences(
        self,
        user_id: Optional[str] = None,
        is_admin: bool = False,
        visibility_filter: Optional[ExperienceVisibility] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> ExperienceList:
        """
        List experiences with visibility filtering and pagination.

        Args:
            user_id: Current user ID
            is_admin: Whether current user is admin
            visibility_filter: Optional visibility filter
            search: Optional search term
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            Paginated list of experiences
        """
        # Include prompt to avoid lazy loading in _experience_to_response
        stmt = self._base_experience_query(include_prompt=True)

        # Build visibility conditions
        if is_admin:
            # Admins see all experiences
            if visibility_filter:
                stmt = stmt.where(Experience.visibility == visibility_filter.value)
        else:
            # Non-admins only see published experiences
            # (drafts and admin_only are visible only to admins who create/manage them)
            stmt = stmt.where(Experience.visibility == ExperienceVisibility.PUBLISHED.value)

        # Apply search filter
        if search:
            search_term = f"%{search}%"
            stmt = stmt.where(
                or_(
                    Experience.name.ilike(search_term),
                    Experience.description.ilike(search_term)
                )
            )

        # Execute with pagination
        total, experiences = await self._execute_paginated_query(
            stmt,
            order_by=Experience.name,
            offset=offset,
            limit=limit
        )

        items = [self._experience_to_response(exp) for exp in experiences]
        return self._build_paginated_response(
            ExperienceList, items, total, offset, limit
        )

    # =========================================================================
    # Template Validation
    # =========================================================================

    def _validate_template_syntax(self, template: str, field_name: str) -> None:
        """
        Validate Jinja2 template syntax.

        Args:
            template: Template string to validate
            field_name: Field name for error messages

        Raises:
            ValidationError: If template syntax is invalid
        """
        try:
            self._jinja_env.parse(template)
        except TemplateSyntaxError as e:
            raise ValidationError(
                f"Invalid Jinja2 template in {field_name}: {e.message} (line {e.lineno})"
            )

    def validate_template_with_context(
        self,
        template: str,
        mock_context: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate a Jinja2 template with a mock context (dry-run).

        Args:
            template: Template string to validate
            mock_context: Optional mock context for rendering

        Returns:
            Tuple of (success, error_message)
        """
        if mock_context is None:
            mock_context = self._build_validation_context()

        try:
            compiled = self._jinja_env.from_string(template)
            compiled.render(mock_context)
            return (True, None)
        except TemplateSyntaxError as e:
            return (False, f"Template syntax error: {e.message} (line {e.lineno})")
        except UndefinedError as e:
            return (False, f"Template variable error: {str(e)}")
        except Exception as e:
            return (False, f"Template error: {str(e)}")

    def _build_validation_context(self) -> Dict[str, Any]:
        """
        Build a sample context for dry-run template validation.

        This context simulates the runtime template context shape, allowing
        the service to validate Jinja2 templates without executing an experience.
        """
        return {
            "user": {
                "id": "mock-user-id",
                "email": "user@example.com",
                "display_name": "Mock User"
            },
            "input": {},
            "steps": {},
            "previous_run": None,
            "now": datetime.now()
        }

    # =========================================================================
    # Required Scopes Computation
    # =========================================================================

    async def compute_required_scopes_for_step(
        self,
        plugin_name: str,
        plugin_op: Optional[str] = None
    ) -> List[str]:
        """
        Compute required identity scopes for a plugin step from the plugin manifest.

        Args:
            plugin_name: Name of the plugin
            plugin_op: Optional operation name

        Returns:
            List of required scopes
        """
        scopes: List[str] = []

        try:
            records = self._get_plugin_loader().discover()

            if plugin_name not in records:
                logger.warning(f"Plugin '{plugin_name}' not found in registry")
                return scopes

            record = records[plugin_name]

            # Check op_auth for operation-specific scopes
            if plugin_op is not None and record.op_auth:
                op_key = plugin_op.lower()
                op_spec = record.op_auth.get(op_key) or {}
                op_scopes = op_spec.get("scopes")
                if isinstance(op_scopes, list):
                    for s in op_scopes:
                        if isinstance(s, str) and s.strip() and s.strip() not in scopes:
                            scopes.append(s.strip())

            # Also check required_identities in manifest for global scopes
            # This would be accessible through the full manifest, but PluginRecord
            # doesn't expose it directly. For now, op_auth scopes are sufficient.

        except Exception as e:
            logger.warning(f"Failed to compute scopes for plugin '{plugin_name}': {e}")

        return scopes

    async def compute_all_required_scopes(
        self,
        experience_id: str
    ) -> Dict[str, List[str]]:
        """
        Compute required scopes for all steps in an experience.

        Args:
            experience_id: Experience ID

        Returns:
            Dict mapping step_key to list of required scopes
        """
        experience = await self._get_experience_by_id(experience_id)
        if not experience:
            return {}

        scopes_by_step: Dict[str, List[str]] = {}

        for step in experience.steps:
            if step.step_type == StepType.PLUGIN.value and step.plugin_name:
                scopes = await self.compute_required_scopes_for_step(
                    step.plugin_name,
                    step.plugin_op
                )
                scopes_by_step[step.step_key] = scopes

        return scopes_by_step

    # =========================================================================
    # Step Validation
    # =========================================================================

    async def _validate_steps(self, steps: List[ExperienceStepCreate]) -> None:
        """
        Validate experience steps configuration.

        Args:
            steps: List of steps to validate

        Raises:
            ValidationError: If validation fails
        """
        step_keys = set()

        for i, step in enumerate(steps):
            # Check for duplicate step keys
            if step.step_key in step_keys:
                raise ValidationError(
                    f"Duplicate step key '{step.step_key}' at position {i}"
                )
            step_keys.add(step.step_key)

            # Validate step type-specific fields
            if step.step_type == StepType.PLUGIN:
                if not step.plugin_name:
                    raise ValidationError(
                        f"Step '{step.step_key}' requires plugin_name for plugin type"
                    )
                if not step.plugin_op:
                    raise ValidationError(
                        f"Step '{step.step_key}' requires plugin_op for plugin type"
                    )
            elif step.step_type == StepType.KNOWLEDGE_BASE:
                if not step.knowledge_base_id:
                    raise ValidationError(
                        f"Step '{step.step_key}' requires knowledge_base_id for knowledge_base type"
                    )

            # Validate templates in params_template
            if step.params_template:
                for key, value in step.params_template.items():
                    if isinstance(value, str) and "{{" in value:
                        self._validate_template_syntax(value, f"step '{step.step_key}' param '{key}'")

            # Validate condition template
            # TODO: Future enhancement - support Jinja2 expressions in condition_template
            # Currently it is treated as a simple step key string for dependency
            # if step.condition_template:
            #     self._validate_template_syntax(
            #         step.condition_template,
            #         f"step '{step.step_key}' condition_template"
            #     )

            # Validate KB query template
            if step.kb_query_template:
                self._validate_template_syntax(
                    step.kb_query_template,
                    f"step '{step.step_key}' kb_query_template"
                )

    async def _create_step(
        self,
        experience_id: str,
        step_data: ExperienceStepCreate
    ) -> ExperienceStep:
        """
        Create an ExperienceStep from step data.

        Args:
            experience_id: Parent experience ID
            step_data: Step creation data

        Returns:
            Created ExperienceStep (not yet committed)
        """
        # Compute required scopes for plugin steps
        required_scopes = None
        if step_data.step_type == StepType.PLUGIN and step_data.plugin_name:
            required_scopes = await self.compute_required_scopes_for_step(
                step_data.plugin_name,
                step_data.plugin_op
            )

        step = ExperienceStep(
            id=str(uuid.uuid4()),
            experience_id=experience_id,
            order=step_data.order,
            step_key=step_data.step_key,
            step_type=step_data.step_type.value,
            plugin_name=step_data.plugin_name,
            plugin_op=step_data.plugin_op,
            knowledge_base_id=step_data.knowledge_base_id,
            kb_query_template=step_data.kb_query_template,
            params_template=step_data.params_template,
            condition_template=step_data.condition_template,
            required_scopes=required_scopes,
        )

        return step

    # =========================================================================
    # Run History
    # =========================================================================

    async def list_runs(
        self,
        experience_id: str,
        user_id: Optional[str] = None,
        is_admin: bool = False,
        limit: int = 50,
        offset: int = 0
    ) -> ExperienceRunList:
        """
        List runs for an experience.

        Args:
            experience_id: Experience ID
            user_id: Filter by user ID (non-admins see only their own)
            is_admin: Whether current user is admin
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            Paginated list of runs
        """
        stmt = select(ExperienceRun).where(ExperienceRun.experience_id == experience_id)

        # Non-admins see only their own runs
        if not is_admin and user_id:
            stmt = stmt.where(ExperienceRun.user_id == user_id)

        # Execute with pagination
        total, runs = await self._execute_paginated_query(
            stmt,
            order_by=ExperienceRun.created_at.desc(),
            offset=offset,
            limit=limit
        )

        # Fetch user info for all runs
        user_ids = list(set(run.user_id for run in runs if run.user_id))
        users_by_id = await self._fetch_users_by_ids(user_ids)

        items = [self._run_to_response(run, users_by_id.get(run.user_id)) for run in runs]
        return self._build_paginated_response(
            ExperienceRunList, items, total, offset, limit
        )

    async def _fetch_users_by_ids(self, user_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """Fetch user info for a list of user IDs.
        
        Args:
            user_ids: List of user IDs to fetch
            
        Returns:
            Dict mapping user_id to user info dict with id, email, display_name
        """
        if not user_ids:
            return {}
        
        from ..auth.models import User as UserModel
        user_stmt = select(UserModel).where(UserModel.id.in_(user_ids))
        user_result = await self.db.execute(user_stmt)
        
        users_by_id: Dict[str, Dict[str, Any]] = {}
        for user in user_result.scalars().all():
            users_by_id[str(user.id)] = {
                "id": str(user.id),
                "email": user.email,
                "display_name": getattr(user, "display_name", None) or user.email
            }
        return users_by_id

    async def get_run(
        self,
        run_id: str,
        user_id: Optional[str] = None,
        is_admin: bool = False
    ) -> Optional[ExperienceRunResponse]:
        """
        Get a specific run by ID.

        Args:
            run_id: Run ID
            user_id: Current user ID (for ownership check)
            is_admin: Whether current user is admin

        Returns:
            Run if found and accessible, None otherwise
        """
        stmt = select(ExperienceRun).where(ExperienceRun.id == run_id)
        result = await self.db.execute(stmt)
        run = result.scalar_one_or_none()

        if not run:
            return None

        # Ownership check for non-admins
        if not is_admin and user_id and run.user_id != user_id:
            return None

        return self._run_to_response(run)

    async def get_user_results(
        self,
        user_id: str,
        offset: int = 0,
        limit: int = 50
    ) -> UserExperienceResults:
        """
        Get user's latest experience results for the dashboard.

        Args:
            user_id: User ID
            offset: Number of experiences to skip
            limit: Maximum number of experiences to return

        Returns:
            User's experience results summary
        """
        # First, count total matching experiences
        count_stmt = (
            select(func.count())
            .select_from(Experience)
            .where(Experience.visibility == ExperienceVisibility.PUBLISHED.value)
        )
        total_result = await self.db.execute(count_stmt)
        total = total_result.scalar() or 0

        # Then fetch paginated experiences
        exp_stmt = (
            self._base_experience_query()
            .where(Experience.visibility == ExperienceVisibility.PUBLISHED.value)
            .order_by(Experience.name)
            .offset(offset)
            .limit(limit)
        )
        exp_result = await self.db.execute(exp_stmt)
        experiences = exp_result.scalars().all()

        if not experiences:
            return UserExperienceResults(experiences=[], total=0)

        # Get the latest run for each experience for this user in a single query
        # using a window function to rank runs by created_at
        experience_ids = [exp.id for exp in experiences]
        latest_runs_stmt = (
                select(ExperienceRun)
                .where(
                    and_(
                        ExperienceRun.experience_id.in_(experience_ids),
                        ExperienceRun.user_id == user_id
                    )
                )
                .order_by(
                    ExperienceRun.experience_id,
                    ExperienceRun.created_at.desc()
                )
                .distinct(ExperienceRun.experience_id)
            )
        runs_result = await self.db.execute(latest_runs_stmt)
        latest_runs = runs_result.scalars().all()

        # Build a map of experience_id -> latest_run
        runs_by_experience: Dict[str, ExperienceRun] = {
            run.experience_id: run for run in latest_runs
        }

        summaries = []
        for exp in experiences:
            latest_run = runs_by_experience.get(exp.id)

            # Compute required identities and check if user can run
            can_run = True
            missing_identities: List[str] = []
            
            # TODO: Check user's connected ProviderIdentity records against
            # required_scopes from experience steps. This requires:
            # 1. Aggregating all required_scopes from exp.steps
            # 2. Querying ProviderIdentity for user_id
            # 3. Comparing scopes

            result_preview = None
            if latest_run and latest_run.result_content:
                # Return full result content (frontend handles display)
                result_preview = latest_run.result_content

            summary = ExperienceResultSummary(
                experience_id=exp.id,
                experience_name=exp.name,
                experience_description=exp.description,
                latest_run_id=latest_run.id if latest_run else None,
                latest_run_status=RunStatus(latest_run.status) if latest_run else None,
                latest_run_finished_at=latest_run.finished_at if latest_run else None,
                result_preview=result_preview,
                can_run=can_run,
                missing_identities=missing_identities
            )
            summaries.append(summary)

        return UserExperienceResults(
            experiences=summaries,
            total=total
        )

    # =========================================================================
    # Private Helpers
    # =========================================================================

    def _get_plugin_loader(self) -> 'PluginLoader':
        """Get or create the plugin loader instance (lazy initialization)."""
        if self._plugin_loader is None:
            from ..plugins.loader import PluginLoader
            self._plugin_loader = PluginLoader()
        return self._plugin_loader

    def _base_experience_query(
        self,
        include_prompt: bool = False,
        include_runs: bool = False
    ):
        """
        Build a base query for experiences with common eager loading.

        Args:
            include_prompt: Whether to load the prompt relationship
            include_runs: Whether to load the runs relationship

        Returns:
            SQLAlchemy select statement with configured options
        """
        options = [
            selectinload(Experience.steps),
            selectinload(Experience.llm_provider),
        ]
        if include_prompt:
            options.append(selectinload(Experience.prompt))
        if include_runs:
            options.append(selectinload(Experience.runs))

        return select(Experience).options(*options)

    async def _execute_paginated_query(
        self,
        stmt,
        order_by,
        offset: int,
        limit: int
    ) -> Tuple[int, List[Any]]:
        """
        Execute a query with pagination.

        Args:
            stmt: Base SQLAlchemy select statement
            order_by: Column or expression to order by
            offset: Number of rows to skip
            limit: Maximum number of rows to return

        Returns:
            Tuple of (total_count, list_of_items)
        """
        # Get total count
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_result = await self.db.execute(count_stmt)
        total = total_result.scalar() or 0

        # Apply ordering and pagination
        stmt = stmt.order_by(order_by).offset(offset).limit(limit)
        result = await self.db.execute(stmt)
        items = result.scalars().all()

        return total, list(items)

    def _build_paginated_response(
        self,
        response_class,
        items: List[Any],
        total: int,
        offset: int,
        limit: int
    ):
        """
        Build a paginated response object.

        Args:
            response_class: The Pydantic model class for the list response
            items: List of response items
            total: Total count of all items
            offset: Current offset
            limit: Items per page

        Returns:
            Instance of response_class with pagination metadata
        """
        pages = (total + limit - 1) // limit if limit > 0 else 1
        page = (offset // limit) + 1 if limit > 0 else 1

        return response_class(
            items=items,
            total=total,
            page=page,
            per_page=limit,
            pages=pages
        )

    async def _get_experience_by_id(self, experience_id: str) -> Optional[Experience]:
        """Get experience by ID."""
        stmt = self._base_experience_query().where(Experience.id == experience_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_experience_by_name(self, name: str) -> Optional[Experience]:
        """Get experience by name."""
        stmt = select(Experience).where(Experience.name == name)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    def _check_visibility(
        self,
        experience: Experience,
        user_id: Optional[str],
        is_admin: bool
    ) -> bool:
        """
        Check if user can see the experience based on visibility.
        
        Since only admins can create experiences, draft and admin_only
        experiences are only visible to admins.
        """
        if is_admin:
            return True
        # Non-admins only see published experiences
        return experience.visibility == ExperienceVisibility.PUBLISHED.value

    def _get_last_run_timestamp(self, experience: Experience) -> Optional[datetime]:
        """
        Get the last run timestamp for an experience.
        
        Uses SQLAlchemy inspect to check if 'runs' was eagerly loaded,
        avoiding lazy loading in async context which causes greenlet errors.
        
        Returns:
            Last run timestamp if runs are loaded, None otherwise
        """
        insp = sa_inspect(experience)
        if 'runs' not in insp.dict:
            return None
        
        runs = experience.runs
        if not runs:
            return None
        
        latest = max(runs, key=lambda r: r.created_at, default=None)
        if latest:
            return latest.finished_at or latest.created_at
        return None

    def _experience_to_response(self, experience: Experience) -> ExperienceResponse:
        """Convert Experience model to response schema."""
        steps = [
            ExperienceStepResponse(
                id=step.id,
                experience_id=step.experience_id,
                step_key=step.step_key,
                step_type=StepType(step.step_type),
                order=step.order,
                plugin_name=step.plugin_name,
                plugin_op=step.plugin_op,
                knowledge_base_id=step.knowledge_base_id,
                kb_query_template=step.kb_query_template,
                params_template=step.params_template,
                condition_template=step.condition_template,
                required_scopes=step.required_scopes,
                created_at=step.created_at,
                updated_at=step.updated_at
            )
            for step in sorted(experience.steps, key=lambda s: s.order)
        ]

        last_run_at = self._get_last_run_timestamp(experience)
        if last_run_at is None:
            # Fallback to the column value if runs are not loaded or empty
            last_run_at = experience.last_run_at

        return ExperienceResponse(
            id=experience.id,
            name=experience.name,
            description=experience.description,
            created_by=experience.created_by,
            visibility=ExperienceVisibility(experience.visibility),
            trigger_type=TriggerType(experience.trigger_type),
            trigger_config=experience.trigger_config,
            include_previous_run=experience.include_previous_run,
            llm_provider_id=experience.llm_provider_id,
            model_name=experience.model_name,
            prompt_id=experience.prompt_id,
            inline_prompt_template=experience.inline_prompt_template,
            max_run_seconds=experience.max_run_seconds,
            token_budget=experience.token_budget,
            version=experience.version,
            is_active_version=experience.is_active_version,
            parent_version_id=experience.parent_version_id,
            steps=steps,
            llm_provider=experience.llm_provider.to_dict() if experience.llm_provider else None,
            prompt=experience.prompt.to_dict() if experience.prompt else None,
            step_count=len(steps),
            last_run_at=last_run_at,
            created_at=experience.created_at,
            updated_at=experience.updated_at
        )

    def _run_to_response(self, run: ExperienceRun, user_info: Optional[Dict[str, Any]] = None) -> ExperienceRunResponse:
        """Convert ExperienceRun model to response schema."""
        duration_seconds = None
        if run.started_at and run.finished_at:
            delta = run.finished_at - run.started_at
            duration_seconds = delta.total_seconds()

        return ExperienceRunResponse(
            id=run.id,
            experience_id=run.experience_id,
            user_id=run.user_id,
            previous_run_id=run.previous_run_id,
            model_provider_id=run.model_provider_id,
            model_name=run.model_name,
            status=RunStatus(run.status),
            started_at=run.started_at,
            finished_at=run.finished_at,
            input_params=run.input_params,
            step_states=run.step_states,
            step_outputs=run.step_outputs,
            result_content=run.result_content,
            result_metadata=run.result_metadata,
            error_message=run.error_message,
            error_details=run.error_details,
            created_at=run.created_at,
            updated_at=run.updated_at,
            user=user_info,
            duration_seconds=duration_seconds
        )
