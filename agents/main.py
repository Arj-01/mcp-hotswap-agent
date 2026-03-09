"""FastAPI application — HTTP interface to the Router Agent."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from agents import metrics as m
from agents.chat_history import ChatHistory
from agents.config import Settings
from agents.mcp_client import MCPClient
from agents.router_agent import AgentResponse, RouterAgent
from agents.tool_registry import ToolRegistry

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- shared state
# Populated in lifespan, available to all endpoint handlers.

_settings: Settings
_registry: ToolRegistry
_history: ChatHistory
_mcp: MCPClient
_router: RouterAgent


# ---------------------------------------------------------------- lifespan

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _settings, _registry, _history, _mcp, _router

    # --- startup ---
    _settings = Settings()

    _registry = ToolRegistry(_settings.redis_url)
    await _registry.connect()

    _history = ChatHistory(_settings.redis_url)
    await _history.connect()

    _mcp = MCPClient(_registry)
    await _mcp.discover_and_register(
        _settings.mcp_server_dir,
        exclude=["calculator_server.py", "daily_digest_server.py"],
    )
    # Scan excluded servers so they can be hot-plugged on demand
    _mcp.scan_dormant_plugins(
        _settings.mcp_server_dir,
        exclude=["daily_digest_server.py"],
    )

    _router = RouterAgent(_registry, _mcp, _settings)

    servers = await _registry.get_all_servers()
    tools = await _registry.get_all_tools()
    m.update_server_count(len(servers))
    m.update_tool_count(len(tools))
    logger.info("MCP Assistant started: %d tools from %d servers", len(tools), len(servers))

    yield

    # --- shutdown ---
    await _mcp.disconnect_all()
    await _registry.close()
    await _history.close()
    logger.info("MCP Assistant shutdown complete")


# ---------------------------------------------------------------- app

app = FastAPI(title="MCP Multi-Agent Assistant", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- request models

class QueryRequest(BaseModel):
    query: str
    session_id: str = "default"


class RegisterServerRequest(BaseModel):
    name: str
    script_path: str


# ---------------------------------------------------------------- endpoints

@app.post("/query", response_model=AgentResponse)
async def post_query(body: QueryRequest):
    """Run a query through the Router Agent and return the structured response."""
    try:
        resp = await _router.route_query(body.query, body.session_id)
    except Exception as exc:
        m.track_query("error", 0)
        logger.error("/query error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    # Persist conversation turn
    await _history.add_message(body.session_id, "user", body.query)
    await _history.add_message(body.session_id, "assistant", resp.answer)

    # Update gauges (in case a server was hot-plugged during this query)
    servers = await _registry.get_all_servers()
    tools = await _registry.get_all_tools()
    m.update_server_count(len(servers))
    m.update_tool_count(len(tools))

    # Metrics
    m.track_query("success", resp.total_duration_ms / 1000)
    for tr in resp.tool_results:
        info = await _registry.find_tool(tr["tool"])
        server = info["server_name"] if info else "unknown"
        result_lower = tr["result"].lower()
        if "timed out" in result_lower:
            status = "timeout"
        elif "error" in result_lower:
            status = "error"
        else:
            status = "success"
        m.track_tool_call(server, tr["tool"], status, tr["duration_ms"] / 1000)

    return resp


@app.get("/tools")
async def get_tools():
    """List all tools registered in the tool registry."""
    return await _registry.get_all_tools()


@app.get("/servers")
async def get_servers():
    """List all connected MCP servers with status and tool count."""
    return await _registry.get_all_servers()


@app.post("/servers/register")
async def register_server(body: RegisterServerRequest):
    """Dynamically connect a new MCP server and register its tools."""
    try:
        result = await _mcp.connect_server(body.name, body.script_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    servers = await _registry.get_all_servers()
    tools = await _registry.get_all_tools()
    m.update_server_count(len(servers))
    m.update_tool_count(len(tools))
    return {"name": body.name, "tools_discovered": result["tools"]}


@app.delete("/servers/{name}")
async def deregister_server(name: str):
    """Disconnect an MCP server and remove its tools from the registry."""
    success = await _mcp.disconnect_server(name)
    if not success:
        raise HTTPException(status_code=404, detail=f"Server {name!r} not found")

    # Re-scan so disconnected server's tools become dormant (available for hot-swap)
    _mcp.scan_dormant_plugins(
        _settings.mcp_server_dir,
        exclude=["daily_digest_server.py"],
    )

    servers = await _registry.get_all_servers()
    tools = await _registry.get_all_tools()
    m.update_server_count(len(servers))
    m.update_tool_count(len(tools))
    return {"disconnected": name}


@app.get("/chat/history/{session_id}")
async def get_chat_history(session_id: str):
    """Return the message history for a session (up to 50 messages)."""
    return await _history.get_history(session_id)


@app.delete("/chat/history/{session_id}")
async def clear_chat_history(session_id: str):
    """Clear all messages for a session."""
    await _history.clear_history(session_id)
    return {"cleared": session_id}


@app.get("/health")
async def health():
    """Health check — returns server count, tool count, and Redis status."""
    servers = await _registry.get_all_servers()
    tools = await _registry.get_all_tools()
    try:
        await _registry.r.ping()
        redis_status = "ok"
    except Exception:
        redis_status = "error"
    return {
        "status": "ok",
        "servers": len(servers),
        "tools": len(tools),
        "redis": redis_status,
    }


@app.get("/metrics")
async def get_metrics():
    """Prometheus metrics endpoint."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
