import pytest
import pytest_asyncio

from agents.tool_registry import ToolRegistry

SAMPLE_TOOLS = [
    {
        "name": "search_web",
        "description": "Search the web for information",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
    },
    {
        "name": "get_page",
        "description": "Fetch a webpage by URL",
        "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}},
    },
]


@pytest_asyncio.fixture
async def registry(fake_redis):
    reg = ToolRegistry("redis://fake")
    reg._redis = fake_redis
    return reg


# ------------------------------------------------------------------ servers

async def test_register_server(registry):
    await registry.register_server("srv", "servers/srv.py", SAMPLE_TOOLS)
    assert await registry.is_server_registered("srv")


async def test_deregister_server(registry):
    await registry.register_server("srv", "servers/srv.py", SAMPLE_TOOLS)
    await registry.deregister_server("srv")
    assert not await registry.is_server_registered("srv")


async def test_deregister_removes_tools(registry):
    await registry.register_server("srv", "servers/srv.py", SAMPLE_TOOLS)
    await registry.deregister_server("srv")
    assert await registry.find_tool("search_web") is None
    assert await registry.find_tool("get_page") is None


async def test_get_all_servers(registry):
    await registry.register_server("srv", "servers/srv.py", SAMPLE_TOOLS)
    servers = await registry.get_all_servers()
    assert len(servers) == 1
    assert servers[0]["name"] == "srv"
    assert servers[0]["tool_count"] == 2
    assert servers[0]["status"] == "active"


# ------------------------------------------------------------------- tools

async def test_register_tool(registry):
    await registry.register_tool("my_srv", "my_tool", "Does something", {"type": "object"})
    tool = await registry.find_tool("my_tool")
    assert tool is not None
    assert tool["tool_name"] == "my_tool"
    assert tool["server_name"] == "my_srv"
    assert tool["description"] == "Does something"
    assert isinstance(tool["schema"], dict)


async def test_find_tool_with_server_info(registry):
    await registry.register_server("srv", "servers/srv.py", SAMPLE_TOOLS)
    result = await registry.find_tool("search_web")
    assert result is not None
    assert result["tool_name"] == "search_web"
    assert result["server_name"] == "srv"
    assert result["server_script_path"] == "servers/srv.py"
    assert result["server_status"] == "active"


async def test_find_tool_missing_returns_none(registry):
    result = await registry.find_tool("nonexistent_tool")
    assert result is None


async def test_get_all_tools(registry):
    await registry.register_server("srv", "servers/srv.py", SAMPLE_TOOLS)
    tools = await registry.get_all_tools()
    assert len(tools) == 2
    names = {t["tool_name"] for t in tools}
    assert names == {"search_web", "get_page"}
    for t in tools:
        assert "server_name" in t
        assert "description" in t
        assert isinstance(t["schema"], dict)


async def test_get_tools_summary_format(registry):
    await registry.register_server("srv", "servers/srv.py", SAMPLE_TOOLS)
    summary = await registry.get_tools_summary()
    lines = summary.strip().split("\n")
    assert len(lines) == 2
    for line in lines:
        assert line.startswith("- ")
        assert ": " in line
    assert "search_web" in summary
    assert "Search the web for information" in summary


async def test_get_tools_summary_empty(registry):
    summary = await registry.get_tools_summary()
    assert summary == "No tools registered."
