"""Liveness and readiness probes."""

import logging

from fastapi import APIRouter, Response, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import text

from lecture_api.deps import SessionDep, StorageDep

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """The process is up. Checks nothing else, so a database outage doesn't restart the API."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(session: SessionDep, storage: StorageDep, response: Response) -> dict[str, str]:
    """The API can serve traffic: Postgres and object storage both answer."""
    checks: dict[str, str] = {}
    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        logger.warning("readiness: database check failed", exc_info=True)
        checks["database"] = "error"
    try:
        await run_in_threadpool(storage.ping)
        checks["storage"] = "ok"
    except Exception:
        logger.warning("readiness: storage check failed", exc_info=True)
        checks["storage"] = "error"
    if any(result != "ok" for result in checks.values()):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return checks
