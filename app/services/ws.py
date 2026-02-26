from __future__ import annotations

import asyncio
import json
from collections import defaultdict

from fastapi import WebSocket
from redis.asyncio.client import PubSub

from app.db.redis import redis_client


class RedisFanoutManager:
    def __init__(self) -> None:
        self.local_connections: dict[str, set[WebSocket]] = defaultdict(set)
        self.lock = asyncio.Lock()

    async def connect(self, channel: str, ws: WebSocket) -> None:
        async with self.lock:
            self.local_connections[channel].add(ws)

    async def disconnect(self, channel: str, ws: WebSocket) -> None:
        async with self.lock:
            if channel in self.local_connections and ws in self.local_connections[channel]:
                self.local_connections[channel].remove(ws)
                if not self.local_connections[channel]:
                    self.local_connections.pop(channel, None)

    async def publish(self, channel: str, message: dict) -> None:
        await redis_client.publish(channel, json.dumps(message))

    async def subscribe_loop(self, channel: str, ws: WebSocket) -> None:
        pubsub: PubSub = redis_client.pubsub()
        await pubsub.subscribe(channel)
        try:
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg.get("data"):
                    await ws.send_text(str(msg["data"]))
                await asyncio.sleep(0.02)
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()


ws_manager = RedisFanoutManager()
