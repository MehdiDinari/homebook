from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.user import UserShadow
from app.services.auth import get_current_user_from_ws
from app.services.ws import ws_manager

notifications_ws_router = APIRouter(tags=["ws-notifications"])


@notifications_ws_router.websocket("/notifications")
async def ws_notifications(websocket: WebSocket) -> None:
    await websocket.accept()

    listener_task = None
    channel = ""

    try:
        async with SessionLocal() as db:
            auth_user = await get_current_user_from_ws(websocket, db)
            me = (
                await db.execute(select(UserShadow).where(UserShadow.wp_user_id == auth_user.wp_user_id))
            ).scalar_one_or_none()
            if me is None:
                await websocket.close(code=4401)
                return

            channel = f"notif:{me.id}"
            await ws_manager.connect(channel, websocket)
            listener_task = asyncio.create_task(ws_manager.subscribe_loop(channel, websocket))

            while True:
                msg = await websocket.receive_text()
                if msg.lower().strip() == "ping":
                    await websocket.send_text('{"type":"pong"}')

    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        if listener_task:
            listener_task.cancel()
        if channel:
            await ws_manager.disconnect(channel, websocket)
