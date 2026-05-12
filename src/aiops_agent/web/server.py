"""AIOps Agent Web Server — aiohttp HTTP API + 前端页面.

提供 REST API 和内嵌的 Chat UI 前端页面。
"""

from __future__ import annotations

import json
import logging
import uuid
from functools import partial
from pathlib import Path
from typing import Any, Optional

from aiohttp import web

from aiops_agent.core.orchestrator import AgentOrchestrator
from aiops_agent.main import create_agent

logger = logging.getLogger(__name__)

# JSON 序列化器（支持中文、datetime 等）
def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)

_json_response = partial(web.json_response, dumps=_json_dumps)

# 全局 orchestrator 实例
_orchestrator: Optional[AgentOrchestrator] = None


async def _get_orchestrator(app: web.Application) -> AgentOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = await create_agent()
    return _orchestrator


# ------------------------------------------------------------------
# API 路由
# ------------------------------------------------------------------


async def handle_chat(request: web.Request) -> web.Response:
    """POST /api/chat — 处理用户对话请求（同步模式）."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        return _json_response({"error": "Invalid JSON"}, status=400)

    user_input = body.get("message", "").strip()
    if not user_input:
        return _json_response({"error": "message 不能为空"}, status=400)

    session_id = body.get("session_id", str(uuid.uuid4()))
    user_id = body.get("user_id", "anonymous")

    orchestrator = await _get_orchestrator(request.app)

    try:
        response = await orchestrator.process_request(
            user_input=user_input,
            session_id=session_id,
            user_id=user_id,
        )
    except Exception as exc:
        logger.exception("处理请求异常")
        return _json_response(
            {"success": False, "message": str(exc), "error_code": "INTERNAL_ERROR",
             "suggestion": "请稍后重试", "data": None, "trace_id": None, "session_id": session_id},
            status=500,
        )

    return _json_response({
        "success": response.success,
        "message": response.message,
        "data": response.data,
        "error_code": response.error_code,
        "suggestion": response.suggestion,
        "trace_id": response.trace_id,
        "session_id": session_id,
    })


async def handle_chat_stream(request: web.Request) -> web.StreamResponse:
    """POST /api/chat/stream — SSE 流式响应."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        return _json_response({"error": "Invalid JSON"}, status=400)

    user_input = body.get("message", "").strip()
    if not user_input:
        return _json_response({"error": "message 不能为空"}, status=400)

    session_id = body.get("session_id", str(uuid.uuid4()))
    user_id = body.get("user_id", "anonymous")

    orchestrator = await _get_orchestrator(request.app)

    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁止 nginx 缓冲
        },
    )
    await response.prepare(request)

    async def _write_event(data: dict) -> None:
        event_type = data.pop("type", "message")
        payload = json.dumps(data, ensure_ascii=False, default=str)
        await response.write(f"event: {event_type}\ndata: {payload}\n\n".encode("utf-8"))

    try:
        async for event in orchestrator.process_request_stream(
            user_input=user_input,
            session_id=session_id,
            user_id=user_id,
        ):
            await _write_event(event)
    except Exception as exc:
        logger.exception("SSE 流式处理异常")
        await _write_event({
            "type": "error",
            "status": "failed",
            "message": str(exc),
            "session_id": session_id,
        })

    await response.write_eof()
    return response


async def handle_health(request: web.Request) -> web.Response:
    """GET /health — 健康检查."""
    return _json_response({"status": "healthy"})


async def handle_ready(request: web.Request) -> web.Response:
    """GET /ready — 就绪检查."""
    return _json_response({"status": "ready"})


async def handle_skills(request: web.Request) -> web.Response:
    """GET /api/skills — 列出可用技能（含市场展示信息）."""
    orchestrator = await _get_orchestrator(request.app)
    skills = orchestrator._skill_registry.list_skills()
    return _json_response({
        "skills": [
            {
                "name": s.skill_name,
                "description": s.description,
                "version": s.version,
                "capabilities": s.capabilities,
                "status": s.status,
                "author": s.author,
                "category": s.category,
                "icon": s.icon,
                "tags": s.tags,
                "install_count": s.install_count,
                "rating": s.rating,
                "updated_at": s.updated_at,
                "readme": s.readme,
            }
            for s in skills
        ]
    })


async def handle_index(request: web.Request) -> web.Response:
    """GET / — 返回前端页面."""
    html_path = Path(__file__).parent / "static" / "index.html"
    if html_path.exists():
        return web.Response(
            text=html_path.read_text(encoding="utf-8"),
            content_type="text/html",
        )
    return web.Response(text="AIOps Agent is running", content_type="text/plain")


async def handle_skills_page(request: web.Request) -> web.Response:
    """GET /skills — 技能市场页面."""
    html_path = Path(__file__).parent / "static" / "skills.html"
    if html_path.exists():
        return web.Response(
            text=html_path.read_text(encoding="utf-8"),
            content_type="text/html",
        )
    return web.Response(text="Skills page not found", status=404)


def create_app() -> web.Application:
    """创建 aiohttp 应用."""
    app = web.Application()

    # API 路由
    app.router.add_get("/", handle_index)
    app.router.add_get("/skills", handle_skills_page)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/ready", handle_ready)
    app.router.add_post("/api/chat", handle_chat)
    app.router.add_post("/api/chat/stream", handle_chat_stream)
    app.router.add_get("/api/skills", handle_skills)

    # 静态文件
    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.router.add_static("/static/", static_dir, name="static")

    return app


def run_server(host: str = "0.0.0.0", port: int = 8080) -> None:
    """启动 Web 服务器."""
    app = create_app()
    logger.info("AIOps Agent Web Server 启动: http://%s:%d", host, port)
    web.run_app(app, host=host, port=port, print=lambda msg: logger.info(msg))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_server()
