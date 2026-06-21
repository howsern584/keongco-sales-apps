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

def _sort_products(products):
    """Sort by category order, then unit_weight_kg high-to-low within each category."""
    return sorted(products, key=lambda p: (_cat_rank(p), -(p.unit_weight_kg or 0)))


def _bulk_lots(db, product_ids=None):
    """Return {product_id: [lot, ...]} in one query instead of N queries."""
    from collections import defaultdict
    q = db.query(models.Lot).order_by(models.Lot.product_id, models.Lot.received_date.desc())
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

    # Bulk loads — one query each instead of N per product
    pid_list = [p.id for p in products]
    lots_map    = _bulk_lots(db, pid_list)
    prev_price_map = _bulk_prev_prices(db)
    fcfs_pools  = _bulk_pools(db)

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

    # Option B: hide products with 0 available stock (alloc + pool = 0).
    # Products with no allocation AND no pool are also hidden — admin controls
    # visibility by setting an allocation or pool.
    def _avail(p):
        alloc = allocations.get(p.id)
        pool  = fcfs_pools.get(p.id)
        alloc_rem  = alloc.remaining_qty  if (alloc and alloc.allocated_qty > 0) else 0
        pool_avail = pool.available_qty   if (pool  and pool.total_qty      > 0) else 0
        return alloc_rem + pool_avail

    products = [p for p in products if _avail(p) > 0]

    # Rebuild order_freq for the filtered product list only
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
        -(p.unit_weight_kg or 0),            # heavy items first within category
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
