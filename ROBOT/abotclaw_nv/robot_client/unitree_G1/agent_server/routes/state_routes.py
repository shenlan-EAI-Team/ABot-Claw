"""GET /state, /health, /cameras endpoints for G1 robot."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Response, Query
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_no_cache_headers = {
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "Pragma": "no-cache",
}


def create_router(state_agg, *args, **kwargs):
    """Create state router.

    Only state_agg and lease_mgr are used; extra positional/keyword args from
    the old multi-backend signature are accepted and ignored for compatibility.
    """
    # Extract lease_mgr from positional args (3rd positional after state_agg)
    # Old signature: create_router(state_agg, camera_backend, lease_mgr, ...)
    lease_mgr = args[1] if len(args) > 1 else kwargs.get("lease_mgr")

    @router.get("/state")
    async def get_state():
        """Get current robot state (arm joints, end-effector pose, gripper)."""
        return state_agg.state

    # NOTE: /cameras endpoints are handled by camera_routes.py
    # to ensure all frame capture goes through /code/execute

    @router.get("/health")
    async def health():
        """Server health and lease status."""
        result: dict = {"status": "ok"}
        if lease_mgr is not None:
            result["lease"] = lease_mgr.status()
        return result

    @router.get("/logs", include_in_schema=False)
    async def get_server_logs(limit: int = Query(default=100, ge=1, le=500)):
        """Get recent server logs for dashboard display."""
        from logging_config import get_log_buffer
        buf = get_log_buffer()
        if buf is None:
            return {"logs": []}
        return {"logs": buf.get_logs(limit)}

    return router
