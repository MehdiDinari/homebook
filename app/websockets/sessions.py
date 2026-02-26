from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.api.v1.education import _ensure_session_access, _role_tag_from_roles, _utc_now
from app.db.session import SessionLocal
from app.models.education import TeacherSession
from app.models.user import Profile, UserShadow
from app.services.auth import get_current_user_from_ws
from app.services.ws import ws_manager

session_ws_router = APIRouter(tags=["ws-sessions"])


@session_ws_router.websocket("/sessions/{session_id}")
async def ws_session_events(websocket: WebSocket, session_id: int) -> None:
    await websocket.accept()

    channel = f"session:{session_id}"
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

            row = (await db.execute(select(TeacherSession).where(TeacherSession.id == session_id))).scalar_one_or_none()
            if row is None:
                await websocket.close(code=4404)
                return

            try:
                await _ensure_session_access(db, actor=me, row=row)
            except HTTPException as exc:
                await websocket.close(code=4403 if int(exc.status_code) == 403 else 4401)
                return

            avatar_url = (
                await db.execute(select(Profile.avatar_url).where(Profile.user_id == me.id))
            ).scalar_one_or_none()

            await ws_manager.connect(channel, websocket)
            listener_task = asyncio.create_task(ws_manager.subscribe_loop(channel, websocket))

            await websocket.send_json(
                {
                    "type": "session.ws.ready",
                    "session_id": row.id,
                    "event_at": _utc_now().isoformat(),
                    "actor": {
                        "wp_user_id": me.wp_user_id,
                        "display_name": me.display_name,
                        "role_tag": _role_tag_from_roles(me.roles),
                        "avatar_url": (avatar_url or "").strip() or None,
                    },
                }
            )

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
