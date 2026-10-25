from fastapi import APIRouter, Depends
from backend.api.auth import require_user
from backend.services.analytics_service import overview
router = APIRouter(prefix="/api/analytics",dependencies=[Depends(require_user)])
router.get("/overview")(overview)
