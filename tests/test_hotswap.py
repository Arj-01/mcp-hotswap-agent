"""Tests for the hot-swap lifecycle: dormant scan -> hot-plug -> execute -> auto-detach -> re-scan."""
import json
from pathlib import Path
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


async def _seed_calculator(registry):
    await registry.register_server("calculator_server", "servers/calculator_server.py", CALC_TOOLS)


# ---------------------------------------------------------------- tests


class TestHotPlugResponseFields:
    """Verify hotplugged_servers and per-tool hotplugged flag."""

    @pytest.mark.asyncio
    async def test_hotplugged_servers_populated(self, router, registry, mcp_client):
        """When a dormant tool is used, hotplugged_servers should list the server."""
        # Set up dormant tool
        mcp_client._dormant_tools = {
            "calculate": {
                "server": "calculator_server",
                "script_path": "servers/calculator_server.py",
                "description": "Evaluate a math expression",
                "params": ["expression"],
            }
        }

        # Mock hotplug: registers tools and returns True
        async def fake_hotplug(tool_name):
            await _seed_calculator(registry)
            mcp_client._dormant_tools.pop(tool_name, None)
            return True

        mcp_client.hotplug_for_tool = fake_hotplug
        mcp_client.call_tool = AsyncMock(return_value="4")
        mcp_client.disconnect_server = AsyncMock(return_value=True)
        mcp_client.scan_dormant_plugins = MagicMock()

        ollama_resp = _make_ollama_resp(
            json.dumps([{"tool": "calculate", "params": {"expression": "2+2"}}])
        )

        with _patch_ollama(ollama_resp):
            resp = await router.route_query("what is 2+2?", "test-hp")

        assert resp.hotplugged_servers == ["calculator_server"]

    @pytest.mark.asyncio
    async def test_hotplugged_servers_empty_for_loaded_tools(self, router, registry, mcp_client):
        """When using already-loaded tools, hotplugged_servers should be empty."""
        await _seed_calculator(registry)
        mcp_client.call_tool = AsyncMock(return_value="42")

        ollama_resp = _make_ollama_resp(
            json.dumps([{"tool": "calculate", "params": {"expression": "6*7"}}])
        )

        with _patch_ollama(ollama_resp):
            resp = await router.route_query("what is 6*7?", "test-loaded")

        assert resp.hotplugged_servers == []

    @pytest.mark.asyncio
    async def test_tool_result_hotplugged_flag_true(self, router, registry, mcp_client):
        """Individual tool results should have hotplugged=True for hot-plugged tools."""
        mcp_client._dormant_tools = {
            "calculate": {
                "server": "calculator_server",
                "script_path": "servers/calculator_server.py",
                "description": "Evaluate a math expression",
                "params": ["expression"],
            }
        }

        async def fake_hotplug(tool_name):
            await _seed_calculator(registry)
            mcp_client._dormant_tools.pop(tool_name, None)
            return True

        mcp_client.hotplug_for_tool = fake_hotplug
        mcp_client.call_tool = AsyncMock(return_value="4")
        mcp_client.disconnect_server = AsyncMock(return_value=True)
        mcp_client.scan_dormant_plugins = MagicMock()

        ollama_resp = _make_ollama_resp(
            json.dumps([{"tool": "calculate", "params": {"expression": "2+2"}}])
        )

        with _patch_ollama(ollama_resp):
            resp = await router.route_query("what is 2+2?", "test-flag")

        assert resp.tool_results[0]["hotplugged"] is True

    @pytest.mark.asyncio
    async def test_tool_result_hotplugged_flag_false(self, router, registry, mcp_client):
        """Already-loaded tools should have hotplugged=False."""
        await _seed_calculator(registry)
        mcp_client.call_tool = AsyncMock(return_value="42")

        ollama_resp = _make_ollama_resp(
            json.dumps([{"tool": "calculate", "params": {"expression": "6*7"}}])
        )

        with _patch_ollama(ollama_resp):
            resp = await router.route_query("what is 6*7?", "test-flag-f")

        assert resp.tool_results[0]["hotplugged"] is False


class TestAutoDetach:
    """Verify servers are disconnected and re-scanned after hot-plug."""

    @pytest.mark.asyncio
    async def test_disconnect_called_after_hotplug(self, router, registry, mcp_client):
        mcp_client._dormant_tools = {
            "calculate": {
                "server": "calculator_server",
                "script_path": "servers/calculator_server.py",
                "description": "Evaluate a math expression",
                "params": ["expression"],
            }
        }

        async def fake_hotplug(tool_name):
            await _seed_calculator(registry)
            mcp_client._dormant_tools.pop(tool_name, None)
            return True

        mcp_client.hotplug_for_tool = fake_hotplug
        mcp_client.call_tool = AsyncMock(return_value="4")
        mcp_client.disconnect_server = AsyncMock(return_value=True)
        mcp_client.scan_dormant_plugins = MagicMock()

        ollama_resp = _make_ollama_resp(
            json.dumps([{"tool": "calculate", "params": {"expression": "2+2"}}])
        )

        with _patch_ollama(ollama_resp):
            await router.route_query("what is 2+2?", "test-detach")

        mcp_client.disconnect_server.assert_called_once_with("calculator_server")
        mcp_client.scan_dormant_plugins.assert_called_once()


class TestDormantScanning:
    """Verify static file parsing of server tools."""

    def test_parse_tools_from_calculator_server(self):
        """_parse_tools_from_file should find all 5 calculator tools."""
        path = Path("servers/calculator_server.py")
        if not path.exists():
            pytest.skip("calculator_server.py not found")

        tools = MCPClient._parse_tools_from_file(path)
        tool_names = {t["name"] for t in tools}
        assert tool_names == {"calculate", "percentage", "split_bill", "unit_convert", "loan_emi"}

    def test_scan_skips_connected_servers(self, mcp_client):
        """scan_dormant_plugins should not include already-connected servers."""
        # Simulate a connected server
        mcp_client._conns["calculator_server"] = MagicMock()

        mcp_client.scan_dormant_plugins("servers", exclude=["daily_digest_server.py"])

        # calculator_server tools should NOT be in dormant
        for tool_info in mcp_client._dormant_tools.values():
            assert tool_info["server"] != "calculator_server"
