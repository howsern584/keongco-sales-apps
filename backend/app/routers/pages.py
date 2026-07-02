"""
pages.py
--------
Serves the HTML pages (the screens users see in their browser).

All pages are protected by a simple session cookie -- if not logged in,
you get redirected to the login page.

Authentication in Phase 2 uses a plain session cookie storing the user id.
(Phase 3 / production should upgrade this to a proper JWT or OAuth flow.)
"""

import re as _re

from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import func as _func2
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models

router = APIRouter()

# Render templates directly with Jinja2 -- no Starlette wrapper needed.
_env = Environment(loader=FileSystemLoader("app/templates", encoding="utf-8"), autoescape=True, auto_reload=True)
_env.filters["order_no"] = lambda v: f"SC{int(v):06d}"

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

def _nat_key(s: str):
    """Natural sort key: splits text into alternating string/float parts so that
    numbers embedded in descriptions sort numerically rather than lexicographically.
    e.g. 'CILI YD 3KG' < 'CILI YD 10KG'  and  '4.5MM' < '6MM' < '9MM'.
    """
    parts = _re.split(r'(\d+(?:\.\d+)?)', s.upper())
    return [float(p) if _re.fullmatch(r'\d+(?:\.\d+)?', p) else p for p in parts]

def _sort_products(products):
    """Sort by category order, then natural sort on description within each category.
    Natural sort means numbers inside names are compared numerically:
    '4.5MM' < '6MM', '3KG' < '10KG', etc.
    """
    return sorted(products, key=lambda p: (_cat_rank(p), _nat_key(p.description)))


def _bulk_lots(db, product_ids=None):
    """Return {product_id: [lot, ...]} in one query instead of N queries.

    Lots are returned in SELL ORDER: admin-prioritised lots first (by
    sale_priority ascending), then the rest in FIFO order (oldest received
    first). This is the sequence the New Order screen defaults to.
    """
    from collections import defaultdict
    q = db.query(models.Lot).order_by(
        models.Lot.product_id,
        models.Lot.sale_priority.is_(None),   # lots with a set priority come first
        models.Lot.sale_priority.asc(),
        models.Lot.received_date.asc(),        # FIFO fallback (oldest first)
    )
    if product_ids is not None:
        q = q.filter(models.Lot.product_id.in_(product_ids))
    result = defaultdict(list)
    for lot in q.all():
        result[lot.product_id].append(lot)
    return result


def _bulk_pools(db):
    """Return {product_id: FcfsPool} in one query instead of N queries."""
    return {p.product_id: p for p in db.query(models.FcfsPool).all()}


def _bulk_prev_prices(db):
    """Return {product_id: old_price} for the most recent price change per product."""
    from sqlalchemy import func as _f
    subq = (
        db.query(
            models.PriceHistory.product_id,
            _f.max(models.PriceHistory.changed_at).label("max_at"),
        )
        .group_by(models.PriceHistory.product_id)
        .subquery()
    )
    rows = (
        db.query(models.PriceHistory)
        .join(
            subq,
            (models.PriceHistory.product_id == subq.c.product_id)
            & (models.PriceHistory.changed_at == subq.c.max_at),
        )
        .all()
    )
    return {r.product_id: r.old_price for r in rows}


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


def orders_summary_query(db, salesperson_id=None, status=None, order_asc=False, limit=None):
    """Single query that returns order summary data without N+1 lookups."""
    order_col = models.Order.created_at.asc() if order_asc else models.Order.created_at.desc()
    q = (
        db.query(
            models.Order.id,
            models.Order.status,
            models.Order.created_at,
            models.Order.delivery_date,
            models.Order.reject_note,
            models.Customer.name.label("customer_name"),
            models.User.name.label("salesperson_name"),
            _func2.coalesce(_func2.sum(models.OrderLineItem.line_total), 0).label("total"),
            _func2.count(models.OrderLineItem.id).label("line_count"),
        )
        .join(models.Customer, models.Customer.id == models.Order.customer_id)
        .join(models.User, models.User.id == models.Order.salesperson_id)
        .outerjoin(models.OrderLineItem, models.OrderLineItem.order_id == models.Order.id)
        .group_by(models.Order.id)
        .order_by(order_col)
    )
    if salesperson_id is not None:
        q = q.filter(models.Order.salesperson_id == salesperson_id)
    if status is not None:
        q = q.filter(models.Order.status == status)
    if limit is not None:
        q = q.limit(limit)
    rows = q.all()
    return [
        {
            "id": r.id,
            "status": r.status.value,
            "created_at": str(r.created_at),
            "delivery_date": str(r.delivery_date) if r.delivery_date else None,
            "reject_note": r.reject_note,
            "customer_name": r.customer_name or "--",
            "salesperson_name": r.salesperson_name or "--",
            "total": float(r.total),
            "line_count": r.line_count,
        }
        for r in rows
    ]


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
        # Admin sees everyone's orders — cap at the 300 most recent so the page
        # stays fast/light even with thousands of historical orders.
        orders = orders_summary_query(db, limit=300)
    else:
        orders = orders_summary_query(db, salesperson_id=user.id)
    return render("orders.html", orders=orders, role=user.role.value, user=user)


@router.get("/app/orders/new", response_class=HTMLResponse)
def new_order_page(request: Request, edit: int | None = None, db: Session = Depends(get_db)):
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

    # ── Edit mode: preload an existing DRAFT order the current user OWNS ──
    # Own orders only — a salesperson (incl. an admin acting as a rep) may edit
    # only their own order. Admins cannot edit another rep's order (approve/reject
    # only); enforced here server-side, not just by hiding the button.
    edit_order = None
    edit_lines = {}          # product_id -> {qty, price, lot_id, lot_note}
    edit_customer = None
    if edit:
        _o = db.query(models.Order).get(edit)
        if (_o and _o.status == models.OrderStatus.draft
                and _o.salesperson_id == user.id):
            edit_order = _o
            edit_customer = db.query(models.Customer).get(_o.customer_id)
            for li in _o.line_items:
                edit_lines[li.product_id] = {
                    "qty":      li.quantity,
                    "price":    li.unit_price,
                    "lot_id":   li.lot_id,
                    "lot_note": li.lot_note or "",
                }
    # Allocation map: product_id -> allocation row for this salesperson.
    alloc_rows = db.query(models.Allocation).filter(
        models.Allocation.salesperson_id == user.id
    ).all()
    allocations = {a.product_id: a for a in alloc_rows}

    # Bulk loads — one query each instead of N per product
    pid_list = [p.id for p in products]
    lots_map    = _bulk_lots(db, pid_list)
    prev_price_map = _bulk_prev_prices(db)
    fcfs_pools  = _bulk_pools(db)

    # Order volume: units this salesperson has sold per product over the LAST
    # MONTH (30 days). Anchored to the latest order in the system so historical
    # test data still shows numbers; in production the latest order ≈ today, so
    # this is effectively "sold in the last month". Shown as "×qty" per product
    # and used to sort their regulars to the top.
    from datetime import datetime, timedelta
    from sqlalchemy import func as _func
    _anchor = db.query(_func.max(models.Order.created_at)).scalar() or datetime.utcnow()
    _month_ago = _anchor - timedelta(days=30)
    freq_rows = (
        db.query(
            models.OrderLineItem.product_id,
            _func.sum(models.OrderLineItem.quantity).label("total_qty"),
        )
        .join(models.Order, models.Order.id == models.OrderLineItem.order_id)
        .filter(
            models.Order.salesperson_id == user.id,
            models.Order.status != models.OrderStatus.draft,
            models.Order.created_at >= _month_ago,
        )
        .group_by(models.OrderLineItem.product_id)
        .all()
    )
    # Build freq map with 0 as default for every active product
    order_freq = {p.id: 0 for p in products}
    for r in freq_rows:
        if r.product_id in order_freq:
            order_freq[r.product_id] = int(r.total_qty)

    # Option B: hide products with 0 available stock (alloc + pool = 0).
    # Products with no allocation AND no pool are also hidden — admin controls
    # visibility by setting an allocation or pool.
    def _avail(p):
        alloc = allocations.get(p.id)
        pool  = fcfs_pools.get(p.id)
        alloc_rem  = alloc.remaining_qty  if (alloc and alloc.allocated_qty > 0) else 0
        pool_avail = pool.available_qty   if (pool  and pool.total_qty      > 0) else 0
        return alloc_rem + pool_avail

    # Keep products with available stock, plus any already in the order being edited.
    products = [p for p in products if _avail(p) > 0 or p.id in edit_lines]

    # Rebuild order_freq for the filtered product list only
    order_freq = {p.id: 0 for p in products}
    for r in freq_rows:
        if r.product_id in order_freq:
            order_freq[r.product_id] = int(r.total_qty)

    # Sort:  1) most-ordered items first (regulars)
    #        2) never-ordered items grouped by canonical category order (ON→SH→GI→PO→GA→CO→PA→BE→SP→DC)
    #        3) natural sort on description within each tier/category
    products.sort(key=lambda p: (
        0 if order_freq[p.id] > 0 else 1,   # regulars before newcomers
        -order_freq[p.id],                   # highest qty first within regulars
        _cat_rank(p),                        # canonical category sequence
        _nat_key(p.description),             # description → size → weight (natural numeric sort)
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
        # Edit mode (None when creating a fresh order)
        edit_order_id=edit_order.id if edit_order else None,
        edit_customer_id=edit_customer.id if edit_customer else None,
        edit_customer_name=edit_customer.name if edit_customer else None,
        edit_delivery=(edit_order.delivery_date.date().isoformat()
                       if edit_order and edit_order.delivery_date else ""),
        edit_notes=(edit_order.order_notes if edit_order else "") or "",
        edit_transport=(edit_order.transport if edit_order else "") or "",
        edit_customer_po=(edit_order.customer_po if edit_order else "") or "",
        edit_lines=edit_lines,
    )


@router.get("/app/orders/{order_id}", response_class=HTMLResponse)
def order_detail_page(order_id: int, request: Request, db: Session = Depends(get_db)):
    user, redirect = login_required(request, db)
    if redirect:
        return redirect

    order = db.query(models.Order).get(order_id)
    if order is None:
        return RedirectResponse("/app/orders", status_code=302)

    # Salespersons may only view their own orders.
    if user.role == models.UserRole.salesperson and order.salesperson_id != user.id:
        return RedirectResponse("/app/orders", status_code=302)

    customer  = db.query(models.Customer).get(order.customer_id)
    salesperson = db.query(models.User).get(order.salesperson_id)
    total = sum(li.line_total for li in order.line_items)

    # Bulk-load products and lots for all line items — no N+1
    _prod_ids = [li.product_id for li in order.line_items]
    _lot_ids  = [li.lot_id for li in order.line_items if li.lot_id]
    _prods = {p.id: p for p in db.query(models.Product).filter(models.Product.id.in_(_prod_ids)).all()}
    _lots  = {l.id: l for l in db.query(models.Lot).filter(models.Lot.id.in_(_lot_ids)).all()} if _lot_ids else {}

    lines = []
    for li in order.line_items:
        product = _prods.get(li.product_id)
        lot     = _lots.get(li.lot_id) if li.lot_id else None
        lines.append({
            "product_name":   product.description if product else "--",
            "unit":           product.unit.value if product else "",
            "quantity":       li.quantity,
            "unit_price":     li.unit_price,
            "line_total":     li.line_total,
            "lot_code":       lot.lot_code if lot else None,
            "lot_note":       li.lot_note,
            "price_override": li.price_override,
            "override_reason":li.override_reason,
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
        # Only the order's own owner may edit it. Admins are sales reps too, so they
        # can edit THEIR OWN orders — but for another rep's order an admin may only
        # approve/reject, not edit.
        is_own_order=(order.salesperson_id == user.id),
        user=user,
    )


# ---------- Price Updates (notifications) ------------------------------------

@router.get("/app/price-updates", response_class=HTMLResponse)
def price_updates_page(request: Request, db: Session = Depends(get_db)):
    """Salesperson-facing feed of recent price changes. Opening this page marks all
    changes up to now as 'seen' for this user (clears the bell badge)."""
    from datetime import datetime as _dt
    user, redirect = login_required(request, db)
    if redirect:
        return redirect

    prev_seen = user.prices_seen_at   # capture BEFORE marking seen, to flag what's new

    events = (
        db.query(models.PriceChangeEvent)
        .order_by(models.PriceChangeEvent.changed_at.desc())
        .limit(100)
        .all()
    )
    # Bulk-load product descriptions and changer names — no N+1.
    prod_ids = {e.product_id for e in events}
    user_ids = {e.changed_by for e in events}
    prods = {p.id: p for p in db.query(models.Product).filter(models.Product.id.in_(prod_ids)).all()} if prod_ids else {}
    users = {u.id: u for u in db.query(models.User).filter(models.User.id.in_(user_ids)).all()} if user_ids else {}

    _LABELS = {"floor": "Min", "base": "Base", "ceiling": "Max", "special": "Discount"}
    _ORDER  = {"floor": 0, "base": 1, "ceiling": 2, "special": 3}  # display order

    def _fmt(v):
        return f"RM {v:.2f}" if v is not None else None

    def _part(e):
        """Compact one-field change, e.g. 'Base 20.50→22.00 ▲' or 'Discount set 9.90'."""
        old_s, new_s = _fmt(e.old_value), _fmt(e.new_value)
        if old_s is None and new_s is not None:
            text, up = f"set {new_s}", None
        elif new_s is None and old_s is not None:
            text, up = f"removed (was {old_s})", None
        elif old_s is not None and new_s is not None:
            up = (e.new_value or 0) > (e.old_value or 0)
            text = f"{old_s}→{new_s} {'▲' if up else '▼'}"
        else:
            text, up = "changed", None
        return {"label": _LABELS.get(e.field, e.field), "text": text, "up": up}

    # Group all fields from one edit (same product + same timestamp) into ONE row.
    groups = {}   # (product_id, changed_at) -> list[event]   (dict preserves insert order)
    for e in events:                       # events are already newest-first
        groups.setdefault((e.product_id, e.changed_at), []).append(e)

    rows = []
    for (product_id, changed_at), evs in groups.items():
        first = evs[0]
        prod = prods.get(product_id)
        parts = [_part(e) for e in sorted(evs, key=lambda e: _ORDER.get(e.field, 9))]
        rows.append({
            "product": prod.description if prod else f"Product #{product_id}",
            "parts":   parts,
            "by":      (users.get(first.changed_by).name if users.get(first.changed_by) else "—"),
            "when":    changed_at.strftime("%d %b %Y, %H:%M") if changed_at else "",
            "is_new":  (first.changed_by != user.id and (prev_seen is None or (changed_at and changed_at > prev_seen))),
        })

    # Mark everything up to now as seen for this user (clears the badge).
    user.prices_seen_at = _dt.utcnow()
    db.commit()

    return render("price_updates.html", rows=rows, role=user.role.value, user=user)


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

    # Bulk loads — one query each instead of N per product
    lots_map       = _bulk_lots(db)
    prev_price_map = _bulk_prev_prices(db)
    fcfs_pools     = _bulk_pools(db)

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

    # Build alerts with names — single joined query (no N+1)
    alerts_raw = (
        db.query(models.StockAlert, models.User, models.Product)
        .join(models.User,    models.User.id    == models.StockAlert.salesperson_id)
        .join(models.Product, models.Product.id == models.StockAlert.product_id)
        .filter(models.StockAlert.resolved.is_(False))
        .order_by(models.StockAlert.triggered_at.desc())
        .all()
    )
    stock_alerts = [
        {
            "id":               a.id,
            "salesperson_id":   a.salesperson_id,
            "salesperson_name": sp.name,
            "product_id":       a.product_id,
            "product_name":     pr.description,
            "triggered_at":     str(a.triggered_at),
        }
        for a, sp, pr in alerts_raw
    ]

    # Customers + Users tabs need the salesperson list + groups (server-side rendered).
    # The heavy All Orders and Prices tabs are loaded lazily (see /admin/tab/orders
    # and /admin/tab/prices) so the initial admin DOM stays light.
    salesperson_users = db.query(models.User).filter(
        models.User.role == models.UserRole.salesperson,
        models.User.is_active.is_(True),
    ).order_by(models.User.name).all()

    all_groups = db.query(models.UserGroup).order_by(models.UserGroup.name).all()
    group_members = {}
    for sp in salesperson_users:
        if sp.group_id:
            group_members.setdefault(sp.group_id, []).append(sp)

    return render("admin.html",
        pending_orders=orders_summary_query(db, status=models.OrderStatus.submitted, order_asc=True),
        stock_alerts=stock_alerts,
        salesperson_users=salesperson_users,
        all_groups=all_groups,
        group_members=group_members,
        role=user.role.value,
        user=user,
    )


# ---------- Admin lazy-data endpoints ----------------------------------------

@router.get("/admin/tab/orders", response_class=HTMLResponse)
def admin_tab_orders(request: Request, db: Session = Depends(get_db)):
    """All Orders tab HTML fragment — fetched lazily on first tab click.
    Capped at the 300 most-recent orders so the page stays light."""
    user, redirect = login_required(request, db)
    if redirect:
        return redirect
    if user.role != models.UserRole.admin:
        return HTMLResponse("", status_code=403)

    return HTMLResponse(_env.get_template("admin_orders_tab.html").render(
        all_orders=orders_summary_query(db, limit=200),
    ))


@router.get("/admin/tab/prices", response_class=HTMLResponse)
def admin_tab_prices(request: Request, db: Session = Depends(get_db)):
    """Prices tab HTML fragment — fetched lazily on first tab click."""
    user, redirect = login_required(request, db)
    if redirect:
        return redirect
    if user.role != models.UserRole.admin:
        return HTMLResponse("", status_code=403)

    products       = _sort_products(db.query(models.Product).all())
    prev_price_map = _bulk_prev_prices(db)
    return HTMLResponse(_env.get_template("admin_prices_tab.html").render(
        products=products,
        prev_price_map=prev_price_map,
    ))


@router.get("/admin/tab/allocations", response_class=HTMLResponse)
def admin_tab_allocations(request: Request, db: Session = Depends(get_db)):
    """Allocation tab HTML fragment — fetched lazily on first tab click."""
    user, redirect = login_required(request, db)
    if redirect:
        return redirect
    if user.role != models.UserRole.admin:
        return HTMLResponse("", status_code=403)

    salespeople = db.query(models.User).filter(
        models.User.role.in_([models.UserRole.salesperson, models.UserRole.admin]),
        models.User.is_active.is_(True),
    ).order_by(models.User.name).all()

    products = _sort_products(db.query(models.Product).all())

    alloc_rows = db.query(models.Allocation).all()
    alloc_map = {(a.salesperson_id, a.product_id): a for a in alloc_rows}

    fcfs_pools     = _bulk_pools(db)
    lots_map       = _bulk_lots(db)

    product_stock = {
        p.id: {
            "total": sum(l.qty_on_hand for l in lots_map.get(p.id, [])),
            "unit":  p.unit.value,
            "lots":  [{"code": l.lot_code, "qty": l.qty_on_hand} for l in lots_map.get(p.id, [])],
        }
        for p in products
    }

    # ── Stock-vs-weekly-sales attention flags ──────────────────────────────
    # Flag products whose current stock (in metric tons) is below the average
    # weekly sales volume (in MT) — they need allocation control attention.
    # Weekly volume = MT sold over the last 8 weeks of order history ÷ 8.
    from datetime import datetime, timedelta
    LOOKBACK_WEEKS = 8
    anchor = db.query(_func2.max(models.Order.created_at)).scalar() or datetime.utcnow()
    cutoff = anchor - timedelta(weeks=LOOKBACK_WEEKS)

    sold_rows = (
        db.query(
            models.OrderLineItem.product_id,
            _func2.sum(models.OrderLineItem.quantity).label("units"),
        )
        .join(models.Order, models.Order.id == models.OrderLineItem.order_id)
        .filter(
            models.Order.created_at >= cutoff,
            models.Order.status.in_([
                models.OrderStatus.submitted,
                models.OrderStatus.approved,
                models.OrderStatus.pushed_to_sage,
            ]),
        )
        .group_by(models.OrderLineItem.product_id)
        .all()
    )
    sold_units = {r.product_id: int(r.units or 0) for r in sold_rows}

    attention_map = {}   # product_id -> {stock_mt, weekly_mt, deficit_mt, _ratio}
    MIN_WEEKLY_MT = 0.1  # ignore negligible-volume products (< 100 kg/week)
    for p in products:
        w = p.unit_weight_kg or 0
        if w <= 0:
            continue   # can't express this product in MT
        stock_mt  = product_stock.get(p.id, {}).get("total", 0) * w / 1000.0
        weekly_mt = (sold_units.get(p.id, 0) * w / 1000.0) / LOOKBACK_WEEKS
        if weekly_mt >= MIN_WEEKLY_MT and 0 < stock_mt < weekly_mt:
            attention_map[p.id] = {
                "stock_mt":   round(stock_mt, 1),
                "weekly_mt":  round(weekly_mt, 1),
                "deficit_mt": round(weekly_mt - stock_mt, 1),
                "_ratio":     stock_mt / weekly_mt,   # raw, for sorting
            }

    # Most critical first (lowest stock-to-weekly ratio).
    attention_products = sorted(
        [p for p in products if p.id in attention_map],
        key=lambda p: attention_map[p.id]["_ratio"],
    )

    sp_map = {sp.id: sp for sp in salespeople}
    pr_map = {p.id: p for p in products}
    critical_allocs = []
    for a in alloc_rows:
        if a.allocated_qty > 0 and (a.remaining_qty / a.allocated_qty) <= 0.20:
            sp = sp_map.get(a.salesperson_id)
            pr = pr_map.get(a.product_id)
            critical_allocs.append({
                "salesperson_id": a.salesperson_id,
                "salesperson_name": sp.name if sp else "--",
                "product_id": a.product_id,
                "product_name": pr.description if pr else "--",
                "remaining_qty": a.remaining_qty,
                "unit": pr.unit.value if pr else "",
            })
    for pid, pool in fcfs_pools.items():
        if pool.total_qty > 0 and (pool.available_qty / pool.total_qty) <= 0.20:
            pr = pr_map.get(pid)
            critical_allocs.append({
                "salesperson_id": 0,
                "salesperson_name": "Shared Pool",
                "product_id": pid,
                "product_name": pr.description if pr else "--",
                "remaining_qty": pool.available_qty,
                "unit": pr.unit.value if pr else "",
            })

    salesperson_users = db.query(models.User).filter(
        models.User.role == models.UserRole.salesperson,
        models.User.is_active.is_(True),
    ).order_by(models.User.name).all()

    all_groups = db.query(models.UserGroup).order_by(models.UserGroup.name).all()
    group_members = {}
    for sp in salesperson_users:
        if sp.group_id:
            group_members.setdefault(sp.group_id, []).append(sp)

    preset_rows = db.query(models.ProductPreset).all()
    preset_map = {}
    for r in preset_rows:
        if r.user_id:
            preset_map[(r.product_id, 'user', r.user_id)] = r.weekly_qty
        elif r.group_id:
            preset_map[(r.product_id, 'group', r.group_id)] = r.weekly_qty

    # Preset schedule settings (create row with defaults if absent)
    schedule = db.query(models.AllocationSettings).first()
    if schedule is None:
        schedule = models.AllocationSettings()
        db.add(schedule)
        db.commit()
        db.refresh(schedule)

    return HTMLResponse(_env.get_template("admin_alloc_tab.html").render(
        salespeople=salespeople,
        products=products,
        alloc_map=alloc_map,
        fcfs_pools=fcfs_pools,
        critical_allocs=critical_allocs,
        product_stock=product_stock,
        all_groups=all_groups,
        group_members=group_members,
        preset_map=preset_map,
        schedule=schedule,
        attention_map=attention_map,
        attention_products=attention_products,
    ))


@router.get("/admin/reports-data")
def admin_reports_data(request: Request, db: Session = Depends(get_db)):
    """Reports tab data — fetched lazily on first tab click, not on page load."""
    user, redirect = login_required(request, db)
    if redirect:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "not logged in"}, status_code=401)
    if user.role != models.UserRole.admin:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "forbidden"}, status_code=403)

    from sqlalchemy import func as _func
    completed = (models.OrderStatus.approved, models.OrderStatus.pushed_to_sage)

    sp_rows = (
        db.query(
            models.User.name,
            _func.count(models.Order.id.distinct()).label("order_count"),
            _func.coalesce(_func.sum(models.OrderLineItem.line_total), 0).label("total_rm"),
            _func.coalesce(_func.sum(models.OrderLineItem.quantity), 0).label("item_count"),
        )
        .join(models.Order, models.Order.salesperson_id == models.User.id)
        .join(models.OrderLineItem, models.OrderLineItem.order_id == models.Order.id)
        .filter(models.Order.status.in_(completed))
        .group_by(models.User.id)
        .all()
    )
    sales_by_sp = sorted(
        [{"name": r.name, "total_rm": float(r.total_rm),
          "order_count": r.order_count, "item_count": int(r.item_count)}
         for r in sp_rows],
        key=lambda x: x["total_rm"], reverse=True
    )

    prod_rows = (
        db.query(
            models.Product.description,
            models.Product.unit,
            _func.coalesce(_func.sum(models.OrderLineItem.quantity), 0).label("total_qty"),
            _func.coalesce(_func.sum(models.OrderLineItem.line_total), 0).label("total_rm"),
        )
        .join(models.OrderLineItem, models.OrderLineItem.product_id == models.Product.id)
        .join(models.Order, models.Order.id == models.OrderLineItem.order_id)
        .filter(models.Order.status.in_(completed))
        .group_by(models.Product.id)
        .order_by(_func.sum(models.OrderLineItem.quantity).desc())
        .limit(10)
        .all()
    )
    top_products = [{"name": r.description, "unit": r.unit.value,
                     "total_qty": int(r.total_qty), "total_rm": float(r.total_rm)}
                    for r in prod_rows]

    cust_rows = (
        db.query(
            models.Customer.name,
            _func.count(models.Order.id.distinct()).label("order_count"),
            _func.coalesce(_func.sum(models.OrderLineItem.line_total), 0).label("total_rm"),
        )
        .join(models.Order, models.Order.customer_id == models.Customer.id)
        .join(models.OrderLineItem, models.OrderLineItem.order_id == models.Order.id)
        .filter(models.Order.status.in_(completed))
        .group_by(models.Customer.id)
        .order_by(_func.sum(models.OrderLineItem.line_total).desc())
        .limit(10)
        .all()
    )
    top_customers = [{"name": r.name, "total_rm": float(r.total_rm),
                      "order_count": r.order_count}
                     for r in cust_rows]

    total_revenue = sum(s["total_rm"] for s in sales_by_sp)
    total_completed = db.query(models.Order).filter(
        models.Order.status.in_(completed)).count()
    total_pending = db.query(models.Order).filter(
        models.Order.status == models.OrderStatus.submitted).count()

    from fastapi.responses import JSONResponse
    return JSONResponse({
        "total_revenue":    total_revenue,
        "total_completed":  total_completed,
        "total_pending":    total_pending,
        "sales_by_sp":      sales_by_sp,
        "top_products":     top_products,
        "top_customers":    top_customers,
    })


# ---------- Root redirect ----------------------------------------------------

@router.get("/")
def root():
    return RedirectResponse("/app/login", status_code=302)


@router.get("/app")
def app_root():
    return RedirectResponse("/app/orders", status_code=302)
