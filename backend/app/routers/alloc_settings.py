"""
alloc_settings.py
-----------------
API endpoints for the automatic allocation settings page.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from ..database import get_db
from .. import models
from ..alloc_engine import get_settings, run_auto_allocation, preview_auto_allocation
from .pages import get_current_user

router = APIRouter(prefix="/alloc-settings", tags=["allocation settings"])


class SettingsUpdate(BaseModel):
    weeks_lookback: int
    low_stock_threshold: int
    auto_renewal_enabled: bool


def require_admin(request: Request, db: Session):
    user = get_current_user(request, db)
    if user is None or user.role != models.UserRole.admin:
        raise HTTPException(status_code=403, detail="Admin only.")
    return user


@router.get("")
def get_alloc_settings(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    s = get_settings(db)
    return {
        "weeks_lookback":       s.weeks_lookback,
        "low_stock_threshold":  s.low_stock_threshold,
        "auto_renewal_enabled": s.auto_renewal_enabled,
        "last_run_at":          s.last_run_at.isoformat() if s.last_run_at else None,
        "next_run_at":          s.next_run_at.isoformat() if s.next_run_at else None,
    }


@router.post("")
def update_alloc_settings(body: SettingsUpdate, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    if body.weeks_lookback < 1 or body.weeks_lookback > 52:
        raise HTTPException(status_code=400, detail="weeks_lookback must be 1–52.")
    if body.low_stock_threshold < 0:
        raise HTTPException(status_code=400, detail="Threshold must be ≥ 0.")
    s = get_settings(db)
    s.weeks_lookback      = body.weeks_lookback
    s.low_stock_threshold = body.low_stock_threshold
    s.auto_renewal_enabled = body.auto_renewal_enabled
    db.commit()
    return {"ok": True}


@router.post("/preview")
def preview_settings(request: Request, db: Session = Depends(get_db)):
    """Show what allocations would be set without writing anything."""
    require_admin(request, db)
    return preview_auto_allocation(db)


@router.post("/run-now")
def run_now(request: Request, db: Session = Depends(get_db)):
    """Trigger the allocation engine immediately (same as Monday 04:00 run)."""
    require_admin(request, db)
    result = run_auto_allocation(db)
    return result
