"""
pages.py
--------
Serves the HTML pages (the screens users see in their browser).

All pages are protected by a simple session cookie -- if not logged in,
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

# Render templates directly with Jinja2 -- no Starlette wrapper needed.
_env = Environment(loader=FileSystemLoader("app/templates", encoding="utf-8"), autoescape=True, auto_reload=True)

# Canonical category display order (first 2 chars of sage_item_code, lowercase).
# Used wherever products are listed so every screen shows the same sequence.
_CAT_ORDER = ['on', 'sh', 'gi', 'po', 'ga', 'co', 'pa', 'be', 'sp', 'dc']

def _cat_rank(product) -> int:
    """Return the sort position for a product based on its category prefix."""
    cat = product.sage_item_code[:2].lower()
    try:
        return _CAT_ORDER.index(cat)
    except ValueError:
        return len(_CAT_ORDER)   # unknown categories go last

def _sort_products(products):
    """Sort a product list by canonical category order, then alphabetically within each category."""
    return sorted(products, key=lambda p: (_cat_rank(p), p.description))


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
        "customer_name": customer.name if customer else "--",
        "salesperson_name": salesperson.name if salesperson else "--",
        "status": order.status.value,
        "total": total,
        "line_count": len(order.line_items),
        "created_at": str(order.created_at),
        "reject_note": order.reject_note,
        "delivery_date": str(order.delivery_date) if order.delivery_date else None,
    }


# ---------- Login / Logout ---------------------------------------------------

@router.get("/app/login", response_class=HTMLResponse)
def login_page(request: Request):
    return render("login.html", error=None)


@router.post("/app/login")
async def do_login(request: Request, username: str = Form(...),
                   password: str = Form(...), db: Session = Depends(get_db)):
    import bcrypt as _bcrypt

    user = db.query(models.User).filter(models.User.login == username).first()

    # Generic error -- don't reveal whether it's the username or password that's wrong.
    bad_creds = "Invalid username or password. Please try again."

    if user is None:
        return render("login.html", error=bad_creds)

    # Block disabled accounts before doing any password work.
    if hasattr(user, 'is_active') and user.is_active is False:
        return render("login.html", error="Your account has been deactivated. Please contact admin.")

    # If no password has been set yet (legacy row from old seed), accept any password
    # so existing deployments aren't locked out -- but show a warning.
    if not user.password_hash:
        # Allow login but flag that password needs to be set.
        pass
    else:
        # Verify the submitted password against the stored bcrypt hash.
        try:
            password_ok = _bcrypt.checkpw(password.encode(), user.password_hash.encode())
        except Exception:
            password_ok = False
        if not password_ok:
            return render("login.html", error=bad_creds)

    # Send salespeople and admins straight to the new-order page (their main job).
    # Warehouse staff go to the orders list (they don't create orders).
    if user.role == models.UserRole.warehouse:
        dest = "/app/orders"
    else:
        dest = "/app/orders/new"
    response = RedirectResponse(dest, status_code=302)
    response.set_cookie("user_id", str(user.id), httponly=True, samesite="lax",
                        max_age=8 * 3600)   # session expires after 8 hours
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
    # Salespeople and admins can place orders.
    # (Admins often do sales too -- warehouse-only users are blocked.)
    if user.role == models.UserRole.warehouse:
        return RedirectResponse("/app/orders", status_code=302)
    products = (
        db.query(models.Product)
        .filter(models.Product.status == models.ProductStatus.active)
        .all()
    )
    # Allocation map: product_id -> allocation row for this salesperson.
    alloc_rows = db.query(models.Allocation).filter(
        models.Allocation.salesperson_id == user.id
    ).all()
    allocations = {a.product_id: a for a in alloc_rows}

    # Lots map: product_id -> list of lots (newest first).
    lots_map = {}
    for p in products:
        lots_map[p.id] = (
            db.query(models.Lot)
            .filter(models.Lot.product_id == p.id)
            .order_by(models.Lot.received_date.desc())
            .all()
        )

    # Previous price map for trend arrows: product_id -> old base_price
    prev_price_map = {}
    for p in products:
        last = (
            db.query(models.PriceHistory)
            .filter(models.PriceHistory.product_id == p.id)
            .order_by(models.PriceHistory.changed_at.desc())
            .first()
        )
        if last:
            prev_price_map[p.id] = last.old_price

    # Shared pools: check ALL products regardless of allocation mode.
    fcfs_pools = {}
    for p in products:
        pool = db.query(models.FcfsPool).filter(
            models.FcfsPool.product_id == p.id
        ).first()
        if pool:
            fcfs_pools[p.id] = pool

    # Order frequency: how many units this salesperson has ordered per product
    # (across all non-draft orders). Used to sort regulars to the top.
    from sqlalchemy import func as _func
    freq_rows = (
        db.query(
            models.OrderLineItem.product_id,
            _func.sum(models.OrderLineItem.quantity).label("total_qty"),
        )
        .join(models.Order, models.Order.id == models.OrderLineItem.order_id)
        .filter(
            models.Order.salesperson_id == user.id,
            models.Order.status != models.OrderStatus.draft,
        )
        .group_by(models.OrderLineItem.product_id)
        .all()
    )
    # Build freq map with 0 as default for every active product
    order_freq = {p.id: 0 for p in products}
    for r in freq_rows:
        if r.product_id in order_freq:
            order_freq[r.product_id] = int(r.total_qty)

    # Sort:  1) most-ordered items first (regulars)
    #        2) never-ordered items grouped by canonical category order (ON→SH→GI→PO→GA→CO→PA→BE→SP→DC)
    #        3) alphabetical within each category
    products.sort(key=lambda p: (
        0 if order_freq[p.id] > 0 else 1,   # regulars before newcomers
        -order_freq[p.id],                   # highest qty first within regulars
        _cat_rank(p),                        # canonical category sequence
        p.description,
    ))

    from datetime import date as _date
    return render("order_new.html",
        salesperson_id=user.id,
        role=user.role.value,
        user=user,
        products=products,
        order_freq=order_freq,
        allocations=allocations,
        fcfs_pools=fcfs_pools,
        lots=lots_map,
        prev_price_map=prev_price_map,
        today=_date.today().isoformat(),   # for date picker min= attribute
    )


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
            "product_name": product.description if product else "--",
            "unit": product.unit.value if product else "",
            "quantity": li.quantity,
            "unit_price": li.unit_price,
            "line_total": li.line_total,
            "lot_code": lot.lot_code if lot else None,
            "price_override": li.price_override,
            "override_reason": li.override_reason,
        })

    return render("order_detail.html",
        order=order,
        customer_name=customer.name if customer else "--",
        customer_whatsapp=(customer.whatsapp or "") if customer else "",
        salesperson_name=salesperson.name if salesperson else "--",
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

    products = _sort_products(db.query(models.Product).all())

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

    # Previous price map for trend arrows
    prev_price_map = {}
    for p in products:
        last = (
            db.query(models.PriceHistory)
            .filter(models.PriceHistory.product_id == p.id)
            .order_by(models.PriceHistory.changed_at.desc())
            .first()
        )
        if last:
            prev_price_map[p.id] = last.old_price

    # Shared pools: check ALL products regardless of allocation mode.
    fcfs_pools = {}
    for p in products:
        pool = db.query(models.FcfsPool).filter(
            models.FcfsPool.product_id == p.id
        ).first()
        if pool:
            fcfs_pools[p.id] = pool

    return render("products.html",
        products=products,
        allocations=allocations,
        fcfs_pools=fcfs_pools,
        lots=lots_map,
        prev_price_map=prev_price_map,
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
        product_name=product.description if product else "--",
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
            "id": a.id,
            "salesperson_id": a.salesperson_id,
            "salesperson_name": sp.name if sp else "--",
            "product_id": a.product_id,
            "product_name": pr.description if pr else "--",
            "triggered_at": str(a.triggered_at),
        })

    # Salespeople + products for the allocation tab
    salespeople = db.query(models.User).filter(
        models.User.role.in_([models.UserRole.salesperson, models.UserRole.admin]),
        models.User.is_active.is_(True),
    ).order_by(models.User.name).all()

    products = _sort_products(db.query(models.Product).all())

    # Current allocations: list of dicts for display
    alloc_rows = db.query(models.Allocation).all()
    alloc_map = {}  # (salesperson_id, product_id) -> allocation
    for a in alloc_rows:
        alloc_map[(a.salesperson_id, a.product_id)] = a

    # Shared pools: product_id -> pool row (ALL products, not just fcfs mode)
    fcfs_pools = {}
    for p in products:
        pool = db.query(models.FcfsPool).filter(
            models.FcfsPool.product_id == p.id
        ).first()
        if pool:
            fcfs_pools[p.id] = pool

    # Critical allocations: remaining <= 20% of allocated (or 0 remaining)
    critical_allocs = []
    for a in alloc_rows:
        if a.allocated_qty > 0 and (a.remaining_qty / a.allocated_qty) <= 0.20:
            sp = db.query(models.User).get(a.salesperson_id)
            pr = db.query(models.Product).get(a.product_id)
            critical_allocs.append({
                "salesperson_id": a.salesperson_id,
                "salesperson_name": sp.name if sp else "--",
                "product_id": a.product_id,
                "product_name": pr.description if pr else "--",
                "remaining_qty": a.remaining_qty,
                "unit": pr.unit.value if pr else "",
            })
    # Also flag critical FCFS pools
    for pid, pool in fcfs_pools.items():
        if pool.total_qty > 0 and (pool.available_qty / pool.total_qty) <= 0.20:
            pr = db.query(models.Product).get(pid)
            critical_allocs.append({
                "salesperson_id": 0,
                "salesperson_name": "Shared Pool",
                "product_id": pid,
                "product_name": pr.description if pr else "--",
                "remaining_qty": pool.available_qty,
                "unit": pr.unit.value if pr else "",
            })

    # Previous price per product: product_id -> last PriceHistory row
    prev_price_map = {}
    for p in products:
        last = (
            db.query(models.PriceHistory)
            .filter(models.PriceHistory.product_id == p.id)
            .order_by(models.PriceHistory.changed_at.desc())
            .first()
        )
        if last:
            prev_price_map[p.id] = last.old_price

    # Stock on hand per product: product_id -> total qty across all lots
    product_stock = {}
    for p in products:
        lots = db.query(models.Lot).filter(models.Lot.product_id == p.id).all()
        product_stock[p.id] = {
            "total": sum(l.qty_on_hand for l in lots),
            "unit": p.unit.value,
            "lots": [{"code": l.lot_code, "qty": l.qty_on_hand} for l in lots],
        }

    # ---------- Reports data --------------------------------------------------
    # Only count approved + pushed-to-sage orders for reports
    completed_statuses = (models.OrderStatus.approved, models.OrderStatus.pushed_to_sage)
    completed_orders = [o for o in all_raw if o.status in completed_statuses]

    # Sales by salesperson: {name: {total_rm, order_count, item_count}}
    sales_by_sp = {}
    for o in completed_orders:
        sp = db.query(models.User).get(o.salesperson_id)
        name = sp.name if sp else "--"
        if name not in sales_by_sp:
            sales_by_sp[name] = {"total_rm": 0.0, "order_count": 0, "item_count": 0}
        sales_by_sp[name]["order_count"] += 1
        for li in o.line_items:
            sales_by_sp[name]["total_rm"] += li.line_total
            sales_by_sp[name]["item_count"] += li.quantity

    # Sort by total RM descending
    sales_by_sp_sorted = sorted(sales_by_sp.items(), key=lambda x: x[1]["total_rm"], reverse=True)

    # Top products by volume: {description: {total_qty, total_rm, unit}}
    product_stats = {}
    for o in completed_orders:
        for li in o.line_items:
            pr = db.query(models.Product).get(li.product_id)
            desc = pr.description if pr else "--"
            if desc not in product_stats:
                product_stats[desc] = {"total_qty": 0, "total_rm": 0.0, "unit": pr.unit.value if pr else ""}
            product_stats[desc]["total_qty"] += li.quantity
            product_stats[desc]["total_rm"] += li.line_total
    top_products = sorted(product_stats.items(), key=lambda x: x[1]["total_qty"], reverse=True)[:10]

    # Top customers by revenue: {name: {total_rm, order_count}}
    customer_stats = {}
    for o in completed_orders:
        cust = db.query(models.Customer).get(o.customer_id)
        cname = cust.name if cust else "--"
        if cname not in customer_stats:
            customer_stats[cname] = {"total_rm": 0.0, "order_count": 0}
        customer_stats[cname]["order_count"] += 1
        for li in o.line_items:
            customer_stats[cname]["total_rm"] += li.line_total
    top_customers = sorted(customer_stats.items(), key=lambda x: x[1]["total_rm"], reverse=True)[:10]

    # Summary KPIs
    total_revenue = sum(s["total_rm"] for s in sales_by_sp.values())
    total_orders_completed = len(completed_orders)
    total_pending = len(pending_raw)

    return render("admin.html",
        pending_orders=[order_summary(o, db) for o in pending_raw],
        all_orders=[order_summary(o, db) for o in all_raw],
        stock_alerts=stock_alerts,
        salespeople=salespeople,
        products=products,
        alloc_map=alloc_map,
        fcfs_pools=fcfs_pools,
        critical_allocs=critical_allocs,
        prev_price_map=prev_price_map,
        product_stock=product_stock,
        # Reports data
        sales_by_sp=sales_by_sp_sorted,
        top_products=top_products,
        top_customers=top_customers,
        total_revenue=total_revenue,
        total_orders_completed=total_orders_completed,
        total_pending=total_pending,
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
