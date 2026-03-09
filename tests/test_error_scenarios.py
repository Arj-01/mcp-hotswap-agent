"""Tests for error handling and resilience paths in RouterAgent."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import httpx
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


CALC_TOOLS = [
    {
        "name": "calculate",
        "description": "Evaluate a math expression",
        "inputSchema": {
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
    },
    {
        "name": "percentage",
        "description": "Calculate percentage",
        "inputSchema": {
            "type": "object",
            "properties": {
                "value": {"type": "number"},
                "percent": {"type": "number"},
            },
            "required": ["value", "percent"],
        },
    },
]


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


def _patch_ollama_error(exc):
    mock_instance = AsyncMock()
    mock_instance.post.side_effect = exc
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=False)
    return patch("agents.router_agent.httpx.AsyncClient", return_value=mock_instance)


async def _seed_calculator(registry):
    await registry.register_server("calculator", "servers/calculator_server.py", CALC_TOOLS)


# ---------------------------------------------------------------- tests


class TestOllamaOffline:

    @pytest.mark.asyncio
    async def test_connect_error_returns_offline_message(self, router):
        with _patch_ollama_error(httpx.ConnectError("Connection refused")):
            resp = await router.route_query("hello", "test-offline")

        assert "offline" in resp.answer.lower()
        assert resp.tools_used == []

    @pytest.mark.asyncio
    async def test_timeout_returns_timeout_message(self, router):
        with _patch_ollama_error(httpx.ReadTimeout("timed out")):
            resp = await router.route_query("hello", "test-timeout")

        assert "timed out" in resp.answer.lower()
        assert resp.tools_used == []


class TestBadLLMOutput:

    @pytest.mark.asyncio
    async def test_garbage_json_retries_then_falls_back(self, router):
        """When LLM returns unparseable text twice, fall back to direct answer."""
        garbage1 = _make_ollama_resp("this is not json at all")
        garbage2 = _make_ollama_resp("still not json!!!")
        fallback = _make_ollama_resp("I can help with that. The answer is 42.")

        with _patch_ollama(garbage1, garbage2, fallback):
            resp = await router.route_query("what is the meaning of life?", "test-garbage")

        assert resp.tools_used == []
        assert "42" in resp.answer


class TestToolExecutionErrors:

    @pytest.mark.asyncio
    async def test_tool_exception_returns_error_string(self, router, registry, mcp_client):
        """When call_tool raises, result should contain the error message."""
        await _seed_calculator(registry)
        mcp_client.call_tool = AsyncMock(side_effect=RuntimeError("connection reset"))

        ollama_resp = _make_ollama_resp(
            json.dumps([{"tool": "calculate", "params": {"expression": "1/0"}}])
        )

        with _patch_ollama(ollama_resp):
            resp = await router.route_query("calculate 1/0", "test-err")

        assert "Tool error" in resp.tool_results[0]["result"]
        assert "connection reset" in resp.tool_results[0]["result"]

    @pytest.mark.asyncio
    async def test_synthesis_failure_falls_back_to_concatenation(self, router, registry, mcp_client):
        """When synthesis LLM call fails, results should be concatenated."""
        await _seed_calculator(registry)

        # Mock call_tool to return different results per call
        mcp_client.call_tool = AsyncMock(side_effect=["4", "50%"])

        # First call: routing → 2 tools. Second call: synthesis → error
        routing_resp = _make_ollama_resp(
            json.dumps([
                {"tool": "calculate", "params": {"expression": "2+2"}},
                {"tool": "percentage", "params": {"value": 200, "percent": 25}},
            ])
        )
        # Synthesis raises an error
        synthesis_error = MagicMock()
        synthesis_error.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock()
        )

        with _patch_ollama(routing_resp, synthesis_error):
            resp = await router.route_query("2+2 and 25% of 200", "test-synth")

        # Should fall back to "**tool**: result" concatenation
        assert "**calculate**" in resp.answer
        assert "**percentage**" in resp.answer
        assert len(resp.tool_results) == 2
