"""
Test Data Cleanup Utilities

This module provides utilities to clean up test data from the database
while preserving development data. Uses naming conventions to identify
test data that should be cleaned up.
"""

import asyncio
import logging
import re
from typing import List, Dict, Any
from httpx import AsyncClient

logger = logging.getLogger(__name__)


class TestDataCleaner:
    """Cleans up test data based on naming conventions."""

    # Patterns that identify test data - handle both word boundaries and underscores
    TEST_PATTERNS = [
        r".*\b[Tt]est\b.*",           # Contains "test" or "Test" as whole word
        r".*\bTEST\b.*",              # Contains "TEST" as whole word
        r".*^[Tt]est[_\W].*",         # Starts with "test" followed by underscore or non-word char
        r".*^TEST[_\W].*",            # Starts with "TEST" followed by underscore or non-word char
        r".*\bIntegration\b.*",       # Contains "Integration" as whole word
        r".*\bINTEGRATION\b.*",       # Contains "INTEGRATION" as whole word
        r".*\b[Dd]ummy\b.*",         # Contains "dummy" or "Dummy" as whole word
        r".*\b[Tt]emp\b.*",          # Contains "temp" or "Temp" as whole word
        r".*\b[Ss]ample\b.*",        # Contains "sample" or "Sample" as whole word
        r".*[Ww]orkflow.*[Tt]est.*",  # Contains "workflow" and "test"
        r".*[Tt]est.*[Ww]orkflow.*",  # Contains "test" and "workflow"
        r".*@test\..*",               # Email addresses with @test.
        r"test-.*@.*",                # Email addresses starting with test-
    ]

    def __init__(self, client: AsyncClient, auth_headers: Dict[str, str]):
        self.client = client
        self.auth_headers = auth_headers
        self.cleanup_stats = {
            'plugins': 0,
            'knowledge_bases': 0,
            'sources': 0,
            'sync_jobs': 0,
            'llm_providers': 0,
            'prompts': 0,
            'prompt_assignments': 0,
            'conversations': 0,
            'users': 0,
            'errors': []
        }

    def is_test_data(self, name: str) -> bool:
        """Check if a name matches test data patterns."""
        if not name:
            return False

        for pattern in self.TEST_PATTERNS:
            if re.match(pattern, name, re.IGNORECASE):
                return True
        return False

    async def cleanup_all_test_data(self) -> Dict[str, Any]:
        """Clean up all test data from the database."""
        logger.info("🧹 Starting comprehensive test data cleanup...")
        logger.info("=== EXPECTED TEST OUTPUT: Authentication warnings may occur during cleanup operations ===")

        # Set flag to enable comprehensive cleanup including sync jobs
        self._comprehensive_cleanup = True

        try:
            # Check if client is still available
            client_available = True
            try:
                # Test if client is still usable
                if hasattr(self.client, '_client') and self.client._client.is_closed:
                    client_available = False
            except Exception:
                client_available = False

            if not client_available:
                logger.warning("API client is closed, skipping API-based cleanup")
                return self.cleanup_stats

            # Clean up in dependency order (children first)
            await self._cleanup_plugin_feeds()  # Plugins: schedules/executions first
            await self._cleanup_plugins()  # Remove test plugins after feeds are gone
            await self._cleanup_prompt_assignments()
            await self._cleanup_prompts()
            await self._cleanup_sync_jobs()
            await self._cleanup_sources()
            await self._cleanup_knowledge_bases()
            await self._cleanup_conversations()  # Messages are cascade deleted
            await self._cleanup_model_configurations()  # Must be before LLM providers
            await self._cleanup_llm_providers()
            await self._cleanup_test_users()  # Clean up test users last

            logger.info("✅ Test data cleanup completed successfully")
            logger.info("=== EXPECTED TEST OUTPUT: Any authentication warnings above were expected during cleanup ===")
            return self.cleanup_stats

        except Exception as e:
            logger.error(f"❌ Test data cleanup failed: {e}")
            self.cleanup_stats['errors'].append(str(e))
            return self.cleanup_stats

    async def cleanup_test_data_quick(self) -> Dict[str, Any]:
        """Quick cleanup without sync history calls for individual test cleanups."""
        logger.info("🧹 Running quick test data cleanup...")

        # Disable comprehensive cleanup to skip sync history calls
        self._comprehensive_cleanup = False

        try:
            # Check if client is still available
            client_available = True
            try:
                # Test if client is still usable
                if hasattr(self.client, '_client') and self.client._client.is_closed:
                    client_available = False
            except Exception:
                client_available = False

            if not client_available:
                logger.warning("API client is closed, skipping API-based cleanup")
                return self.cleanup_stats

            # Clean up in dependency order (children first) - skip sync jobs
            await self._cleanup_plugin_feeds()  # Plugins: schedules/executions first
            await self._cleanup_plugins()  # Remove test plugins after feeds are gone
            await self._cleanup_prompt_assignments()
            await self._cleanup_prompts()
            # Skip sync jobs for quick cleanup
            await self._cleanup_sources()
            await self._cleanup_knowledge_bases()
            await self._cleanup_conversations()  # Messages are cascade deleted
            await self._cleanup_model_configurations()  # Must be before LLM providers
            await self._cleanup_llm_providers()
            await self._cleanup_test_users()  # Clean up test users last

            logger.info("✅ Quick test data cleanup completed successfully")
            return self.cleanup_stats

        except Exception as e:
            logger.error(f"❌ Quick test data cleanup failed: {e}")
            self.cleanup_stats['errors'].append(str(e))

    async def _cleanup_plugin_feeds(self):
        """Clean up test plugin schedules (and prevent orphaned executions from running)."""
        try:
            # List all schedules
            resp = await self.client.get("/api/v1/plugins/admin/feeds", headers=self.auth_headers)
            if resp.status_code != 200:
                logger.warning(f"Failed to list plugin schedules: {resp.status_code}")
                return
            schedules = resp.json().get("data", [])
            # Delete schedules that look like test data
            for sched in schedules:
                name = sched.get("name", "")
                if self.is_test_data(name):
                    try:
                        del_resp = await self.client.delete(f"/api/v1/plugins/admin/feeds/{sched['id']}", headers=self.auth_headers)
                        if del_resp.status_code in [200, 204]:
                            logger.info(f"🗑️  Cleaned up plugin schedule: {name}")
                        else:
                            logger.warning(f"Failed to delete plugin schedule {name}: {del_resp.status_code}")
                    except Exception as e:
                        logger.warning(f"Error deleting plugin schedule {name}: {e}")
        except Exception as e:
            logger.warning(f"Plugin feeds cleanup warning: {e}")



    async def _cleanup_plugins(self):
        """Remove test plugins (registry rows + plugin directories)."""
        try:
            resp = await self.client.get("/api/v1/plugins", headers=self.auth_headers)
            if resp.status_code != 200:
                logger.warning(f"Failed to list plugins: {resp.status_code}")
                return

            data = resp.json().get("data", [])
            if isinstance(data, dict):
                plugins = data.get("items") or data.get("plugins") or data.get("data") or []
            elif isinstance(data, list):
                plugins = data
            else:
                plugins = []

            # Only delete the in-test sample plugin to avoid touching dev/debug plugins.
            target_names = {"my_test_plugin"}

            for plugin in plugins:
                name = plugin.get("name") if isinstance(plugin, dict) else str(plugin)
                if not name or name not in target_names:
                    continue

                try:
                    del_resp = await self.client.delete(f"/api/v1/plugins/admin/{name}", headers=self.auth_headers)
                    if del_resp.status_code in [200, 204]:
                        self.cleanup_stats['plugins'] += 1
                        logger.info(f"🗑️  Deleted plugin: {name}")
                    elif del_resp.status_code == 409:
                        logger.warning(f"Plugin {name} has dependent feeds; will retry after feed cleanup")
                    else:
                        logger.warning(f"Failed to delete plugin {name}: {del_resp.status_code} {del_resp.text}")
                except Exception as e:
                    error_msg = f"Error deleting plugin {name}: {e}"
                    logger.error(error_msg)
                    self.cleanup_stats['errors'].append(error_msg)

        except Exception as e:
            error_msg = f"Plugin cleanup warning: {e}"
            logger.warning(error_msg)
            self.cleanup_stats['errors'].append(error_msg)


    async def _cleanup_prompts(self):
        """Clean up test prompts."""
        try:
            offset = 0
            limit = 100  # Get more prompts per request

            while True:
                response = await self.client.get(
                    f"/api/v1/prompts/?limit={limit}&offset={offset}",
                    headers=self.auth_headers
                )
                if response.status_code != 200:
                    logger.warning(f"Failed to list prompts: {response.status_code}")
                    return

                data = response.json()["data"]
                # Handle different response formats
                if isinstance(data, dict):
                    if "items" in data:
                        prompts = data["items"]
                    elif "prompts" in data:
                        prompts = data["prompts"]
                    else:
                        prompts = []
                    total = data.get("total", 0)
                elif isinstance(data, list):
                    prompts = data
                    total = len(data)
                else:
                    prompts = [data] if data else []
                    total = len(prompts)

                # Process prompts in this batch
                for prompt in prompts:
                    prompt_name = prompt.get("name", "")
                    is_test = self.is_test_data(prompt_name)
                    logger.debug(f"Checking prompt: '{prompt_name}' -> is_test: {is_test}")

                    if is_test:
                        try:
                            delete_response = await self.client.delete(
                                f"/api/v1/prompts/{prompt['id']}",
                                headers=self.auth_headers
                            )
                            if delete_response.status_code in [200, 204]:
                                self.cleanup_stats['prompts'] += 1
                                logger.info(f"🗑️  Cleaned up prompt: {prompt['name']}")
                            else:
                                logger.warning(f"Failed to delete prompt {prompt['name']}: {delete_response.status_code}")
                        except Exception as e:
                            error_msg = f"Error deleting prompt {prompt.get('name', prompt.get('id'))}: {e}"
                            logger.error(error_msg)
                            self.cleanup_stats['errors'].append(error_msg)

                # Check if we've processed all prompts
                if offset + len(prompts) >= total:
                    break

                offset += limit

        except Exception as e:
            error_msg = f"Error cleaning up prompts: {e}"
            logger.error(error_msg)
            self.cleanup_stats['errors'].append(error_msg)

    async def _cleanup_prompt_assignments(self):
        """Clean up test prompt assignments."""
        try:
            # Get all prompts first (with pagination)
            offset = 0
            limit = 100

            while True:
                response = await self.client.get(
                    f"/api/v1/prompts/?limit={limit}&offset={offset}",
                    headers=self.auth_headers
                )
                if response.status_code != 200:
                    return

                data = response.json()["data"]
                # Standard API response format with items
                prompts = data.get("items", [])
                total = data.get("total", 0)

                for prompt in prompts:
                    if self.is_test_data(prompt.get("name", "")):
                        try:
                            # Get assignments for this prompt
                            assignments_response = await self.client.get(
                                f"/api/v1/prompts/{prompt['id']}/assignments",
                                headers=self.auth_headers
                            )

                            if assignments_response.status_code == 200:
                                assignments_data = assignments_response.json()["data"]
                                assignments = assignments_data.get("items", assignments_data) if isinstance(assignments_data, dict) else assignments_data

                                for assignment in assignments:
                                    try:
                                        # Delete the assignment
                                        delete_response = await self.client.delete(
                                            f"/api/v1/prompts/{prompt['id']}/assignments/{assignment['entity_id']}",
                                            headers=self.auth_headers
                                        )
                                        if delete_response.status_code in [200, 204]:
                                            self.cleanup_stats['prompt_assignments'] += 1
                                            logger.info(f"🗑️  Cleaned up prompt assignment: {prompt['name']} -> {assignment['entity_id']}")
                                    except Exception as e:
                                        error_msg = f"Error deleting prompt assignment {assignment.get('entity_id')}: {e}"
                                        logger.error(error_msg)
                                        self.cleanup_stats['errors'].append(error_msg)
                        except Exception as e:
                            logger.warning(f"Error processing assignments for prompt {prompt.get('name')}: {e}")

                # Check if we've processed all prompts
                if offset + len(prompts) >= total:
                    break

                offset += limit

        except Exception as e:
            error_msg = f"Error cleaning up prompt assignments: {e}"
            logger.error(error_msg)
            self.cleanup_stats['errors'].append(error_msg)

    async def _cleanup_knowledge_bases(self):
        """Clean up test knowledge bases."""
        try:
            response = await self.client.get("/api/v1/knowledge-bases",
                                           headers=self.auth_headers)
            if response.status_code != 200:
                logger.warning(f"Failed to list knowledge bases: {response.status_code}")
                return

            data = response.json()["data"]
            kbs = data.get("items", data) if isinstance(data, dict) else data

            for kb in kbs:
                if self.is_test_data(kb.get("name", "")):
                    try:
                        delete_response = await self.client.delete(
                            f"/api/v1/knowledge-bases/{kb['id']}",
                            headers=self.auth_headers
                        )
                        if delete_response.status_code in [200, 204]:
                            self.cleanup_stats['knowledge_bases'] += 1
                            logger.info(f"🗑️  Cleaned up KB: {kb['name']}")
                        else:
                            logger.warning(f"Failed to delete KB {kb['name']}: {delete_response.status_code}")
                    except Exception as e:
                        error_msg = f"Error deleting KB {kb.get('name', kb.get('id'))}: {e}"
                        logger.error(error_msg)
                        self.cleanup_stats['errors'].append(error_msg)

        except Exception as e:
            error_msg = f"Error cleaning up knowledge bases: {e}"
            logger.error(error_msg)
            self.cleanup_stats['errors'].append(error_msg)

    async def _cleanup_sources(self):
        """Clean up test sources from all knowledge bases."""
        try:
            # Get all knowledge bases first
            response = await self.client.get("/api/v1/knowledge-bases",
                                           headers=self.auth_headers)
            if response.status_code != 200:
                return

            data = response.json()["data"]
            kbs = data.get("items", data) if isinstance(data, dict) else data

            for kb in kbs:
                try:
                    # Get sources for this KB
                    sources_response = await self.client.get(
                        f"/api/v1/knowledge-bases/{kb['id']}/sources",
                        headers=self.auth_headers
                    )

                    if sources_response.status_code != 200:
                        continue

                    sources_data = sources_response.json()["data"]
                    sources = sources_data.get("items", sources_data) if isinstance(sources_data, dict) else sources_data

                    for source in sources:
                        if self.is_test_data(source.get("name", "")):
                            try:
                                delete_response = await self.client.delete(
                                    f"/api/v1/knowledge-bases/{kb['id']}/sources/{source['id']}",
                                    headers=self.auth_headers
                                )
                                if delete_response.status_code in [200, 204]:
                                    self.cleanup_stats['sources'] += 1
                                    logger.info(f"🗑️  Cleaned up source: {source['name']}")
                            except Exception as e:
                                error_msg = f"Error deleting source {source.get('name')}: {e}"
                                logger.error(error_msg)
                                self.cleanup_stats['errors'].append(error_msg)

                except Exception as e:
                    logger.warning(f"Error processing sources for KB {kb.get('name')}: {e}")

        except Exception as e:
            error_msg = f"Error cleaning up sources: {e}"
            logger.error(error_msg)
            self.cleanup_stats['errors'].append(error_msg)

    async def _cleanup_sync_jobs(self):
        """Clean up test sync jobs with proper background job handling."""
        try:
            # Only get sync history if we're doing a comprehensive cleanup
            # Skip this during individual test cleanups to reduce noise
            if not hasattr(self, '_comprehensive_cleanup') or not self._comprehensive_cleanup:
                return

            # Get sync history to find test jobs
            response = await self.client.get("/api/v1/sync/history",
                                           headers=self.auth_headers)

            # The history endpoint should work now - if it doesn't, that's an error
            if response.status_code != 200:
                logger.warning(f"Sync history endpoint returned {response.status_code}, skipping sync job cleanup")
                return

            data = response.json()["data"]
            jobs = data.get("items", [])

            for job in jobs:
                    # Check if job is associated with test KB or has test name
                    job_name = job.get("name", "")
                    kb_name = job.get("knowledge_base_name", "")

                    if self.is_test_data(job_name) or self.is_test_data(kb_name):
                        try:
                            kb_id = job.get("knowledge_base_id")
                            job_id = job.get("id")
                            job_status = job.get("status", "")

                            if kb_id and job_id:
                                # Cancel running jobs first
                                if job_status in ["pending", "running"]:
                                    cancel_response = await self.client.post(
                                        f"/api/v1/sync/{kb_id}/jobs/{job_id}/cancel",
                                        headers=self.auth_headers
                                    )
                                    if cancel_response.status_code == 200:
                                        logger.info(f"🗑️  Cancelled running sync job: {job_id}")
                                        # Wait a moment for cancellation to take effect
                                        import asyncio
                                        await asyncio.sleep(0.2)

                                # Now delete the job
                                delete_response = await self.client.delete(
                                    f"/api/v1/sync/{kb_id}/jobs/{job_id}",
                                    headers=self.auth_headers
                                )
                                if delete_response.status_code in [200, 204]:
                                    self.cleanup_stats['sync_jobs'] += 1
                                    logger.info(f"🗑️  Cleaned up sync job: {job_id}")
                                else:
                                    logger.warning(f"Failed to delete sync job {job_id}: {delete_response.status_code}")
                        except Exception as e:
                            error_msg = f"Error deleting sync job {job.get('id')}: {e}"
                            logger.warning(error_msg)  # Use warning instead of error for expected issues
                            self.cleanup_stats['errors'].append(error_msg)

        except Exception as e:
            logger.warning(f"Sync job cleanup completed with expected errors: {e}")

        # Add a small delay to allow background jobs to finish cleanup
        import asyncio
        await asyncio.sleep(0.5)

    async def _cleanup_llm_providers(self):
        """Clean up test LLM providers."""
        try:
            response = await self.client.get("/api/v1/llm/providers",
                                           headers=self.auth_headers)
            if response.status_code != 200:
                logger.warning(f"Failed to list LLM providers: {response.status_code}")
                return

            # Handle envelope or direct list
            data = response.json()
            providers = data.get("data", data) if isinstance(data, dict) else data
            if isinstance(providers, dict):
                providers = providers.get("items", [])
            if not isinstance(providers, list):
                logger.warning(f"Unexpected LLM providers response format: {type(providers)}")
                return

            for provider in providers:
                if self.is_test_data(provider.get("name", "")):
                    try:
                        delete_response = await self.client.delete(
                            f"/api/v1/llm/providers/{provider['id']}",
                            headers=self.auth_headers
                        )
                        if delete_response.status_code in [200, 204]:
                            self.cleanup_stats['llm_providers'] += 1
                            logger.info(f"🗑️  Cleaned up LLM provider: {provider['name']}")
                        else:
                            logger.warning(f"Failed to delete provider {provider['name']}: {delete_response.status_code}")
                    except Exception as e:
                        error_msg = f"Error deleting LLM provider {provider.get('name')}: {e}"
                        logger.error(error_msg)
                        self.cleanup_stats['errors'].append(error_msg)

        except Exception as e:
            error_msg = f"Error cleaning up LLM providers: {e}"
            logger.error(error_msg)
            self.cleanup_stats['errors'].append(error_msg)

    async def _cleanup_model_configurations(self):
        """Clean up test model configurations."""
        try:
            response = await self.client.get("/api/v1/model-configurations",
                                           headers=self.auth_headers)
            if response.status_code != 200:
                logger.warning(f"Failed to list model configurations: {response.status_code}")
                return

            # Model configurations endpoint returns paginated data in envelope
            data = response.json()
            if isinstance(data, dict):
                # Handle paginated response: {'data': {'items': [...], 'total': N}}
                data_section = data.get("data", {})
                if isinstance(data_section, dict) and "items" in data_section:
                    configurations = data_section["items"]
                else:
                    configurations = data.get("data", [])
            else:
                configurations = data

            if not isinstance(configurations, list):
                logger.warning(f"Unexpected model configurations response format: {type(configurations)}")
                logger.warning(f"Response data: {data}")
                return

            for config in configurations:
                if self.is_test_data(config.get("name", "")):
                    try:
                        delete_response = await self.client.delete(
                            f"/api/v1/model-configurations/{config['id']}",
                            headers=self.auth_headers
                        )
                        if delete_response.status_code in [200, 204]:
                            self.cleanup_stats['model_configurations'] = self.cleanup_stats.get('model_configurations', 0) + 1
                            logger.info(f"🗑️  Cleaned up model configuration: {config['name']}")
                        else:
                            logger.warning(f"Failed to delete model configuration {config['name']}: {delete_response.status_code}")
                    except Exception as e:
                        error_msg = f"Error deleting model configuration {config.get('name')}: {e}"
                        logger.error(error_msg)
                        self.cleanup_stats['errors'].append(error_msg)

        except Exception as e:
            error_msg = f"Error cleaning up model configurations: {e}"
            logger.error(error_msg)
            self.cleanup_stats['errors'].append(error_msg)

    async def _cleanup_conversations(self):
        """Clean up test conversations and their messages."""
        try:
            response = await self.client.get("/api/v1/chat/conversations",
                                           headers=self.auth_headers)

            if response.status_code != 200:
                return

            conversations = response.json().get("data", [])

            for conversation in conversations:
                if self.is_test_data(conversation.get("title", "")):
                    try:
                        delete_response = await self.client.delete(
                            f"/api/v1/chat/conversations/{conversation['id']}",
                            headers=self.auth_headers
                        )
                        if delete_response.status_code in [200, 204]:
                            self.cleanup_stats['conversations'] += 1
                            logger.info(f"🗑️  Cleaned up conversation: {conversation['title']}")
                    except Exception as e:
                        error_msg = f"Error deleting conversation {conversation.get('title')}: {e}"
                        logger.warning(error_msg)  # Use warning instead of error for expected issues
                        self.cleanup_stats['errors'].append(error_msg)

        except Exception as e:
            error_msg = f"Error cleaning up conversations: {e}"
            logger.warning(error_msg)  # Use warning for expected validation errors
            self.cleanup_stats['errors'].append(error_msg)

    async def _cleanup_test_users(self):
        """Clean up test users created during testing."""
        try:
            # Get list of users via admin endpoint
            response = await self.client.get("/api/v1/auth/users", headers=self.auth_headers)
            if response.status_code != 200:
                logger.warning(f"Failed to get users for cleanup: {response.status_code}")
                return

            users_data = response.json()
            if "data" not in users_data:
                logger.warning("No user data found in response")
                return

            # Handle both list and dict formats for the data
            data = users_data["data"]
            if isinstance(data, list):
                users = data
            elif isinstance(data, dict) and "items" in data:
                users = data["items"]
            else:
                logger.warning(f"Unexpected user data format: {type(data)}")
                return

            for user in users:
                user_email = user.get("email", "")
                user_name = user.get("name", "")

                # Check if this is test data (but preserve the current admin user)
                if self.is_test_data(user_email) or self.is_test_data(user_name):
                    # Don't delete the current admin user (the one we're using for auth)
                    if "test-admin-" in user_email and "@example.com" in user_email:
                        logger.info(f"Preserving current admin user: {user_email}")
                        continue

                    try:
                        delete_response = await self.client.delete(
                            f"/api/v1/auth/users/{user['user_id']}",
                            headers=self.auth_headers
                        )
                        if delete_response.status_code in [200, 204]:
                            self.cleanup_stats['users'] += 1
                            logger.info(f"🗑️  Cleaned up test user: {user_email}")
                        else:
                            logger.warning(f"Failed to delete user {user_email}: {delete_response.status_code}")
                    except Exception as e:
                        error_msg = f"Error deleting test user {user_email}: {e}"
                        logger.error(error_msg)
                        self.cleanup_stats['errors'].append(error_msg)

        except Exception as e:
            error_msg = f"Error cleaning up test users: {e}"
            logger.error(error_msg)
            self.cleanup_stats['errors'].append(error_msg)


async def cleanup_test_data_main():
    """Main function for running cleanup as a standalone script."""
    import sys
    import os

    from integ.integration_test_runner import IntegrationTestRunner

    # Create test runner to get client and auth
    runner = IntegrationTestRunner()
    await runner.setup()

    try:
        # Create cleaner and run cleanup
        cleaner = TestDataCleaner(runner.client, runner.auth_headers)
        stats = await cleaner.cleanup_all_test_data()

        # Print results
        print("\n" + "="*60)
        print("🧹 TEST DATA CLEANUP RESULTS")
        print("="*60)
        print(f"Knowledge Bases cleaned: {stats['knowledge_bases']}")
        print(f"Sources cleaned: {stats['sources']}")
        print(f"Sync Jobs cleaned: {stats['sync_jobs']}")
        print(f"LLM Providers cleaned: {stats['llm_providers']}")
        print(f"Prompts cleaned: {stats['prompts']}")
        print(f"Prompt Assignments cleaned: {stats['prompt_assignments']}")
        print(f"Conversations cleaned: {stats['conversations']}")
        print(f"Users cleaned: {stats['users']}")

        if stats['errors']:
            print(f"\n❌ Errors encountered: {len(stats['errors'])}")
            for error in stats['errors']:
                print(f"  - {error}")
        else:
            print("\n✅ Cleanup completed without errors!")

    finally:
        await runner.teardown()


if __name__ == "__main__":
    asyncio.run(cleanup_test_data_main())
