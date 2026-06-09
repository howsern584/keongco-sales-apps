"""
photos.py
---------
Quality photo management — two sides:

  Warehouse staff:
    POST /lots/{lot_id}/photos   -> upload a photo for a lot

  Salespeople (and admin):
    GET  /lots/{lot_id}/photos   -> see all photos for a lot, newest first
    GET  /lots/{lot_id}/photos/latest -> the single most recent photo
    GET  /photos/{filename}      -> serve the actual image file

Photos are stored in backend/uploads/ on your own server.
Nothing is sent to any external service.
Only .jpg, .jpeg, .png, and .webp files are accepted.
"""

import os
import uuid
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models

router = APIRouter(tags=["photos"])

# Where uploaded photos are saved on disk.
# This folder sits inside backend/ and is excluded from git via .gitignore.
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "uploads")
ALLOWED_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB per photo


def _ensure_upload_dir():
    os.makedirs(UPLOAD_DIR, exist_ok=True)


# ----- Schema for photo responses (inline here — small enough) ---------------

from pydantic import BaseModel

class PhotoOut(BaseModel):
    id: int
    lot_id: int
    uploaded_by: int
    image_path: str
    taken_at: datetime
    url: str               # the URL to view the actual image

    class Config:
        from_attributes = True


# ----- Endpoints -------------------------------------------------------------

@router.post("/lots/{lot_id}/photos", response_model=PhotoOut)
async def upload_photo(
    lot_id: int,
    warehouse_user_id: int = Query(..., description="ID of the warehouse user uploading"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Warehouse staff upload a quality photo for a lot.
    Only warehouse-role users may upload.
    Accepts JPG, PNG, or WEBP up to 10 MB.
    """
    # Confirm the lot exists.
    lot = db.query(models.Lot).get(lot_id)
    if lot is None:
        raise HTTPException(status_code=404, detail="Lot not found")

    # Confirm the uploader is a warehouse user.
    user = db.query(models.User).get(warehouse_user_id)
    if user is None or user.role != models.UserRole.warehouse:
        raise HTTPException(status_code=403, detail="Only warehouse staff can upload photos")

    # Check file type.
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{file.content_type}' not allowed. Use JPG, PNG, or WEBP."
        )

    # Read file and check size.
    contents = await file.read()
    if len(contents) > MAX_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="File is too large. Maximum size is 10 MB.")

    # Save the file with a unique name so nothing gets overwritten.
    ext = os.path.splitext(file.filename or "photo.jpg")[1].lower() or ".jpg"
    filename = f"lot{lot_id}_{uuid.uuid4().hex}{ext}"
    _ensure_upload_dir()
    filepath = os.path.join(UPLOAD_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(contents)

    # Save a record to the database.
    photo = models.LotPhoto(
        lot_id=lot_id,
        uploaded_by=warehouse_user_id,
        image_path=filename,   # store just the filename; full path varies by machine
        taken_at=datetime.utcnow(),
    )
    db.add(photo)
    db.commit()
    db.refresh(photo)
    return _with_url(photo)


@router.get("/lots/{lot_id}/photos", response_model=List[PhotoOut])
def list_photos(lot_id: int, db: Session = Depends(get_db)):
    """All photos for a lot, newest first. Salespeople use this to browse quality shots."""
    lot = db.query(models.Lot).get(lot_id)
    if lot is None:
        raise HTTPException(status_code=404, detail="Lot not found")

    photos = (
        db.query(models.LotPhoto)
        .filter(models.LotPhoto.lot_id == lot_id)
        .order_by(models.LotPhoto.taken_at.desc())
        .all()
    )
    return [_with_url(p) for p in photos]


@router.get("/lots/{lot_id}/photos/latest", response_model=PhotoOut)
def latest_photo(lot_id: int, db: Session = Depends(get_db)):
    """The single most recent photo for a lot — what a salesperson sees at a glance."""
    lot = db.query(models.Lot).get(lot_id)
    if lot is None:
        raise HTTPException(status_code=404, detail="Lot not found")

    photo = (
        db.query(models.LotPhoto)
        .filter(models.LotPhoto.lot_id == lot_id)
        .order_by(models.LotPhoto.taken_at.desc())
        .first()
    )
    if photo is None:
        raise HTTPException(status_code=404, detail="No photos uploaded for this lot yet")
    return _with_url(photo)


@router.get("/photos/{filename}")
def serve_photo(filename: str):
    """Serve the actual image file so it can be displayed in the app."""
    # Strip any path separators to prevent directory traversal attacks.
    safe_filename = os.path.basename(filename)
    filepath = os.path.join(UPLOAD_DIR, safe_filename)
    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="Photo not found")
    return FileResponse(filepath)


def _with_url(photo: models.LotPhoto) -> PhotoOut:
    """Build a PhotoOut with a ready-to-use URL for the image."""
    return PhotoOut(
        id=photo.id,
        lot_id=photo.lot_id,
        uploaded_by=photo.uploaded_by,
        image_path=photo.image_path,
        taken_at=photo.taken_at,
        url=f"/photos/{photo.image_path}",
    )
