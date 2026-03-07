from fastapi import APIRouter

from app.api.admin_routes import router as admin_router
from app.api.store_routes import router as store_router

router = APIRouter(prefix="/twa")
router.include_router(store_router)
router.include_router(admin_router)
