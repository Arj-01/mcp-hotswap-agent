import json
from datetime import datetime, timezone

import redis.asyncio as aioredis

MAX_MESSAGES = 50


class ChatHistory:
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

    async def add_message(self, session_id: str, role: str, content: str) -> None:
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        key = f"chat:{session_id}"
        await self.r.rpush(key, json.dumps(message))
        # Keep only the most recent MAX_MESSAGES entries
        await self.r.ltrim(key, -MAX_MESSAGES, -1)

    async def get_history(self, session_id: str) -> list[dict]:
        raw = await self.r.lrange(f"chat:{session_id}", 0, -1)
        return [json.loads(m) for m in raw]

    async def clear_history(self, session_id: str) -> None:
        await self.r.delete(f"chat:{session_id}")
