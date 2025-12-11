"""
Resource Management Integration Tests

Tests the resource management system for RAGProcessingService instances,
ensuring proper lifecycle management, memory usage bounds, and cleanup.
"""

import sys
import os
import asyncio
import time
from typing import List, Callable
from unittest.mock import patch, Mock

from integ.base_integration_test import BaseIntegrationTestSuite

async def test_rag_service_instance_caching(client, db, auth_headers):
        """Test that RAG service instances are properly cached and reused."""

        # Mock SentenceTransformer to avoid loading actual models
        with patch("sentence_transformers.SentenceTransformer") as mock_transformer:
            mock_model = Mock()
            mock_model.encode.return_value = [[0.1, 0.2, 0.3]]  # Mock embedding
            mock_transformer.return_value = mock_model
            
            from shu.services.rag_processing_service import RAGProcessingService, get_rag_service_stats, clear_rag_service_cache
            
            # Clear any existing instances
            clear_rag_service_cache()
            
            # Get initial stats
            initial_stats = get_rag_service_stats()
            assert initial_stats['active_instances'] == 0, "Should start with no instances"
            
            # Create first instance
            service1 = RAGProcessingService.get_instance("test-model", "cpu")
            stats_after_first = get_rag_service_stats()
            assert stats_after_first['active_instances'] == 1, "Should have 1 instance after first creation"
            
            # Create second instance with same parameters (should reuse)
            service2 = RAGProcessingService.get_instance("test-model", "cpu")
            stats_after_second = get_rag_service_stats()
            assert stats_after_second['active_instances'] == 1, "Should still have 1 instance (reused)"
            assert service1 is service2, "Should return the same cached instance"
            
            # Create third instance with different parameters
            RAGProcessingService.get_instance("different-model", "cpu")
            stats_after_third = get_rag_service_stats()
            assert stats_after_third['active_instances'] == 2, "Should have 2 instances with different models"
            
            # Clean up
            clear_rag_service_cache()
            final_stats = get_rag_service_stats()
            assert final_stats['active_instances'] == 0, "Should have no instances after cleanup"
    
async def test_rag_service_resource_cleanup(client, db, auth_headers):
        """Test that expired RAG service instances are cleaned up."""

        with patch("sentence_transformers.SentenceTransformer") as mock_transformer:
            mock_model = Mock()
            mock_model.encode.return_value = [[0.1, 0.2, 0.3]]
            mock_transformer.return_value = mock_model
            
            from shu.services.rag_processing_service import (
                RAGProcessingService, get_rag_service_stats, clear_rag_service_cache,
                cleanup_rag_services, _service_manager
            )
            
            # Clear any existing instances
            clear_rag_service_cache()
            
            # Temporarily reduce TTL for testing
            original_ttl = _service_manager._cache_ttl
            _service_manager._cache_ttl = 1  # 1 second TTL
            
            try:
                # Create an instance
                service = RAGProcessingService.get_instance("test-model", "cpu")
                stats_after_create = get_rag_service_stats()
                assert stats_after_create['active_instances'] == 1, "Should have 1 instance after creation"
                
                # Wait for TTL to expire
                await asyncio.sleep(2)
                
                # Trigger cleanup
                cleanup_rag_services()
                stats_after_cleanup = get_rag_service_stats()
                assert stats_after_cleanup['active_instances'] == 0, "Should have no instances after cleanup"
                
            finally:
                # Restore original TTL
                _service_manager._cache_ttl = original_ttl
                clear_rag_service_cache()
    
async def test_rag_service_instance_limits(client, db, auth_headers):
        """Test that RAG service instance limits are enforced."""

        with patch("sentence_transformers.SentenceTransformer") as mock_transformer:
            mock_model = Mock()
            mock_model.encode.return_value = [[0.1, 0.2, 0.3]]
            mock_transformer.return_value = mock_model
            
            from shu.services.rag_processing_service import (
                RAGProcessingService, get_rag_service_stats, clear_rag_service_cache, _service_manager
            )
            
            # Clear any existing instances
            clear_rag_service_cache()
            
            # Temporarily reduce max instances for testing
            original_max = _service_manager._max_instances
            _service_manager._max_instances = 3
            
            try:
                # Create instances up to the limit
                services = []
                for i in range(4):  # Try to create more than the limit
                    service = RAGProcessingService.get_instance(f"model-{i}", "cpu")
                    services.append(service)
                
                final_stats = get_rag_service_stats()
                assert final_stats['active_instances'] <= 3, f"Should not exceed limit of 3 instances, got {final_stats['active_instances']}"
                
            finally:
                # Restore original limit
                _service_manager._max_instances = original_max
                clear_rag_service_cache()
    
async def test_rag_service_statistics_accuracy(client, db, auth_headers):
        """Test that RAG service statistics are accurate."""

        with patch("sentence_transformers.SentenceTransformer") as mock_transformer:
            mock_model = Mock()
            mock_model.encode.return_value = [[0.1, 0.2, 0.3]]
            mock_transformer.return_value = mock_model
            
            from shu.services.rag_processing_service import RAGProcessingService, get_rag_service_stats, clear_rag_service_cache
            
            # Clear any existing instances
            clear_rag_service_cache()
            
            # Get initial stats
            initial_stats = get_rag_service_stats()
            assert initial_stats['active_instances'] == 0, "Should start with no instances"
            assert len(initial_stats['instances']) == 0, "Should have no instance details"
            
            # Create some instances
            service1 = RAGProcessingService.get_instance("model-1", "cpu")
            service2 = RAGProcessingService.get_instance("model-2", "cpu")
            
            # Get updated stats
            updated_stats = get_rag_service_stats()
            assert updated_stats['active_instances'] == 2, "Should have 2 active instances"
            assert len(updated_stats['instances']) == 2, "Should have 2 instance details"
            assert 'model-1:cpu' in updated_stats['instances'], "Should have model-1 instance"
            assert 'model-2:cpu' in updated_stats['instances'], "Should have model-2 instance"
            
            # Verify instance details have required fields
            for instance_key, instance_info in updated_stats['instances'].items():
                assert 'age_seconds' in instance_info, f"Instance {instance_key} should have age_seconds"
                assert 'last_used_seconds_ago' in instance_info, f"Instance {instance_key} should have last_used_seconds_ago"
                assert isinstance(instance_info['age_seconds'], (int, float)), "age_seconds should be numeric"
                assert isinstance(instance_info['last_used_seconds_ago'], (int, float)), "last_used_seconds_ago should be numeric"
            
            # Clean up
            clear_rag_service_cache()
    
async def test_resources_api_endpoints(client, db, auth_headers):
        """Test the resource management API endpoints."""
        
        # Test resource stats endpoint
        response = await client.get("/api/v1/resources/stats", headers=auth_headers)
        assert response.status_code == 200, f"Resource stats should return 200, got {response.status_code}"
        
        stats_data = response.json()
        assert 'data' in stats_data, "Resource stats should have data field"
        data = stats_data['data']
        assert 'rag_services' in data, "Should include RAG services stats"
        assert 'resource_management' in data, "Should include resource management info"
        
        # Test resource health endpoint
        response = await client.get("/api/v1/resources/health", headers=auth_headers)
        assert response.status_code == 200, f"Resource health should return 200, got {response.status_code}"
        
        health_data = response.json()
        assert 'data' in health_data, "Resource health should have data field"
        data = health_data['data']
        assert 'status' in data, "Should include health status"
        assert 'active_instances' in data, "Should include active instances count"
        
        # Test resource cleanup endpoint
        response = await client.post("/api/v1/resources/cleanup", headers=auth_headers)
        assert response.status_code == 200, f"Resource cleanup should return 200, got {response.status_code}"
        
        cleanup_data = response.json()
        assert 'data' in cleanup_data, "Resource cleanup should have data field"
        data = cleanup_data['data']
        assert 'cleanup_performed' in data, "Should indicate cleanup was performed"


class ResourceManagementIntegrationTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for resource management functionality."""

    def get_test_functions(self) -> List[Callable]:
        """Return all resource management integration test functions."""
        return [
            test_rag_service_instance_caching,
            test_rag_service_resource_cleanup,
            test_rag_service_instance_limits,
            test_rag_service_statistics_accuracy,
            test_resources_api_endpoints,
        ]

    def get_suite_name(self) -> str:
        """Return the suite name for CLI usage."""
        return "resource_management"

    def get_suite_description(self) -> str:
        """Return a description of this test suite."""
        return "Resource Management Integration Tests"


if __name__ == "__main__":
    suite = ResourceManagementIntegrationTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
