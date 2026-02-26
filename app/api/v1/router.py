from fastapi import APIRouter

from app.api.v1.assets import router as assets_router
from app.api.v1.auth import router as auth_router
from app.api.v1.catalog import router as catalog_router
from app.api.v1.chatbot import router as chatbot_router
from app.api.v1.chats import router as chats_router
from app.api.v1.education import router as education_router
from app.api.v1.friends import router as friends_router
from app.api.v1.help import router as help_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.posts import router as posts_router
from app.api.v1.profiles import router as profiles_router
from app.api.v1.reports import router as reports_router
from app.api.v1.search import router as search_router
from app.api.v1.settings import router as settings_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(profiles_router)
api_router.include_router(friends_router)
api_router.include_router(catalog_router)
api_router.include_router(chats_router)
api_router.include_router(posts_router)
api_router.include_router(reports_router)
api_router.include_router(chatbot_router)
api_router.include_router(search_router)
api_router.include_router(notifications_router)
api_router.include_router(settings_router)
api_router.include_router(help_router)
api_router.include_router(assets_router)
api_router.include_router(education_router)
