"""
MCP client that manages stdio server connections.

Each server runs in a persistent background asyncio.Task that holds the
stdio_client + ClientSession context managers open. A stop-event signals
clean shutdown, avoiding the anyio cancel-scope affinity error that occurs
when AsyncExitStack.aclose() is called from a different task than the one
that entered the context managers.
"""
import ast
import asyncio
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

from agents.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class _Conn:
    """State for a single live server connection."""
    session: ClientSession | None = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    error: Exception | None = None


class MCPClient:
    def __init__(self, tool_registry: ToolRegistry):
        self._registry = tool_registry
        self._conns: dict[str, _Conn] = {}
        # Available but not-yet-loaded plugins: {tool_name: {server, script, description}}
        self._dormant_tools: dict[str, dict] = {}

    # ----------------------------------------------------------------- internal

    async def _start_conn(self, params: StdioServerParameters) -> _Conn:
        """Spawn a background task that keeps the MCP session alive."""
        conn = _Conn()

        async def _run() -> None:
            try:
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        conn.session = session
                        conn.ready.set()          # unblock connect_server
                        await conn.stop.wait()    # stay alive until disconnect
            except Exception as exc:
                conn.error = exc
                conn.ready.set()                  # unblock connect_server on failure

        conn.task = asyncio.create_task(_run())
        await conn.ready.wait()

        if conn.error:
            raise conn.error

        return conn

    async def _stop_conn(self, name: str) -> None:
        conn = self._conns.pop(name, None)
        if not conn:
            return
        conn.stop.set()
        if conn.task and not conn.task.done():
            try:
                await asyncio.wait_for(asyncio.shield(conn.task), timeout=5.0)
            except (asyncio.TimeoutError, Exception) as exc:
                logger.warning("Server %s did not shut down cleanly: %s", name, exc)
                conn.task.cancel()

    # ----------------------------------------------------------------- connect

    async def connect_server(self, name: str, script_path: str) -> dict:
        path = Path(script_path)
        if not path.exists():
            raise FileNotFoundError(f"Server script not found: {script_path}")

        params = StdioServerParameters(
            command=sys.executable,
            args=[str(path.resolve())],
            env=dict(os.environ),
        )

        # Attempt 1 — retry once on failure
        try:
            conn = await self._start_conn(params)
        except Exception as exc:
            logger.warning("connect_server(%s) attempt 1 failed: %s — retrying", name, exc)
            try:
                conn = await self._start_conn(params)
            except Exception as exc2:
                logger.error("connect_server(%s) failed after retry: %s", name, exc2)
                raise

        self._conns[name] = conn

        # Discover tools
        try:
            result = await conn.session.list_tools()
        except Exception as exc:
            logger.error("list_tools failed for %s: %s", name, exc)
            await self._stop_conn(name)
            raise

        tool_dicts = [
            {
                "name": t.name,
                "description": t.description or "",
                "inputSchema": (
                    t.inputSchema
                    if isinstance(t.inputSchema, dict)
                    else t.inputSchema.model_dump()
                ),
            }
            for t in result.tools
        ]

        # register_server stores metadata and calls register_tool for each entry
        await self._registry.register_server(name, script_path, tool_dicts)

        tool_names = [t["name"] for t in tool_dicts]
        logger.info("Connected %s with tools: %s", name, tool_names)
        return {"name": name, "tools": tool_names}

    # --------------------------------------------------------------- disconnect

    async def disconnect_server(self, name: str) -> bool:
        if name not in self._conns:
            return False
        await self._stop_conn(name)
        await self._registry.deregister_server(name)
        logger.info("Disconnected server %s", name)
        return True

    async def disconnect_all(self) -> None:
        for name in list(self._conns):
            await self.disconnect_server(name)

    # ---------------------------------------------------------------- call tool

    async def call_tool(self, tool_name: str, params: dict) -> str:
        tool_info = await self._registry.find_tool(tool_name)
        if not tool_info:
            return "Tool not available"

        server_name = tool_info["server_name"]
        conn = self._conns.get(server_name)

        # Session gone — attempt one reconnect
        if not conn or not conn.session:
            script_path = tool_info.get("server_script_path", "")
            if not script_path:
                return f"Server {server_name!r} is unavailable"
            logger.warning("Session lost for %s — reconnecting", server_name)
            try:
                await self.connect_server(server_name, script_path)
                conn = self._conns.get(server_name)
            except Exception as exc:
                logger.error("Reconnect failed for %s: %s", server_name, exc)
                return f"Server {server_name!r} is unavailable"

        if not conn or not conn.session:
            return f"Server {server_name!r} is unavailable"

        try:
            result = await asyncio.wait_for(
                conn.session.call_tool(tool_name, params),
                timeout=120.0,
            )
        except asyncio.TimeoutError:
            return "Tool timed out"
        except Exception as exc:
            logger.error("call_tool(%s) error: %s", tool_name, exc)
            return f"Tool error: {exc}"

        if result.isError:
            texts = [c.text for c in result.content if isinstance(c, TextContent)]
            return "Tool error: " + " ".join(texts)

        parts = []
        for c in result.content:
            if isinstance(c, TextContent):
                parts.append(c.text)
            elif hasattr(c, "text"):
                parts.append(c.text)
            else:
                parts.append(str(c))
        return "\n".join(parts)

    # --------------------------------------------------------------- discovery

    async def discover_and_register(
        self, server_dir: str, exclude: list[str] | None = None
    ) -> None:
        skip = {"__init__.py"} | set(exclude or [])
        scripts = [
            f for f in Path(server_dir).glob("*.py")
            if f.name not in skip
        ]

        connected = 0
        total_tools = 0
        for script in scripts:
            try:
                info = await self.connect_server(script.stem, str(script))
                connected += 1
                total_tools += len(info["tools"])
            except Exception as exc:
                logger.error("Skipping %s: %s", script.name, exc)

        logger.info("Discovered %d tools from %d servers", total_tools, connected)

    # --------------------------------------------------------- plugin scanning

    @staticmethod
    def _parse_tools_from_file(script_path: Path) -> list[dict]:
        """Parse @mcp.tool() decorated functions from a server file without running it."""
        try:
            source = script_path.read_text()
            tree = ast.parse(source)
        except Exception:
            return []

        tools = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            # Check if decorated with @mcp.tool()
            is_tool = any(
                (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                 and d.func.attr == "tool")
                or (isinstance(d, ast.Attribute) and d.attr == "tool")
                for d in node.decorator_list
            )
            if not is_tool:
                continue
            docstring = ast.get_docstring(node) or ""
            params = []
            for arg in node.args.args:
                if arg.arg != "self":
                    params.append(arg.arg)
            tools.append({
                "name": node.name,
                "description": docstring,
                "params": params,
            })
        return tools

    def scan_dormant_plugins(
        self, server_dir: str, exclude: list[str] | None = None
    ) -> None:
        """Scan server scripts that are NOT loaded and build a dormant tools catalog."""
        skip = {"__init__.py"} | set(exclude or [])
        self._dormant_tools.clear()

        for script in Path(server_dir).glob("*.py"):
            if script.name in skip:
                continue
            server_name = script.stem
            # Skip already-connected servers
            if server_name in self._conns:
                continue
            tools = self._parse_tools_from_file(script)
            for tool in tools:
                self._dormant_tools[tool["name"]] = {
                    "server": server_name,
                    "script_path": str(script),
                    "description": tool["description"],
                    "params": tool["params"],
                }
        if self._dormant_tools:
            logger.info(
                "Scanned %d dormant tools from unloaded plugins: %s",
                len(self._dormant_tools),
                list(self._dormant_tools.keys()),
            )

    def get_dormant_tools_summary(self) -> str:
        """Return a formatted summary of dormant (not-yet-loaded) tools for the LLM."""
        if not self._dormant_tools:
            return ""
        lines = []
        for name, info in self._dormant_tools.items():
            params_str = ", ".join(f"{p} (required)" for p in info["params"])
            if params_str:
                lines.append(f"- {name}({params_str}): {info['description']}")
            else:
                lines.append(f"- {name}: {info['description']}")
        return "\n".join(lines)

    def get_dormant_server_for_tool(self, tool_name: str) -> str | None:
        """Return the server name that provides a dormant tool, or None."""
        info = self._dormant_tools.get(tool_name)
        return info["server"] if info else None

    async def hotplug_for_tool(self, tool_name: str) -> bool:
        """Hot-plug a dormant server if it provides the requested tool.

        Returns True if a server was successfully loaded.
        """
        info = self._dormant_tools.get(tool_name)
        if not info:
            return False

        server_name = info["server"]
        script_path = info["script_path"]
        logger.info("Hot-plugging server %s for tool %s", server_name, tool_name)

        try:
            result = await self.connect_server(server_name, script_path)
        except Exception as exc:
            logger.error("Failed to hot-plug %s: %s", server_name, exc)
            return False

        # Remove all tools from this server from the dormant catalog
        loaded_tools = result["tools"]
        for t in loaded_tools:
            self._dormant_tools.pop(t, None)

        logger.info("Hot-plugged %s with tools: %s", server_name, loaded_tools)
        return True
