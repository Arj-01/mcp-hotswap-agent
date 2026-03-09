"""Tests for the parameter name correction heuristic in RouterAgent."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import pytest
import pytest_asyncio

from agents.config import Settings
from agents.mcp_client import MCPClient
from agents.router_agent import RouterAgent
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
async def mcp_client(registry):
    return MCPClient(registry)


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


# ---------------------------------------------------------------- helpers


def _make_ollama_resp(text: str) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"response": text}
    resp.raise_for_status.return_value = None
    return resp


def _patch_ollama(*responses):
    mock_instance = AsyncMock()
    if len(responses) == 1:
        mock_instance.post.return_value = responses[0]
    else:
        mock_instance.post.side_effect = list(responses)
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=False)
    return patch("agents.router_agent.httpx.AsyncClient", return_value=mock_instance)


async def _register_search_tool(registry):
    await registry.register_server(
        "web",
        "servers/web_research_server.py",
        [
            {
                "name": "search_web",
                "description": "Search the web",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        ],
    )


async def _register_convert_tool(registry):
    await registry.register_server(
        "calculator",
        "servers/calculator_server.py",
        [
            {
                "name": "unit_convert",
                "description": "Convert units",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "number"},
                        "from_unit": {"type": "string"},
                        "to_unit": {"type": "string"},
                    },
                    "required": ["value", "from_unit", "to_unit"],
                },
            },
        ],
    )


# ---------------------------------------------------------------- tests


class TestParamCorrection:

    @pytest.mark.asyncio
    async def test_wrong_name_corrected_when_count_matches(self, router, registry, mcp_client):
        """LLM sends {"q": "news"} but tool expects {"query": "..."} → corrected."""
        await _register_search_tool(registry)
        mcp_client.call_tool = AsyncMock(return_value="search results here")

        # LLM uses wrong param name "q" instead of "query"
        ollama_resp = _make_ollama_resp(
            json.dumps([{"tool": "search_web", "params": {"q": "latest news"}}])
        )

        with _patch_ollama(ollama_resp):
            resp = await router.route_query("search for latest news", "test-correct")

        # call_tool should have been called with corrected param name
        mcp_client.call_tool.assert_called_once_with("search_web", {"query": "latest news"})
        assert resp.tool_results[0]["result"] == "search results here"

    @pytest.mark.asyncio
    async def test_correct_names_unchanged(self, router, registry, mcp_client):
        """LLM sends {"query": "news"} — already correct, no correction needed."""
        await _register_search_tool(registry)
        mcp_client.call_tool = AsyncMock(return_value="results")

        ollama_resp = _make_ollama_resp(
            json.dumps([{"tool": "search_web", "params": {"query": "news"}}])
        )

        with _patch_ollama(ollama_resp):
            await router.route_query("search news", "test-noop")

        mcp_client.call_tool.assert_called_once_with("search_web", {"query": "news"})

    @pytest.mark.asyncio
    async def test_no_correction_when_count_differs(self, router, registry, mcp_client):
        """LLM sends 1 param but tool expects 3 → no correction applied."""
        await _register_convert_tool(registry)
        mcp_client.call_tool = AsyncMock(return_value="result")

        # LLM only sends 1 param for a 3-param tool
        ollama_resp = _make_ollama_resp(
            json.dumps([{"tool": "unit_convert", "params": {"v": 100}}])
        )

        with _patch_ollama(ollama_resp):
            await router.route_query("convert 100 km to miles", "test-mismatch")

        # params should be passed as-is (no correction)
        mcp_client.call_tool.assert_called_once_with("unit_convert", {"v": 100})

    @pytest.mark.asyncio
    async def test_no_correction_when_partial_match(self, router, registry, mcp_client):
        """LLM sends {"value": 100, "src": "km", "dst": "miles"} — value matches, no correction."""
        await _register_convert_tool(registry)
        mcp_client.call_tool = AsyncMock(return_value="result")

        # "value" matches an expected param, so correction should NOT fire
        ollama_resp = _make_ollama_resp(
            json.dumps([{"tool": "unit_convert", "params": {"value": 100, "src": "km", "dst": "miles"}}])
        )

        with _patch_ollama(ollama_resp):
            await router.route_query("convert 100 km to miles", "test-partial")

        # params should be passed as-is
        mcp_client.call_tool.assert_called_once_with(
            "unit_convert", {"value": 100, "src": "km", "dst": "miles"}
        )
