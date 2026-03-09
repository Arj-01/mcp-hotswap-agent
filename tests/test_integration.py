"""Integration tests — full API flow with fakeredis, mocked Ollama, and mocked MCP."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from agents.chat_history import ChatHistory
from agents.config import Settings
from agents.mcp_client import MCPClient
from agents.router_agent import AgentResponse, RouterAgent
from agents.tool_registry import ToolRegistry


# ---------------------------------------------------------------- fixtures


@pytest_asyncio.fixture
async def redis():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest_asyncio.fixture
async def registry(redis):
    reg = ToolRegistry.__new__(ToolRegistry)
    reg.redis_url = "redis://fake"
    reg._redis = redis
    return reg


@pytest_asyncio.fixture
async def history(redis):
    hist = ChatHistory.__new__(ChatHistory)
    hist.redis_url = "redis://fake"
    hist._redis = redis
    return hist


@pytest_asyncio.fixture
async def mcp_client(registry):
    client = MCPClient(registry)
    return client


@pytest_asyncio.fixture
async def settings():
    return Settings(
        ollama_base_url="http://mock-ollama:11434",
        redis_url="redis://fake",
        mcp_server_dir="servers",
    )


@pytest_asyncio.fixture
async def router(registry, mcp_client, settings):
    return RouterAgent(registry, mcp_client, settings)


@pytest_asyncio.fixture
async def app_client(registry, history, mcp_client, router):
    """ASGI test client with all dependencies injected (no real servers)."""
    from agents.main import app
    import agents.main as main_mod

    # Inject test doubles into the module-level globals
    main_mod._registry = registry
    main_mod._history = history
    main_mod._mcp = mcp_client
    main_mod._router = router
    main_mod._settings = Settings(
        ollama_base_url="http://mock-ollama:11434",
        redis_url="redis://fake",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ---------------------------------------------------------------- helpers


def _make_ollama_resp(text: str) -> MagicMock:
    """Create a mock httpx Response with .json() returning Ollama format."""
    resp = MagicMock()
    resp.json.return_value = {"response": text}
    resp.raise_for_status.return_value = None
    return resp


async def _seed_calculator(registry):
    """Register a fake calculator server + tools in the registry."""
    await registry.register_server(
        "calculator",
        "servers/calculator_server.py",
        [
            {
                "name": "calculate",
                "description": "Evaluate a math expression",
                "inputSchema": {
                    "type": "object",
                    "properties": {"expression": {"type": "string"}},
                    "required": ["expression"],
                },
            },
        ],
    )


def _patch_ollama(*responses: MagicMock):
    """Context manager that patches httpx.AsyncClient to return mock responses."""
    mock_instance = AsyncMock()
    if len(responses) == 1:
        mock_instance.post.return_value = responses[0]
    else:
        mock_instance.post.side_effect = list(responses)
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=False)

    patcher = patch("agents.router_agent.httpx.AsyncClient", return_value=mock_instance)
    return patcher


# -------------------------------------------------------- full query flow


class TestQueryFlow:
    """POST /query — agent routes query → tool executes → result returned."""

    @pytest.mark.asyncio
    async def test_query_routes_to_tool_and_returns_result(
        self, app_client, registry, mcp_client
    ):
        await _seed_calculator(registry)

        # Mock Ollama: returns a tool call for "calculate"
        ollama_resp = _make_ollama_resp(
            json.dumps([{"tool": "calculate", "params": {"expression": "2+2"}}])
        )

        # Mock MCP call_tool to return a result
        mcp_client.call_tool = AsyncMock(return_value="4")

        with _patch_ollama(ollama_resp):
            resp = await app_client.post(
                "/query", json={"query": "what is 2+2?", "session_id": "test-flow"}
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "what is 2+2?"
        assert "calculate" in data["tools_used"]
        assert data["tool_results"][0]["result"] == "4"
        assert data["answer"] == "4"
        assert data["total_duration_ms"] > 0

    @pytest.mark.asyncio
    async def test_query_without_tools_returns_direct_answer(
        self, app_client, registry
    ):
        """When LLM returns [] (no tools), the agent answers directly."""
        routing_resp = _make_ollama_resp("[]")
        direct_resp = _make_ollama_resp("Hello! How can I help?")

        with _patch_ollama(routing_resp, direct_resp):
            resp = await app_client.post(
                "/query", json={"query": "hello", "session_id": "test-direct"}
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["tools_used"] == []
        assert data["answer"] == "Hello! How can I help?"


# --------------------------------------------------- plugin registration


class TestPluginRegistration:
    """Register a calculator → query math → correct answer."""

    @pytest.mark.asyncio
    async def test_register_then_query_uses_tool(
        self, app_client, registry, mcp_client
    ):
        await _seed_calculator(registry)

        # Verify tool is discoverable
        tools_resp = await app_client.get("/tools")
        assert tools_resp.status_code == 200
        tool_names = [t["tool_name"] for t in tools_resp.json()]
        assert "calculate" in tool_names

        # Query math — mock LLM picks calculate, mock MCP returns result
        mcp_client.call_tool = AsyncMock(return_value="42")

        ollama_resp = _make_ollama_resp(
            json.dumps([{"tool": "calculate", "params": {"expression": "6*7"}}])
        )

        with _patch_ollama(ollama_resp):
            resp = await app_client.post(
                "/query", json={"query": "what is 6*7?", "session_id": "test-calc"}
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "calculate" in data["tools_used"]
        assert data["answer"] == "42"

    @pytest.mark.asyncio
    async def test_deregister_server_removes_tools(self, app_client, registry):
        await _seed_calculator(registry)

        # Verify tool exists
        tools = await app_client.get("/tools")
        assert any(t["tool_name"] == "calculate" for t in tools.json())

        # Deregister
        await registry.deregister_server("calculator")

        # Tool should be gone
        tools = await app_client.get("/tools")
        assert not any(t["tool_name"] == "calculate" for t in tools.json())


# --------------------------------------------------------- chat history


class TestChatHistory:
    """Send queries → fetch history → all present."""

    @pytest.mark.asyncio
    async def test_three_queries_appear_in_history(
        self, app_client, registry, mcp_client
    ):
        session = "test-history"
        queries = ["first query", "second query", "third query"]

        # Build mock responses: each query needs routing (→[]) + direct answer
        responses = []
        for q in queries:
            responses.append(_make_ollama_resp("[]"))
            responses.append(_make_ollama_resp(f"Answer to: {q}"))

        with _patch_ollama(*responses):
            for q in queries:
                resp = await app_client.post(
                    "/query", json={"query": q, "session_id": session}
                )
                assert resp.status_code == 200

        # Fetch history
        hist_resp = await app_client.get(f"/chat/history/{session}")
        assert hist_resp.status_code == 200
        messages = hist_resp.json()

        # 3 queries × 2 messages each (user + assistant) = 6
        assert len(messages) == 6

        user_messages = [m["content"] for m in messages if m["role"] == "user"]
        assert user_messages == queries

        assistant_messages = [m["content"] for m in messages if m["role"] == "assistant"]
        for i, q in enumerate(queries):
            assert f"Answer to: {q}" == assistant_messages[i]

    @pytest.mark.asyncio
    async def test_clear_history(self, app_client, history):
        session = "test-clear"
        await history.add_message(session, "user", "hello")
        await history.add_message(session, "assistant", "hi")

        resp = await app_client.delete(f"/chat/history/{session}")
        assert resp.status_code == 200
        assert resp.json() == {"cleared": session}

        hist = await app_client.get(f"/chat/history/{session}")
        assert hist.json() == []

    @pytest.mark.asyncio
    async def test_separate_sessions_isolated(self, app_client, history):
        await history.add_message("session-a", "user", "msg for A")
        await history.add_message("session-b", "user", "msg for B")

        resp_a = await app_client.get("/chat/history/session-a")
        resp_b = await app_client.get("/chat/history/session-b")

        assert len(resp_a.json()) == 1
        assert resp_a.json()[0]["content"] == "msg for A"
        assert len(resp_b.json()) == 1
        assert resp_b.json()[0]["content"] == "msg for B"


# ---------------------------------------------------------- tools endpoint


class TestToolsEndpoint:
    """GET /tools shows all registered tools."""

    @pytest.mark.asyncio
    async def test_tools_endpoint_lists_all_tools(self, app_client, registry):
        # Register two servers with different tools
        await registry.register_server(
            "calculator",
            "servers/calculator_server.py",
            [
                {"name": "calculate", "description": "Math eval", "inputSchema": {}},
                {"name": "percentage", "description": "Percentage calc", "inputSchema": {}},
            ],
        )
        await registry.register_server(
            "notes",
            "servers/notes_creator_server.py",
            [
                {"name": "create_note", "description": "Create a note", "inputSchema": {}},
                {"name": "list_notes", "description": "List all notes", "inputSchema": {}},
            ],
        )

        resp = await app_client.get("/tools")
        assert resp.status_code == 200
        tools = resp.json()
        tool_names = {t["tool_name"] for t in tools}
        assert tool_names == {"calculate", "percentage", "create_note", "list_notes"}

    @pytest.mark.asyncio
    async def test_tools_endpoint_empty_when_no_servers(self, app_client):
        resp = await app_client.get("/tools")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_tools_include_schema_and_server(self, app_client, registry):
        await _seed_calculator(registry)

        resp = await app_client.get("/tools")
        tool = resp.json()[0]
        assert tool["server_name"] == "calculator"
        assert tool["tool_name"] == "calculate"
        assert "properties" in tool["schema"]


# ------------------------------------------------------- health endpoint


class TestHealth:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, app_client, registry):
        await _seed_calculator(registry)

        resp = await app_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["servers"] == 1
        assert data["tools"] == 1
        assert data["redis"] == "ok"

    @pytest.mark.asyncio
    async def test_health_empty_state(self, app_client):
        resp = await app_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["servers"] == 0
        assert data["tools"] == 0


# -------------------------------------------------------- servers endpoint


class TestServersEndpoint:
    @pytest.mark.asyncio
    async def test_servers_lists_registered_servers(self, app_client, registry):
        await _seed_calculator(registry)

        resp = await app_client.get("/servers")
        assert resp.status_code == 200
        servers = resp.json()
        assert len(servers) == 1
        assert servers[0]["name"] == "calculator"
        assert servers[0]["status"] == "active"
        assert servers[0]["tool_count"] == 1
