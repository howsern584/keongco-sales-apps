"""
groups.py
---------
CRUD for UserGroups and assigning users to groups.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from ..database import get_db
from .. import models

router = APIRouter(prefix="/admin/groups", tags=["groups"])


class GroupCreate(BaseModel):
    name: str


class UserGroupAssign(BaseModel):
    group_id: Optional[int] = None   # None = remove from group (individual)


@router.get("")
def list_groups(db: Session = Depends(get_db)):
    groups = db.query(models.UserGroup).order_by(models.UserGroup.name).all()
    result = []
    for g in groups:
        members = db.query(models.User).filter(
            models.User.group_id == g.id,
            models.User.is_active.is_(True),
        ).order_by(models.User.name).all()
        result.append({
            "id":      g.id,
            "name":    g.name,
            "members": [{"id": m.id, "name": m.name} for m in members],
        })
    return result


@router.post("")
def create_group(body: GroupCreate, db: Session = Depends(get_db)):
    if db.query(models.UserGroup).filter(models.UserGroup.name == body.name).first():
        raise HTTPException(status_code=400, detail="Group name already exists.")
    g = models.UserGroup(name=body.name)
    db.add(g)
    db.commit()
    db.refresh(g)
    return {"id": g.id, "name": g.name}


@router.delete("/{group_id}")
def delete_group(group_id: int, db: Session = Depends(get_db)):
    g = db.query(models.UserGroup).get(group_id)
    if g is None:
        raise HTTPException(status_code=404, detail="Group not found.")
    # Remove all members from the group first
    db.query(models.User).filter(models.User.group_id == group_id).update({"group_id": None})
    db.delete(g)
    db.commit()
    return {"ok": True}


@router.post("/users/{user_id}/assign")
def assign_user_to_group(user_id: int, body: UserGroupAssign, db: Session = Depends(get_db)):
    user = db.query(models.User).get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if body.group_id is not None:
        g = db.query(models.UserGroup).get(body.group_id)
        if g is None:
            raise HTTPException(status_code=404, detail="Group not found.")
    user.group_id = body.group_id
    db.commit()
    return {"ok": True, "user_id": user_id, "group_id": body.group_id}
