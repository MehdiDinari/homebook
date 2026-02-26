from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import and_, select

from app.db.session import SessionLocal
from app.models.chat import ChatMember
from app.models.user import UserShadow
from app.services.auth import get_current_user_from_ws
from app.services.ws import ws_manager

chat_ws_router = APIRouter(tags=["ws-chat"])


@chat_ws_router.websocket("/chats/rooms/{room_id}")
async def ws_room_chat(websocket: WebSocket, room_id: int) -> None:
    await websocket.accept()

    channel = f"chat:room:{room_id}"
    listener_task = None

    try:
        async with SessionLocal() as db:
            auth_user = await get_current_user_from_ws(websocket, db)
            me = (
                await db.execute(select(UserShadow).where(UserShadow.wp_user_id == auth_user.wp_user_id))
            ).scalar_one_or_none()
            if me is None:
                await websocket.close(code=4401)
                return

            member = (
                await db.execute(
                    select(ChatMember).where(
                        and_(ChatMember.room_id == room_id, ChatMember.user_id == me.id)
                    )
                )
            ).scalar_one_or_none()
            if member is None:
                await websocket.close(code=4403)
                return

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
        await ws_manager.disconnect(channel, websocket)
