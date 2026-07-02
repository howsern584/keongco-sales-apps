"""
notifications.py
----------------
Salesperson-facing "Price Updates" notifications.

Whenever admin changes a product's price (base / special / floor / ceiling), a
PriceChangeEvent row is written (see allocations.update_product_price). These
endpoints power the bell badge in the top bar:

  GET  /price-updates/count      -> how many changes this user hasn't seen yet
  POST /price-updates/mark-seen  -> mark everything up to now as seen (clears badge)

"Unread" = events newer than this user's prices_seen_at, excluding changes the
user made themselves (an admin shouldn't be pinged for their own price edit).
The list itself is rendered as an HTML page by pages.order... (see pages.py:
GET /app/price-updates), which also marks things seen when opened.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from .pages import get_current_user

router = APIRouter(prefix="/price-updates", tags=["notifications"])


def unread_count(db: Session, user: models.User) -> int:
    """Number of unseen PRODUCT price updates for this user (not raw field changes).

    One admin edit changes several fields (base/min/max/discount) but is a single
    'update' to the user — so we count distinct (product, timestamp) groups, matching
    the one-line-per-product feed. Excludes changes the user made themselves.
    """
    q = db.query(models.PriceChangeEvent.product_id, models.PriceChangeEvent.changed_at).filter(
        models.PriceChangeEvent.changed_by != user.id
    )
    if user.prices_seen_at is not None:
        q = q.filter(models.PriceChangeEvent.changed_at > user.prices_seen_at)
    return q.distinct().count()


@router.get("/count")
def get_unread_count(request: Request, db: Session = Depends(get_db)):
    """Return the current user's unseen price-change count (for the bell badge)."""
    user = get_current_user(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Login required.")
    return {"unread": unread_count(db, user)}


@router.post("/mark-seen")
def mark_seen(request: Request, db: Session = Depends(get_db)):
    """Mark all price changes up to now as seen for the current user (clears badge)."""
    user = get_current_user(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Login required.")
    user.prices_seen_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "unread": 0}
