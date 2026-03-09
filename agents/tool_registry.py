import json
from datetime import datetime, timezone

import redis.asyncio as aioredis


class ToolRegistry:
    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self._redis: aioredis.Redis | None = None

    async def connect(self) -> None:
        self._redis = await aioredis.from_url(self.redis_url, decode_responses=True)

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()

    @property
    def r(self) -> aioredis.Redis:
        if self._redis is None:
            raise RuntimeError("Not connected — call connect() first.")
        return self._redis

    # ------------------------------------------------------------------ servers

    async def register_server(
        self, name: str, script_path: str, tools: list[dict]
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self.r.hset(
            f"servers:{name}",
            mapping={
                "name": name,
                "script_path": script_path,
                "status": "active",
                "registered_at": now,
                "tool_count": len(tools),
                "tools": json.dumps(tools),
            },
        )
        for tool in tools:
            await self.register_tool(
                server_name=name,
                tool_name=tool["name"],
                description=tool.get("description", ""),
                input_schema=tool.get("inputSchema", {}),
            )

    async def deregister_server(self, name: str) -> None:
        tool_names = await self.r.smembers(f"server_tools:{name}")
        pipe = self.r.pipeline()
        for tool_name in tool_names:
            pipe.delete(f"tools:{tool_name}")
            pipe.zrem("tool_index", tool_name)
        pipe.delete(f"server_tools:{name}")
        pipe.delete(f"servers:{name}")
        await pipe.execute()

    async def get_all_servers(self) -> list[dict]:
        keys = await self.r.keys("servers:*")
        servers = []
        for key in keys:
            data = await self.r.hgetall(key)
            if data:
                data["tool_count"] = int(data.get("tool_count", 0))
                data["tools"] = json.loads(data.get("tools", "[]"))
                servers.append(data)
        return servers

    async def is_server_registered(self, name: str) -> bool:
        return bool(await self.r.exists(f"servers:{name}"))

    # ------------------------------------------------------------------- tools

    async def register_tool(
        self,
        server_name: str,
        tool_name: str,
        description: str,
        input_schema: dict,
    ) -> None:
        await self.r.hset(
            f"tools:{tool_name}",
            mapping={
                "server_name": server_name,
                "tool_name": tool_name,
                "description": description,
                "schema": json.dumps(input_schema),
            },
        )
        await self.r.sadd(f"server_tools:{server_name}", tool_name)
        await self.r.zadd("tool_index", {tool_name: 0})

    async def get_all_tools(self) -> list[dict]:
        tool_names = await self.r.zrange("tool_index", 0, -1)
        tools = []
        for tool_name in tool_names:
            data = await self.r.hgetall(f"tools:{tool_name}")
            if data:
                data["schema"] = json.loads(data.get("schema", "{}"))
                tools.append(data)
        return tools

    async def find_tool(self, tool_name: str) -> dict | None:
        data = await self.r.hgetall(f"tools:{tool_name}")
        if not data:
            return None
        data["schema"] = json.loads(data.get("schema", "{}"))
        server_data = await self.r.hgetall(f"servers:{data['server_name']}")
        data["server_script_path"] = server_data.get("script_path", "")
        data["server_status"] = server_data.get("status", "unknown")
        return data

    async def get_tools_summary(self) -> str:
        tools = await self.get_all_tools()
        if not tools:
            return "No tools registered."
        lines = []
        for t in tools:
            schema = t.get("schema", {})
            props = schema.get("properties", {})
            required = schema.get("required", [])
            if props:
                params = ", ".join(
                    f"{name} ({'required' if name in required else 'optional'})"
                    for name in props
                )
                lines.append(f"- {t['tool_name']}({params}): {t['description']}")
            else:
                lines.append(f"- {t['tool_name']}: {t['description']}")
        return "\n".join(lines)
