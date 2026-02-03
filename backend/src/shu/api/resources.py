"""Resource Management API for Shu RAG Backend.

Provides endpoints for monitoring and managing system resources,
particularly for RAG processing services and caches.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from ..auth.models import User
from ..auth.rbac import get_current_user, require_admin
from ..core.response import ShuResponse
from ..services.rag_processing_service import (
    cleanup_rag_services,
    clear_rag_service_cache,
    get_rag_service_stats,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resources", tags=["resources"])


@router.get(
    "/stats",
    summary="Get system resource usage statistics",
    description="Get detailed statistics about system resource usage including RAG services and caches",
)
async def get_resource_stats(current_user: User = Depends(require_admin)):
    """Get comprehensive system resource usage statistics."""
    try:
        # Get RAG service statistics
        rag_stats = get_rag_service_stats()

        # Get cache statistics (if available)
        cache_stats = {}
        try:
            # This would need to be implemented if we have a global cache instance
            # For now, we'll provide placeholder stats
            cache_stats = {
                "config_cache": "Not implemented",
                "note": "Cache statistics would be available if ConfigCache is globally accessible",
            }
        except Exception as e:
            logger.warning(f"Could not get cache statistics: {e}")
            cache_stats = {"error": "Cache statistics unavailable"}

        stats = {
            "rag_services": rag_stats,
            "caches": cache_stats,
            "resource_management": {"cleanup_available": True, "clear_cache_available": True},
        }

        return ShuResponse.success(data=stats)

    except Exception as e:
        logger.error(f"Error getting resource statistics: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get resource statistics: {e!s}")


@router.post(
    "/cleanup",
    summary="Cleanup expired system resources",
    description="Clean up expired RAG service instances and cache entries",
)
async def cleanup_resources(current_user: User = Depends(require_admin)):
    """Clean up expired system resources."""
    try:
        # Get stats before cleanup
        before_stats = get_rag_service_stats()

        # Perform cleanup
        cleanup_rag_services()

        # Get stats after cleanup
        after_stats = get_rag_service_stats()

        cleanup_result = {
            "before": before_stats,
            "after": after_stats,
            "instances_cleaned": before_stats["active_instances"] - after_stats["active_instances"],
            "cleanup_performed": True,
        }

        logger.info(f"Resource cleanup completed: {cleanup_result['instances_cleaned']} instances cleaned")

        return ShuResponse.success(data=cleanup_result)

    except Exception as e:
        logger.error(f"Error during resource cleanup: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to cleanup resources: {e!s}")


@router.post(
    "/clear-cache",
    summary="Clear all cached resources",
    description="Clear all RAG service instances and cached data. Use with caution in production.",
)
async def clear_all_cache(current_user: User = Depends(require_admin)):
    """Clear all cached resources."""
    try:
        # Get stats before clearing
        before_stats = get_rag_service_stats()

        # Clear all caches
        clear_rag_service_cache()

        # Get stats after clearing
        after_stats = get_rag_service_stats()

        clear_result = {
            "before": before_stats,
            "after": after_stats,
            "instances_cleared": before_stats["active_instances"],
            "cache_cleared": True,
            "warning": "All cached models and resources have been cleared. Next requests will be slower due to model reloading.",
        }

        logger.warning(f"All resource caches cleared: {clear_result['instances_cleared']} instances removed")

        return ShuResponse.success(data=clear_result)

    except Exception as e:
        logger.error(f"Error clearing cache: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to clear cache: {e!s}")


@router.get(
    "/health",
    summary="Check resource health",
    description="Check if system resource usage is within healthy limits",
)
async def check_resource_health(current_user: User = Depends(get_current_user)):
    """Check system resource health and provide recommendations."""
    try:
        stats = get_rag_service_stats()

        # Analyze health
        active_instances = stats["active_instances"]
        max_instances = stats["max_instances"]

        health_status = "healthy"
        recommendations = []

        if active_instances >= max_instances:
            health_status = "warning"
            recommendations.append("Maximum RAG service instances reached. Consider cleanup.")

        if active_instances >= max_instances * 0.8:
            health_status = "caution"
            recommendations.append("RAG service instances approaching limit.")

        # Check for old instances
        old_instances = 0
        for instance_info in stats.get("instances", {}).values():
            if instance_info.get("age_seconds", 0) > 7200:  # 2 hours
                old_instances += 1

        if old_instances > 0:
            recommendations.append(f"{old_instances} RAG service instances are over 2 hours old. Consider cleanup.")

        health_result = {
            "status": health_status,
            "active_instances": active_instances,
            "max_instances": max_instances,
            "utilization_percent": (active_instances / max_instances) * 100 if max_instances > 0 else 0,
            "old_instances": old_instances,
            "recommendations": recommendations,
            "last_check": "now",
        }

        return ShuResponse.success(data=health_result)

    except Exception as e:
        logger.error(f"Error checking resource health: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to check resource health: {e!s}")
