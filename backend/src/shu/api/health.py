"""Health check API endpoints for Shu.

This module provides comprehensive health monitoring endpoints
for application health, database connectivity, and service status.
"""

import os
import time

import psutil
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.models import User
from ..auth.rbac import require_power_user
from ..core.config import get_settings_instance
from ..core.logging import get_logger
from ..core.response import ShuResponse
from ..services.system_status import check_db_release
from .dependencies import get_db

logger = get_logger(__name__)
router = APIRouter(prefix="/health", tags=["health"])

settings = get_settings_instance()


@router.get("", summary="Health check", description="Comprehensive health check for Shu.")
async def health_check(
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
):
    """Comprehensive health check endpoint.

    Checks database connectivity, memory usage, and overall system health.
    Returns detailed health information for monitoring and debugging.

    Args:
        db: Database session dependency

    Returns:
        JSONResponse with single-envelope structure containing health status

    """
    logger.info("Health check requested")

    # Initialize health data
    health_data = {
        "status": "healthy",
        "timestamp": time.time(),
        "version": settings.version,
        "environment": settings.environment,
        "checks": {},
    }

    # Database health check
    try:
        logger.debug(f"Health check - db type: {type(db)}")
        result = await db.execute(text("SELECT 1"))
        logger.debug(f"Health check - result type: {type(result)}")
        row = result.first()  # .first() is not async, it returns a Row object directly
        logger.debug(f"Health check - row type: {type(row)}")
        if row is not None:
            health_data["checks"]["database"] = {"status": "healthy", "response_time": "fast"}
        else:
            health_data["checks"]["database"] = {
                "status": "unhealthy",
                "error": "No result from SELECT 1",
            }
            health_data["status"] = "unhealthy"
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        health_data["checks"]["database"] = {"status": "unhealthy", "error": str(e)}
        health_data["status"] = "unhealthy"

    # Memory usage check
    try:
        memory = psutil.virtual_memory()
        health_data["checks"]["memory"] = {
            "status": "healthy" if memory.percent < 90 else "warning",
            "usage_percent": memory.percent,
            "available_mb": memory.available // (1024 * 1024),
            "total_mb": memory.total // (1024 * 1024),
        }
        if memory.percent >= 90:
            health_data["status"] = "warning"
    except Exception as e:
        logger.error(f"Memory health check failed: {e}")
        health_data["checks"]["memory"] = {"status": "unknown", "error": str(e)}

    # Disk usage check
    try:
        disk = psutil.disk_usage("/")
        health_data["checks"]["disk"] = {
            "status": "healthy" if disk.percent < 90 else "warning",
            "usage_percent": disk.percent,
            "free_gb": disk.free // (1024 * 1024 * 1024),
            "total_gb": disk.total // (1024 * 1024 * 1024),
        }
        if disk.percent >= 90:
            health_data["status"] = "warning"
    except Exception as e:
        logger.error(f"Disk health check failed: {e}")
        health_data["checks"]["disk"] = {"status": "unknown", "error": str(e)}

    # Process information
    try:
        process = psutil.Process()
        health_data["checks"]["process"] = {
            "status": "healthy",
            "pid": process.pid,
            "cpu_percent": process.cpu_percent(),
            "memory_mb": process.memory_info().rss // (1024 * 1024),
            "create_time": process.create_time(),
        }
    except Exception as e:
        logger.error(f"Process health check failed: {e}")
        health_data["checks"]["process"] = {"status": "unknown", "error": str(e)}

    # Configuration check
    try:
        health_data["checks"]["configuration"] = {
            "status": "healthy",
            "database_url_configured": bool(settings.database_url),
            "debug_mode": settings.debug,
            "log_level": settings.log_level,
            "api_host": settings.api_host,
            "api_port": settings.api_port,
        }
    except Exception as e:
        logger.error(f"Configuration health check failed: {e}")
        health_data["checks"]["configuration"] = {"status": "unhealthy", "error": str(e)}
        health_data["status"] = "unhealthy"

    # Determine overall status
    overall_status = 200 if health_data["status"] in ["healthy", "warning"] else 503

    logger.info(f"Health check completed with status: {health_data['status']}")

    return ShuResponse.success(data=health_data, status_code=overall_status)


@router.get("/readiness", summary="Readiness probe", description="Kubernetes readiness probe endpoint.")
async def readiness_probe(db: AsyncSession = Depends(get_db)):
    """Readiness probe for Kubernetes.

    Checks if the application is ready to serve requests.
    This includes database connectivity and essential services.

    Returns:
        JSONResponse with single-envelope structure containing readiness status

    Raises:
        HTTPException: If application is not ready (503)

    """
    logger.debug("Performing readiness check")

    start_time = time.time()
    readiness_status = {"ready": True, "timestamp": time.time(), "checks": {}, "errors": []}

    # Check database connectivity
    try:
        await db.execute(text("SELECT 1"))
        readiness_status["checks"]["database"] = "ready"
    except Exception as e:
        readiness_status["ready"] = False
        readiness_status["checks"]["database"] = "not_ready"
        readiness_status["errors"].append(f"Database not ready: {e!s}")

    # DB schema baseline check via shared service
    if settings.db_release:
        release_check = await check_db_release(db, settings.db_release)
        if release_check.get("error"):
            readiness_status["ready"] = False
            readiness_status["checks"]["db_release"] = "error"
            readiness_status["errors"].append(f"DB release check error: {release_check['error']}")
        elif release_check.get("mismatch"):
            readiness_status["ready"] = False
            readiness_status["checks"]["db_release"] = "mismatch"
            readiness_status["errors"].append(
                f"DB release mismatch: expected {release_check['expected']}, current {release_check['current']}"
            )
        else:
            readiness_status["checks"]["db_release"] = "ok"

    execution_time = time.time() - start_time
    readiness_status["execution_time"] = execution_time

    if not readiness_status["ready"]:
        logger.warning(
            "Readiness check failed",
            extra={"errors": readiness_status["errors"], "execution_time": execution_time},
        )
        return ShuResponse.error(
            message="Application not ready",
            code="READINESS_CHECK_FAILED",
            details=readiness_status,
            status_code=503,
        )

    logger.debug("Readiness check passed", extra={"execution_time": execution_time})

    return ShuResponse.success(readiness_status)


@router.get("/liveness", summary="Liveness probe", description="Kubernetes liveness probe endpoint.")
async def liveness_probe():
    """Liveness probe for Kubernetes.

    Simple check to determine if the application is alive.
    This is a lightweight check that should always succeed
    unless the application is completely dead.

    Returns:
        JSONResponse with single-envelope structure containing liveness status

    """
    logger.debug("Performing liveness check")

    liveness_status = {
        "alive": True,
        "timestamp": time.time(),
        "pid": os.getpid(),
        "version": settings.version,
    }

    logger.debug("Liveness check passed")

    return ShuResponse.success(liveness_status)


@router.get(
    "/database",
    summary="Database health check",
    description="Detailed database connectivity and performance check.",
)
async def database_health_check(
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
):
    """Detailed database health check.

    Performs comprehensive database connectivity and performance checks.
    Useful for debugging database issues and monitoring performance.

    Args:
        db: Database session dependency

    Returns:
        JSONResponse with single-envelope structure containing database health status

    """
    logger.info("Database health check requested")

    db_health = {"status": "healthy", "timestamp": time.time(), "checks": {}, "performance": {}}

    # Basic connectivity check
    try:
        start_time = time.time()
        result = await db.execute(text("SELECT 1"))
        row = result.first()
        response_time = (time.time() - start_time) * 1000  # Convert to milliseconds

        if row is not None:
            db_health["checks"]["connectivity"] = {
                "status": "healthy",
                "response_time_ms": round(response_time, 2),
            }
            db_health["performance"]["query_time_ms"] = round(response_time, 2)
        else:
            db_health["checks"]["connectivity"] = {
                "status": "unhealthy",
                "error": "No result from connectivity test",
            }
            db_health["status"] = "unhealthy"
    except Exception as e:
        logger.error(f"Database connectivity check failed: {e}")
        db_health["checks"]["connectivity"] = {"status": "unhealthy", "error": str(e)}
        db_health["status"] = "unhealthy"

    # Connection pool check
    try:
        # This would need to be implemented based on your database connection pool
        # For now, we'll assume it's healthy if we can execute a query
        db_health["checks"]["connection_pool"] = {
            "status": "healthy",
            "note": "Connection pool status not implemented",
        }
    except Exception as e:
        logger.error(f"Connection pool check failed: {e}")
        db_health["checks"]["connection_pool"] = {"status": "unknown", "error": str(e)}

    # Database version check
    try:
        result = await db.execute(text("SELECT version()"))
        row = result.first()
        if row:
            db_health["checks"]["version"] = {"status": "healthy", "version": str(row[0])}
        else:
            db_health["checks"]["version"] = {
                "status": "unknown",
                "error": "Could not retrieve database version",
            }
    except Exception as e:
        logger.error(f"Database version check failed: {e}")
        db_health["checks"]["version"] = {"status": "unknown", "error": str(e)}

    # Determine overall status
    overall_status = 200 if db_health["status"] == "healthy" else 503

    logger.info(f"Database health check completed with status: {db_health['status']}")

    return ShuResponse.success(data=db_health, status_code=overall_status)
