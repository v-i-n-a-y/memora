"""Lightweight REST API for clmux daemon integration.

Registers /api/v1/memory/health and /api/v1/memory/search as custom routes
on the FastMCP server so the Zig-based clmux daemon can call them via curl
without needing MCP framing.

Usage:
    from .http_api import register_rest_routes
    register_rest_routes(mcp)
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from starlette.requests import Request
from starlette.responses import JSONResponse

from .storage import connect, get_statistics, hybrid_search

logger = logging.getLogger(__name__)


def register_rest_routes(mcp) -> None:
    """Register REST endpoints on the FastMCP server instance."""

    @mcp.custom_route("/api/v1/memory/health", methods=["GET"])
    async def memory_health(request: Request) -> JSONResponse:
        """Lightweight health check."""
        try:
            conn = connect()
            try:
                stats = get_statistics(conn)
                count = stats.get("total_memories", 0)
            finally:
                conn.close()
            return JSONResponse({"status": "ok", "memory_count": count})
        except Exception as exc:
            logger.error("health check failed: %s", exc, exc_info=True)
            return JSONResponse(
                {"status": "error", "message": str(exc)}, status_code=503
            )

    @mcp.custom_route("/api/v1/memory/search", methods=["POST"])
    async def memory_search(request: Request) -> JSONResponse:
        """Hybrid search proxy for clmux daemon."""
        try:
            body: Dict[str, Any] = await request.json()
        except Exception:
            return JSONResponse(
                {"error": "invalid_json", "message": "Request body must be valid JSON"},
                status_code=400,
            )

        query = body.get("query")
        if not query or not isinstance(query, str):
            return JSONResponse(
                {
                    "error": "invalid_input",
                    "message": "query is required and must be a string",
                },
                status_code=400,
            )

        top_k = body.get("top_k", 7)
        try:
            top_k = int(top_k)
            if top_k <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return JSONResponse(
                {"error": "invalid_input", "message": "top_k must be a positive integer"},
                status_code=400,
            )

        preview_chars = body.get("preview_chars", 300)
        try:
            preview_chars = int(preview_chars)
            if preview_chars <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return JSONResponse(
                {"error": "invalid_input", "message": "preview_chars must be a positive integer"},
                status_code=400,
            )

        tags_any = body.get("tags_any")
        if tags_any is not None:
            if not isinstance(tags_any, list) or not all(isinstance(t, str) for t in tags_any):
                return JSONResponse(
                    {"error": "invalid_input", "message": "tags_any must be a list of strings or null"},
                    status_code=400,
                )

        content_mode = body.get("content_mode", "preview")
        if content_mode not in ("preview", "full"):
            return JSONResponse(
                {"error": "invalid_input", "message": "content_mode must be \"preview\" or \"full\""},
                status_code=400,
            )

        try:
            conn = connect()
            try:
                results = hybrid_search(
                    conn,
                    query,
                    semantic_weight=0.6,
                    top_k=top_k,
                    min_score=0.0,
                    metadata_filters=None,
                    date_from=None,
                    date_to=None,
                    tags_any=tags_any,
                    tags_all=None,
                    tags_none=None,
                )
            finally:
                conn.close()
        except Exception as exc:
            logger.error("search failed: %s", exc, exc_info=True)
            return JSONResponse(
                {
                    "error": "search_failed",
                    "message": "Search failed. Check server logs.",
                },
                status_code=500,
            )

        # Apply content projection
        if content_mode == "preview":
            for entry in results:
                if "memory" in entry:
                    mem = entry["memory"]
                    full = mem.pop("content", "") or ""
                    if len(full) > preview_chars:
                        mem["content_preview"] = full[:preview_chars] + "\u2026"
                    else:
                        mem["content_preview"] = full

        return JSONResponse({"count": len(results), "results": results})
