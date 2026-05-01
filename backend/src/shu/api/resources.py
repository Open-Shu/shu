"""Resource Management API for Shu RAG Backend.

Provides endpoints for monitoring and managing system resources,
particularly for embedding services and caches.
"""

import gc
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth.models import User
from ..auth.rbac import get_current_user, require_admin
from ..core.embedding_service import (
    cleanup_embedding_services,
    clear_embedding_service_cache,
    get_embedding_service_stats,
)
from ..core.memory_tools import (
    asyncio_task_inventory,
    current_rss_bytes,
    get_tracemalloc_controller,
    top_object_types,
    trim_memory,
)
from ..core.response import ShuResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resources", tags=["resources"])


@router.get(
    "/stats",
    summary="Get system resource usage statistics",
    description="Get detailed statistics about system resource usage including embedding services and caches",
)
async def get_resource_stats(current_user: User = Depends(require_admin)):
    """Get comprehensive system resource usage statistics."""
    try:
        embedding_stats = get_embedding_service_stats()

        # Get cache statistics (if available)
        cache_stats = {}
        try:
            cache_stats = {
                "config_cache": "Not implemented",
                "note": "Cache statistics would be available if ConfigCache is globally accessible",
            }
        except Exception as e:
            logger.warning(f"Could not get cache statistics: {e}")
            cache_stats = {"error": "Cache statistics unavailable"}

        stats = {
            "embedding_services": embedding_stats,
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
    description="Clean up expired embedding service instances and cache entries",
)
async def cleanup_resources(current_user: User = Depends(require_admin)):
    """Clean up expired system resources."""
    try:
        before_stats = get_embedding_service_stats()

        cleanup_embedding_services()

        after_stats = get_embedding_service_stats()

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
    description="Clear all embedding service instances and cached data. Use with caution in production.",
)
async def clear_all_cache(current_user: User = Depends(require_admin)):
    """Clear all cached resources."""
    try:
        before_stats = get_embedding_service_stats()

        clear_embedding_service_cache()

        after_stats = get_embedding_service_stats()

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
    "/heap-stats",
    summary="Python heap + RSS diagnostics",
    description=(
        "Returns gc.get_stats(), top object types by count, asyncio task "
        "inventory, and current RSS. If tracemalloc is enabled, returns the "
        "top-N allocators by file:line. Admin only."
    ),
)
async def get_heap_stats(
    top_n: int = Query(25, ge=1, le=200, description="Top-N objects/types/tasks to include"),
    include_objects: bool = Query(True, description="Include gc.get_objects() type inventory"),
    current_user: User = Depends(require_admin),
):
    """Diagnose Python heap retention in-process (SHU-731).

    Lightweight enough to call periodically from dashboards (~30-80 ms on a
    460k-object heap). The tracemalloc section is empty unless tracing has
    been started via ``POST /resources/heap-stats/tracemalloc/start``.
    """
    controller = get_tracemalloc_controller()
    stats = {
        "rss_bytes": current_rss_bytes(),
        "gc": {
            "stats": gc.get_stats(),
            "counts": gc.get_count(),
            "threshold": gc.get_threshold(),
        },
        "asyncio": asyncio_task_inventory(limit=top_n),
        "tracemalloc": controller.info(),
    }
    if include_objects:
        stats["top_types"] = top_object_types(limit=top_n)
    if controller.is_enabled():
        try:
            stats["tracemalloc_top"] = controller.top_stats(limit=top_n)
        except RuntimeError as exc:
            stats["tracemalloc_top_error"] = str(exc)
    return ShuResponse.success(data=stats)


@router.post(
    "/heap-stats/trim",
    summary="Force gc.collect() + malloc_trim(0)",
    description=(
        "Runs a GC cycle and returns freed glibc arena pages to the kernel "
        "via malloc_trim(0). Returns before/after RSS. Admin only."
    ),
)
async def force_trim(
    run_gc: bool = Query(True, description="Run gc.collect() before trim"),
    current_user: User = Depends(require_admin),
):
    result = trim_memory(run_gc=run_gc)
    logger.info(
        "heap_stats_trim",
        extra={
            "user": getattr(current_user, "email", None),
            **result.to_dict(),
        },
    )
    return ShuResponse.success(data=result.to_dict())


@router.post(
    "/heap-stats/tracemalloc/start",
    summary="Start tracemalloc tracing",
    description="Enable tracemalloc with N-frame tracebacks. Admin only.",
)
async def tracemalloc_start(
    nframes: int = Query(1, ge=1, le=25, description="Frames of traceback per allocation"),
    current_user: User = Depends(require_admin),
):
    info = get_tracemalloc_controller().start(nframes=nframes)
    return ShuResponse.success(data=info)


@router.post(
    "/heap-stats/tracemalloc/stop",
    summary="Stop tracemalloc tracing",
)
async def tracemalloc_stop(current_user: User = Depends(require_admin)):
    return ShuResponse.success(data=get_tracemalloc_controller().stop())


@router.post(
    "/heap-stats/tracemalloc/snapshot",
    summary="Take a tracemalloc snapshot",
    description=(
        "Stores a baseline snapshot so subsequent snapshots can be diffed "
        "against it. Use case: snapshot before a workload, run the workload, "
        "take another snapshot to see exactly what the workload retained."
    ),
)
async def tracemalloc_snapshot(
    label: str | None = Query(None, description="Human label stored with the snapshot"),
    current_user: User = Depends(require_admin),
):
    controller = get_tracemalloc_controller()
    try:
        controller.snapshot(label=label, keep_as_baseline=True)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ShuResponse.success(data=controller.info())


@router.get(
    "/heap-stats/tracemalloc/diff",
    summary="Diff current heap against the stored tracemalloc baseline",
    description=(
        "Compares a fresh snapshot against the last baseline snapshot, "
        "returning the top-N allocators by byte delta. 400 if no baseline."
    ),
)
async def tracemalloc_diff(
    top_n: int = Query(25, ge=1, le=200),
    group_by: str = Query("lineno", regex="^(lineno|filename|traceback)$"),
    current_user: User = Depends(require_admin),
):
    controller = get_tracemalloc_controller()
    try:
        diff = controller.top_diff(limit=top_n, group_by=group_by)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ShuResponse.success(data={"top_diff": diff, "info": controller.info()})


@router.get(
    "/health",
    summary="Check resource health",
    description="Check if system resource usage is within healthy limits",
)
async def check_resource_health(current_user: User = Depends(get_current_user)):
    """Check system resource health and provide recommendations."""
    try:
        stats = get_embedding_service_stats()

        active_instances = stats["active_instances"]
        max_instances = stats["max_instances"]

        health_status = "healthy"
        recommendations = []

        if active_instances >= max_instances:
            health_status = "warning"
            recommendations.append("Maximum embedding service instances reached. Consider cleanup.")
        elif active_instances >= max_instances * 0.8:
            health_status = "caution"
            recommendations.append("Embedding service instances approaching limit.")

        old_instances = 0
        for instance_info in stats.get("instances", {}).values():
            if instance_info.get("age_seconds", 0) > 7200:  # 2 hours
                old_instances += 1

        if old_instances > 0:
            recommendations.append(
                f"{old_instances} embedding service instances are over 2 hours old. Consider cleanup."
            )

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
