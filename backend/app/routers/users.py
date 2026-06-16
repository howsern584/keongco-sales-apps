# -*- coding: utf-8 -*-
"""
users.py
--------
Admin-only endpoints for managing user accounts:

  GET  /admin/users          -> list all users
  POST /admin/users          -> create a new user (admin sets the initial password)
  POST /admin/users/{id}/deactivate  -> disable a user (they can no longer log in)
  POST /admin/users/{id}/activate    -> re-enable a disabled user
  POST /admin/users/{id}/reset-password -> set a new password for any user
"""

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional

from ..database import get_db
from .. import models

router = APIRouter(prefix="/admin/users", tags=["users"])


# ---------- Request / Response schemas (local, no need to pollute schemas.py) -

class UserCreatePayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    login: str = Field(..., min_length=2, max_length=50)
    role: str                    # "salesperson" | "admin" | "warehouse"
    password: str = Field(..., min_length=4, max_length=128)


class PasswordResetPayload(BaseModel):
    new_password: str = Field(..., min_length=4, max_length=128)


class UserOut(BaseModel):
    id: int
    name: str
    login: str
    role: str
    is_active: bool
    has_password: bool

    class Config:
        from_attributes = True


# ---------- Helper ----------------------------------------------------------

def _require_admin(request: Request, db: Session) -> models.User:
    """Raise 403 if the caller isn't a logged-in admin."""
    user_id = request.cookies.get("user_id")
    if not user_id:
        raise HTTPException(status_code=403, detail="Not logged in.")
    user = db.query(models.User).get(int(user_id))
    if user is None or user.role != models.UserRole.admin:
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user


def _to_out(u: models.User) -> UserOut:
    return UserOut(
        id=u.id,
        name=u.name,
        login=u.login,
        role=u.role.value,
        is_active=u.is_active if u.is_active is not None else True,
        has_password=bool(u.password_hash),
    )


# ---------- Endpoints -------------------------------------------------------

@router.get("", response_model=list[UserOut])
def list_users(request: Request, db: Session = Depends(get_db)):
    """Return all users. Admin only."""
    _require_admin(request, db)
    users = db.query(models.User).order_by(models.User.id).all()
    return [_to_out(u) for u in users]


@router.post("", response_model=UserOut, status_code=201)
def create_user(payload: UserCreatePayload, request: Request, db: Session = Depends(get_db)):
    """Create a new user account. Admin only."""
    _require_admin(request, db)

    # Validate role value
    try:
        role = models.UserRole(payload.role)
    except ValueError:
        raise HTTPException(status_code=400,
                            detail=f"Invalid role '{payload.role}'. "
                                   f"Choose: salesperson, admin, warehouse.")

    # Enforce unique login
    existing = db.query(models.User).filter(models.User.login == payload.login).first()
    if existing:
        raise HTTPException(status_code=400,
                            detail=f"Login '{payload.login}' is already taken.")

    pw_hash = bcrypt.hashpw(payload.password.encode(), bcrypt.gensalt()).decode()
    user = models.User(
        name=payload.name,
        login=payload.login,
        role=role,
        password_hash=pw_hash,
        is_active=True,
    )
    db.add(user)
    db.flush()  # get user.id before creating allocations

    # Auto-create allocation rows for salespeople and admins
    # (both roles place orders and follow the same stock rules).
    if role in (models.UserRole.salesperson, models.UserRole.admin):
        products = db.query(models.Product).all()
        for p in products:
            if p.allocation_mode in (
                models.AllocationMode.manual_topup,
                models.AllocationMode.shared_reset,
            ):
                db.add(models.Allocation(
                    salesperson_id=user.id,
                    product_id=p.id,
                    allocated_qty=0,
                    used_qty=0,
                    remaining_qty=0,
                    allocation_mode=p.allocation_mode,
                ))

    db.commit()
    db.refresh(user)
    return _to_out(user)


class UserUpdatePayload(BaseModel):
    name: str = Field(..., min_length=1)
    role: Optional[str] = None

@router.patch("/{user_id}")
def update_user(user_id: int, payload: UserUpdatePayload, request: Request, db: Session = Depends(get_db)):
    """Update a user's name and/or role. Admin only."""
    _require_admin(request, db)
    user = db.query(models.User).get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    user.name = payload.name.strip()
    if payload.role:
        try:
            user.role = models.UserRole(payload.role)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid role.")
    db.commit()
    return _to_out(user)


@router.post("/{user_id}/deactivate")
def deactivate_user(user_id: int, request: Request, db: Session = Depends(get_db)):
    """Disable a user account so they cannot log in. Admin only."""
    caller = _require_admin(request, db)
    if caller.id == user_id:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account.")
    user = db.query(models.User).get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    user.is_active = False
    db.commit()
    return {"ok": True, "message": f"{user.name} has been deactivated."}


@router.post("/{user_id}/activate")
def activate_user(user_id: int, request: Request, db: Session = Depends(get_db)):
    """Re-enable a deactivated user account. Admin only."""
    _require_admin(request, db)
    user = db.query(models.User).get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    user.is_active = True
    db.commit()
    return {"ok": True, "message": f"{user.name} has been re-activated."}


@router.delete("/{user_id}")
def delete_user(user_id: int, request: Request, db: Session = Depends(get_db)):
    """Permanently delete a user account. Admin only. Cannot delete yourself."""
    caller = _require_admin(request, db)
    if caller.id == user_id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account.")
    user = db.query(models.User).get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    db.delete(user)
    db.commit()
    return {"ok": True, "message": f"{user.name} has been deleted."}


@router.post("/{user_id}/reset-password")
def reset_password(user_id: int, payload: PasswordResetPayload,
                   request: Request, db: Session = Depends(get_db)):
    """Admin sets a new password for any user. The user should change it on next login."""
    _require_admin(request, db)
    user = db.query(models.User).get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    pw_hash = bcrypt.hashpw(payload.new_password.encode(), bcrypt.gensalt()).decode()
    user.password_hash = pw_hash
    db.commit()
    return {"ok": True, "message": f"Password for {user.name} has been reset."}
