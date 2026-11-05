from fastapi import APIRouter, HTTPException

from app.core.database import check_database

router = APIRouter()


@router.get("/health", tags=["system"])
def health():
    return {"status": "ok", "service": "ai-series-backend"}


@router.get("/health/db", tags=["system"])
def database_health():
    try:
        check_database()
        return {"status": "ok", "database": "connected"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc


@router.get("/api/v1", tags=["system"])
def api_root():
    return {
        "name": "AI Series API",
        "version": "1.0.0",
        "message": "API foundation is running",
    }
