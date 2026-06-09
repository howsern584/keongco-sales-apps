"""
pages.py
--------
Serves the HTML pages (the screens users see in their browser).

All pages are protected by a simple session cookie — if not logged in,
you get redirected to the login page.

Authentication in Phase 2 uses a plain session cookie storing the user id.
(Phase 3 / production should upgrade this to a proper JWT or OAuth flow.)
"""

from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models

router = APIRouter()

# Render templates directly with Jinja2 — no Starlette wrapper needed.
_env = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)


def render(name: str, **ctx) -> HTMLResponse:
    """Render a Jinja2 template and return an HTMLResponse."""
    return HTMLResponse(_env.get_template(name).render(**ctx))


# ---------- Helpers ----------------------------------------------------------

def get_current_user(request: Request, db: Session):
    """Read the logged-in user from the session cookie. Return None if not logged in."""
    user_id = request.cookies.get("user_id")
    if not user_id:
        return None
    return db.query(models.User).get(int(user_id))


def login_required(request: Request, db: Session):
    """Return user or redirect to login."""
    user = get_current_user(request, db)
    if user is None:
        return None, RedirectResponse("/app/login", status_code=302)
    return user, None


def order_summary(order, db):
    """Build the display-friendly dict for an order card."""
    customer = db.query(models.Customer).get(order.customer_id)
    salesperson = db.query(models.User).get(order.salesperson_id)
    total = sum(li.line_total for li in order.line_items)
    return {
        "id": order.id,
        "customer_name": customer.name if customer else "—",
        "salesperson_name": salesperson.name if salesperson else "—",
        "status": order.status.value,
        "total": total,
        "line_count": len(order.line_items),
        "created_at": str(order.created_at),
        "reject_note": order.reject_note,
    }


# ---------- Login / Logout ---------------------------------------------------

@router.get("/app/login", response_class=HTMLResponse)
def login_page(request: Request):
    return render("login.html", error=None)


@router.post("/app/login")
async def do_login(request: Request, username: str = Form(...),
                   password: str = Form(...), db: Session = Depends(get_db)):
    # Phase 2: password check is skipped (no passwords stored yet).
    # Any user who exists can log in. Phase 3 should add real password hashing.
    user = db.query(models.User).filter(models.User.login == username).first()
    if user is None:
        return render("login.html", error="Username not found. Please try again.")
    response = RedirectResponse("/app/orders", status_code=302)
    response.set_cookie("user_id", str(user.id), httponly=True, samesite="lax")
    return response


@router.get("/app/logout")
def logout():
    response = RedirectResponse("/app/login", status_code=302)
    response.delete_cookie("user_id")
    return response


# ---------- Orders -----------------------------------------------------------

@router.get("/app/orders", response_class=HTMLResponse)
def orders_page(request: Request, db: Session = Depends(get_db)):
    user, redirect = login_required(request, db)
    if redirect:
        return redirect

    if user.role == models.UserRole.admin:
        raw_orders = db.query(models.Order).order_by(models.Order.created_at.desc()).all()
    else:
        raw_orders = db.query(models.Order).filter(
            models.Order.salesperson_id == user.id
        ).order_by(models.Order.created_at.desc()).all()

    orders = [order_summary(o, db) for o in raw_orders]
    return render("orders.html", orders=orders, role=user.role.value, user=user)


@router.get("/app/orders/new", response_class=HTMLResponse)
def new_order_page(request: Request, db: Session = Depends(get_db)):
    user, redirect = login_required(request, db)
    if redirect:
        return redirect
    if user.role != models.UserRole.salesperson:
        return RedirectResponse("/app/orders", status_code=302)
    return render("order_new.html", salesperson_id=user.id, role=user.role.value, user=user)


@router.get("/app/orders/{order_id}", response_class=HTMLResponse)
def order_detail_page(order_id: int, request: Request, db: Session = Depends(get_db)):
    user, redirect = login_required(request, db)
    if redirect:
        return redirect

    order = db.query(models.Order).get(order_id)
    if order is None:
        return RedirectResponse("/app/orders", status_code=302)

    customer  = db.query(models.Customer).get(order.customer_id)
    salesperson = db.query(models.User).get(order.salesperson_id)
    total = sum(li.line_total for li in order.line_items)

    lines = []
    for li in order.line_items:
        product = db.query(models.Product).get(li.product_id)
        lot = db.query(models.Lot).get(li.lot_id) if li.lot_id else None
        lines.append({
            "product_name": product.description if product else "—",
            "unit": product.unit.value if product else "",
            "quantity": li.quantity,
            "unit_price": li.unit_price,
            "line_total": li.line_total,
            "lot_code": lot.lot_code if lot else None,
        })

    return render("order_detail.html",
        order=order,
        customer_name=customer.name if customer else "—",
        salesperson_name=salesperson.name if salesperson else "—",
        lines=lines,
        total=total,
        role=user.role.value,
        admin_id=user.id if user.role == models.UserRole.admin else None,
        user=user,
    )


# ---------- Products ---------------------------------------------------------

@router.get("/app/products", response_class=HTMLResponse)
def products_page(request: Request, db: Session = Depends(get_db)):
    user, redirect = login_required(request, db)
    if redirect:
        return redirect

    products = db.query(models.Product).order_by(models.Product.description).all()

    # Build allocation map for salespeople: {product_id: allocation_row}
    allocations = {}
    if user.role == models.UserRole.salesperson:
        allocs = db.query(models.Allocation).filter(
            models.Allocation.salesperson_id == user.id
        ).all()
        allocations = {a.product_id: a for a in allocs}

    # Build lots map: {product_id: [lot, ...]}
    lots_map = {}
    for p in products:
        lots_map[p.id] = (
            db.query(models.Lot)
            .filter(models.Lot.product_id == p.id)
            .order_by(models.Lot.received_date.desc())
            .all()
        )

    return render("products.html",
        products=products,
        allocations=allocations,
        lots=lots_map,
        role=user.role.value,
        user=user,
    )


# ---------- Lot photos -------------------------------------------------------

@router.get("/app/lots/{lot_id}/photos", response_class=HTMLResponse)
def lot_photos_page(lot_id: int, request: Request, db: Session = Depends(get_db)):
    user, redirect = login_required(request, db)
    if redirect:
        return redirect

    lot = db.query(models.Lot).get(lot_id)
    if lot is None:
        return RedirectResponse("/app/products", status_code=302)

    product = db.query(models.Product).get(lot.product_id)
    photos_raw = (
        db.query(models.LotPhoto)
        .filter(models.LotPhoto.lot_id == lot_id)
        .order_by(models.LotPhoto.taken_at.desc())
        .all()
    )
    photos = [{"url": f"/photos/{p.image_path}", "taken_at": str(p.taken_at)} for p in photos_raw]

    return render("lot_photos.html",
        lot_id=lot_id,
        lot_code=lot.lot_code,
        product_name=product.description if product else "—",
        photos=photos,
        role=user.role.value,
        user_id=user.id,
        user=user,
    )


# ---------- Admin dashboard --------------------------------------------------

@router.get("/app/admin", response_class=HTMLResponse)
def admin_page(request: Request, db: Session = Depends(get_db)):
    user, redirect = login_required(request, db)
    if redirect:
        return redirect
    if user.role != models.UserRole.admin:
        return RedirectResponse("/app/orders", status_code=302)

    pending_raw = (
        db.query(models.Order)
        .filter(models.Order.status == models.OrderStatus.submitted)
        .order_by(models.Order.created_at.asc())
        .all()
    )
    all_raw = db.query(models.Order).order_by(models.Order.created_at.desc()).all()

    # Build alerts with names for display
    alerts_raw = (
        db.query(models.StockAlert)
        .filter(models.StockAlert.resolved.is_(False))
        .order_by(models.StockAlert.triggered_at.desc())
        .all()
    )
    stock_alerts = []
    for a in alerts_raw:
        sp = db.query(models.User).get(a.salesperson_id)
        pr = db.query(models.Product).get(a.product_id)
        stock_alerts.append({
            "salesperson_id": a.salesperson_id,
            "salesperson_name": sp.name if sp else "—",
            "product_id": a.product_id,
            "product_name": pr.description if pr else "—",
            "triggered_at": str(a.triggered_at),
        })

    return render("admin.html",
        pending_orders=[order_summary(o, db) for o in pending_raw],
        all_orders=[order_summary(o, db) for o in all_raw],
        stock_alerts=stock_alerts,
        role=user.role.value,
        user=user,
    )


# ---------- Root redirect ----------------------------------------------------

@router.get("/")
def root():
    return RedirectResponse("/app/login", status_code=302)


@router.get("/app")
def app_root():
    return RedirectResponse("/app/orders", status_code=302)
