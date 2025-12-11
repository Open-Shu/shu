#!/usr/bin/env python3
"""
Standalone Test Data Cleanup Script

This script cleans up test data from the Shu database while preserving
your development data. It identifies test data using naming conventions.

Usage:
    python cleanup_test_data.py [--dry-run] [--verbose]

Options:
    --dry-run    Show what would be cleaned up without actually deleting
    --verbose    Show detailed output
    --help       Show this help message
"""

import asyncio
import argparse
import sys
import os
from pathlib import Path

# Add src and tests to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))
sys.path.insert(0, str(project_root / "src" / "tests"))
sys.path.insert(0, str(project_root))

from integ.test_data_cleanup import TestDataCleaner
from integ.integration_test_runner import IntegrationTestRunner


class DryRunCleaner(TestDataCleaner):
    """Test data cleaner that shows what would be deleted without actually deleting."""
    
    def __init__(self, client, auth_headers):
        super().__init__(client, auth_headers)
        self.would_delete = {
            'knowledge_bases': [],
            'sources': [],
            'sync_jobs': [],
            'llm_providers': [],
            'prompts': [],
            'prompt_assignments': []
        }
    
    async def cleanup_all_test_data(self):
        """Show what would be cleaned up without actually deleting."""
        print("DRY RUN: Scanning for test data to clean up...")
        
        await self._scan_prompts()
        await self._scan_prompt_assignments()
        await self._scan_knowledge_bases()
        await self._scan_sources()
        await self._scan_sync_jobs()
        await self._scan_llm_providers()
        
        return self._print_dry_run_results()
    
    async def _scan_knowledge_bases(self):
        """Scan for test knowledge bases."""
        try:
            response = await self.client.get("/api/v1/knowledge-bases", 
                                           headers=self.auth_headers)
            if response.status_code != 200:
                return
            
            data = response.json()["data"]
            kbs = data.get("items", data) if isinstance(data, dict) else data
            
            for kb in kbs:
                if self.is_test_data(kb.get("name", "")):
                    self.would_delete['knowledge_bases'].append({
                        'id': kb['id'],
                        'name': kb['name']
                    })
        except Exception as e:
            print(f"Error scanning knowledge bases: {e}")
    
    async def _scan_sources(self):
        """Scan for test sources."""
        try:
            response = await self.client.get("/api/v1/knowledge-bases", 
                                           headers=self.auth_headers)
            if response.status_code != 200:
                return
            
            data = response.json()["data"]
            kbs = data.get("items", data) if isinstance(data, dict) else data
            
            for kb in kbs:
                try:
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
                            self.would_delete['sources'].append({
                                'id': source['id'],
                                'name': source['name'],
                                'kb_name': kb['name']
                            })
                except Exception:
                    continue
        except Exception as e:
            print(f"Error scanning sources: {e}")
    
    async def _scan_sync_jobs(self):
        """Scan for test sync jobs."""
        try:
            response = await self.client.get("/api/v1/sync/history", 
                                           headers=self.auth_headers)
            
            if response.status_code == 200:
                data = response.json()["data"]
                jobs = data.get("items", data) if isinstance(data, dict) else data
                
                for job in jobs:
                    job_name = job.get("name", "")
                    kb_name = job.get("knowledge_base_name", "")
                    
                    if self.is_test_data(job_name) or self.is_test_data(kb_name):
                        self.would_delete['sync_jobs'].append({
                            'id': job.get('id'),
                            'name': job_name or f"Job {job.get('id', 'Unknown')}",
                            'kb_name': kb_name
                        })
        except Exception:
            pass  # Sync history might not be available
    
    async def _scan_llm_providers(self):
        """Scan for test LLM providers."""
        try:
            response = await self.client.get("/api/v1/llm/providers", 
                                           headers=self.auth_headers)
            if response.status_code != 200:
                return
            
            data = response.json()["data"]
            # Handle different response formats
            if isinstance(data, dict) and "items" in data:
                providers = data["items"]
            elif isinstance(data, list):
                providers = data
            else:
                providers = [data] if data else []

            for provider in providers:
                if self.is_test_data(provider.get("name", "")):
                    self.would_delete['llm_providers'].append({
                        'id': provider['id'],
                        'name': provider['name']
                    })
        except Exception as e:
            print(f"Error scanning LLM providers: {e}")
    
    async def _scan_prompts(self):
        """Scan for test prompts."""
        try:
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
                # Handle different response formats
                if isinstance(data, dict) and "items" in data:
                    prompts = data["items"]
                    total = data.get("total", 0)
                elif isinstance(data, list):
                    prompts = data
                    total = len(data)
                else:
                    prompts = [data] if data else []
                    total = len(prompts)
                
                for prompt in prompts:
                    if self.is_test_data(prompt.get("name", "")):
                        self.would_delete['prompts'].append({
                            'id': prompt['id'],
                            'name': prompt['name']
                        })
                
                # Check if we've processed all prompts
                if offset + len(prompts) >= total:
                    break
                    
                offset += limit
        except Exception as e:
            print(f"Error scanning prompts: {e}")
    
    async def _scan_prompt_assignments(self):
        """Scan for test prompt assignments."""
        try:
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
                # Handle different response formats
                if isinstance(data, dict) and "items" in data:
                    prompts = data["items"]
                    total = data.get("total", 0)
                elif isinstance(data, list):
                    prompts = data
                    total = len(data)
                else:
                    prompts = [data] if data else []
                    total = len(prompts)
                
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
                                    self.would_delete['prompt_assignments'].append({
                                        'prompt_id': prompt['id'],
                                        'prompt_name': prompt['name'],
                                        'entity_id': assignment['entity_id']
                                    })
                        except Exception:
                            continue
                
                # Check if we've processed all prompts
                if offset + len(prompts) >= total:
                    break
                    
                offset += limit
        except Exception as e:
            print(f"Error scanning prompt assignments: {e}")
    
    def _print_dry_run_results(self):
        """Print what would be deleted."""
        print("\n" + "="*60)
        print("DRY RUN RESULTS - What would be cleaned up:")
        print("="*60)
        
        total_items = 0
        
        if self.would_delete['knowledge_bases']:
            print(f"\nKnowledge Bases ({len(self.would_delete['knowledge_bases'])}):")
            for kb in self.would_delete['knowledge_bases']:
                print(f"  - {kb['name']} (ID: {kb['id']})")
            total_items += len(self.would_delete['knowledge_bases'])
        
        if self.would_delete['sources']:
            print(f"\nSources ({len(self.would_delete['sources'])}):")
            for source in self.would_delete['sources']:
                print(f"  - {source['name']} (KB: {source['kb_name']})")
            total_items += len(self.would_delete['sources'])
        
        if self.would_delete['sync_jobs']:
            print(f"\nSync Jobs ({len(self.would_delete['sync_jobs'])}):")
            for job in self.would_delete['sync_jobs']:
                print(f"  - {job['name']} (KB: {job.get('kb_name', 'Unknown')})")
            total_items += len(self.would_delete['sync_jobs'])
        
        if self.would_delete['llm_providers']:
            print(f"\nLLM Providers ({len(self.would_delete['llm_providers'])}):")
            for provider in self.would_delete['llm_providers']:
                print(f"  - {provider['name']} (ID: {provider['id']})")
            total_items += len(self.would_delete['llm_providers'])
        
        if self.would_delete['prompts']:
            print(f"\nPrompts ({len(self.would_delete['prompts'])}):")
            for prompt in self.would_delete['prompts']:
                print(f"  - {prompt['name']} (ID: {prompt['id']})")
            total_items += len(self.would_delete['prompts'])
        
        if self.would_delete['prompt_assignments']:
            print(f"\nPrompt Assignments ({len(self.would_delete['prompt_assignments'])}):")
            for assignment in self.would_delete['prompt_assignments']:
                print(f"  - {assignment['prompt_name']} -> {assignment['entity_id']}")
            total_items += len(self.would_delete['prompt_assignments'])
        
        if total_items == 0:
            print("\nNo test data found to clean up!")
        else:
            print(f"\nTotal items that would be deleted: {total_items}")
            print("\nRun without --dry-run to actually perform the cleanup.")
        
        return {'total_items': total_items}


async def main():
    parser = argparse.ArgumentParser(
        description="Clean up test data from Shu database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("--dry-run", action="store_true", 
                       help="Show what would be cleaned up without actually deleting")
    parser.add_argument("--verbose", action="store_true",
                       help="Show detailed output")
    
    args = parser.parse_args()
    
    if args.verbose:
        import logging
        logging.getLogger().setLevel(logging.INFO)
    
    print("🧹 Shu Test Data Cleanup Tool")
    print("="*40)
    
    # Create test runner to get client and auth
    runner = IntegrationTestRunner()
    
    try:
        await runner.setup()
        
        if args.dry_run:
            # Use dry run cleaner
            cleaner = DryRunCleaner(runner.client, runner.auth_headers)
            await cleaner.cleanup_all_test_data()
        else:
            # Use real cleaner
            cleaner = TestDataCleaner(runner.client, runner.auth_headers)
            stats = await cleaner.cleanup_all_test_data()
            
            # Print results
            print("\n" + "="*60)
            print("🧹 CLEANUP RESULTS")
            print("="*60)
            print(f"Knowledge Bases cleaned: {stats['knowledge_bases']}")
            print(f"Sources cleaned: {stats['sources']}")
            print(f"Sync Jobs cleaned: {stats['sync_jobs']}")
            print(f"LLM Providers cleaned: {stats['llm_providers']}")
            print(f"Prompts cleaned: {stats['prompts']}")
            print(f"Prompt Assignments cleaned: {stats['prompt_assignments']}")
            
            total_cleaned = (stats['knowledge_bases'] + stats['sources'] + 
                           stats['sync_jobs'] + stats['llm_providers'] + 
                           stats['prompts'] + stats['prompt_assignments'])
            
            if stats['errors']:
                print(f"\nErrors encountered: {len(stats['errors'])}")
                for error in stats['errors']:
                    print(f"  - {error}")
            
            if total_cleaned == 0:
                print("\nNo test data found to clean up!")
            else:
                print(f"\nSuccessfully cleaned up {total_cleaned} test entities!")
                
    except KeyboardInterrupt:
        print("\n\nCleanup interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nError during cleanup: {e}")
        sys.exit(1)
    finally:
        # Don't call teardown here since it closes the client before cleanup
        # The cleanup is already handled in setup()
        if runner.client:
            await runner.client.aclose()
        if runner.db:
            await runner.db.close()


async def cleanup_test_data_main():
    """Main function for running cleanup from other scripts."""
    # Add paths for imports

    from integ.integration_test_runner import IntegrationTestRunner

    # Create test runner to get client and auth
    runner = IntegrationTestRunner()

    try:
        await runner.setup()

        # Use real cleaner
        cleaner = TestDataCleaner(runner.client, runner.auth_headers)
        stats = await cleaner.cleanup_all_test_data()

        # Print results
        print("\n" + "="*60)
        print("TEST DATA CLEANUP RESULTS")
        print("="*60)
        print(f"Knowledge Bases cleaned: {stats['knowledge_bases']}")
        print(f"Sources cleaned: {stats['sources']}")
        print(f"Sync Jobs cleaned: {stats['sync_jobs']}")
        print(f"LLM Providers cleaned: {stats['llm_providers']}")
        print(f"Prompts cleaned: {stats['prompts']}")
        print(f"Prompt Assignments cleaned: {stats['prompt_assignments']}")

        total_cleaned = (stats['knowledge_bases'] + stats['sources'] +
                       stats['sync_jobs'] + stats['llm_providers'] + 
                       stats['prompts'] + stats['prompt_assignments'])

        if stats['errors']:
            print(f"\nErrors encountered: {len(stats['errors'])}")
            for error in stats['errors']:
                print(f"  - {error}")

        if total_cleaned == 0:
            print("\nNo test data found to clean up!")
        else:
            print(f"\nSuccessfully cleaned up {total_cleaned} test entities!")

    finally:
        await runner.teardown()


if __name__ == "__main__":
    asyncio.run(main())
