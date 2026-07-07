# CLAUDE.md — Sales Order-Entry App (Keongco / Sage 300 Accpac)

This file gives you persistent context and rules for this project. Read it at the
start of every session and follow it. If anything here conflicts with a request,
flag the conflict instead of silently overriding these rules.

---

## 1. Project Summary

We are building an **order-entry app** for the sales team of Keongco, a family-owned
fresh produce trading company in Malaysia.

**The problem:** Salespeople take orders by WhatsApp/phone, type them up manually, and
send them to invoicing, who re-key everything into our **Sage 300 (Accpac)** ERP. This
double-entry is slow and error-prone.

**The solution:** An app where salespeople enter orders once and push them into Sage 300
themselves via the official write path. Each salesperson owns their whole order: they
build it, confirm it (which reserves stock), and push it to Sage — no separate admin
approval step. Invoicing no longer re-keys orders.

> **Workflow note (changed):** An earlier design routed every order through an admin
> approval step before Sage. The owner decided to remove that step so reps are
> self-sufficient and faster. The trade-off is accepted: there is no second pair of eyes
> before an order reaches the ERP, so entry accuracy now rests with the salesperson.

---

## 2. Order Flow (the core of the app)

```
draft -> confirmed -> pushed-to-sage -> invoiced (LOCKED)
   ^          |             |
   |          |             +--> (Amend) reopen -> edit -> re-confirm -> re-push
   +----------+  (cancel / reopen returns reserved stock)
```

- Salesperson: selects customer, adds products + quantities + prices.
- **Confirm** finalizes the order and reserves stock (status `submitted` in the DB,
  shown as "Confirmed"). Still editable by reopening.
- **Push to Sage** is done by the salesperson themselves — the order is written to Sage 300.
- **Amend:** the salesperson can reopen their own *pushed* order, edit it, and push again.
  Until they re-push, Sage still holds the old copy (`needs_resync` flag), and the app
  warns them to re-push.
- **Invoiced = locked:** once invoicing converts the Sage OE into an invoice, they mark the
  order **Invoiced** (records the Sage invoice number). The order is then final — the
  salesperson can no longer amend it, and it shows an "Invoiced" badge. (Phase 3 can set
  this automatically by syncing the invoice number back from Sage.)
- Pushing to Sage is always a human action (a click), never automatic.

DB note: the `OrderStatus` enum keeps its original values for backward compatibility —
`submitted` = Confirmed, `rejected` = Cancelled, and `approved` is a legacy value from
the old approval flow (new orders never use it).

---

## 3. Hard Rules (NEVER violate)

1. **Never write directly to Sage 300 database tables.** Use only the official Sage 300
   Web API (OData/REST) or the .NET SDK. Direct DB writes corrupt data and void support.
2. **Never fabricate** API endpoints, table names, field names, or capabilities. If
   unsure how a Sage 300 feature works, say so and ask me to confirm or look it up.
3. **Orders must be CONFIRMED (stock reserved) before they can be pushed to Sage.** A raw
   draft cannot go straight to Sage. (There is no admin approval step — the salesperson
   confirms and pushes their own order.)
4. **Keep customer data within our own infrastructure.** Do not route it through external
   third-party services without flagging it to me first and getting approval.
5. **Never auto-push to Sage.** Pushing is always a deliberate human click by the order's
   owner. Amending an already-pushed order requires an explicit re-push to re-sync Sage.
6. **Never overwrite or delete my files** without telling me first.
7. **Validate against a Sage test company/dataset** before anything touches live data.

---

## 4. Architecture

```
[Sales App]  ->  [Middleware/API]  ->  [Sage 300 Web API / SDK]  ->  [Accpac DB]
                  auth, validation,        official write path
                  staging, caching
```

The middleware layer must:
- Hold orders in a "pending review" state.
- Cache the product list and customer-specific pricing for fast mobile entry.
- Handle authentication and role-based access.

---

## 5. Roles

- **Salesperson** — create/edit their own orders, confirm them, **push their own orders to
  Sage**, amend and re-push their own pushed orders, and view their own orders.
- **Invoicing/Admin** — manage products, pricing, allocations, customers and users; view a
  read-only overview of all orders and reports. Admins are also salespeople and act on
  THEIR OWN orders exactly like any rep.
- No one approves or pushes another rep's order on their behalf — each rep owns their own.

---

## 6. Data Model (baseline — refine as needed)

- **users** — id, name, role (salesperson / invoicing / admin), login.
- **customers** — id, sage_customer_code, name, pricing tier, contact.
- **products** — id, sage_item_code, description, unit, base price.
- **orders** — id, customer_id, salesperson_id, status, created_at, pushed_at,
  needs_resync, sage_order_ref, sage_invoice_no, reject_note. (`approved_by`/`approved_at`
  are legacy columns kept for old rows.)
- **order_line_items** — id, order_id, product_id, quantity, unit_price, line_total.
- **order status** enum (DB values): draft | submitted (= Confirmed) | approved (legacy) |
  pushed-to-sage | rejected (= Cancelled).

Every field that maps to Sage must store the Sage code/reference, not just our internal id.

---

## 7. Build Phases (proof-of-concept first)

**Phase 1 — Foundation**
- Project skeleton, repo structure, README with run instructions.
- Data model + local database. No Sage connection yet.

**Phase 2 — Core app (no Sage)**
- Salesperson screens: customer search, product search/add, qty + price, summary, confirm,
  push to Sage, and amend/re-push their own pushed orders.
- Admin screens: read-only orders overview, plus products, pricing, allocations, customers,
  users, and reports.
- Mock the "push to Sage" step as a placeholder function.

**Phase 3 — Sage integration**
- Replace the mock with the real Sage 300 write path, based on confirmed version/API details.
- **Pull stock directly from the Sage 300 server** (replacing the Excel report — swap only
  `_read_report` in `app/import_stock.py`).
- Test against a Sage test company before going live.

Do not start Phase 3 until I have confirmed my Sage version and API access.

---

## 7a. Stock sync & allocation

- **Daily stock sync** (`app/import_stock.py` → `sync_stock`) refreshes physical stock only
  (`Lot.qty_on_hand`). It is idempotent and **decoupled** from allocation — it never changes
  reps' weekly allocations. Runs via the in-app scheduler (`app/scheduler.py`), a manual
  "Sync Stock Now" button, or `python -m app.import_stock`. Source path = `STOCK_REPORT_PATH`.
- **Weekly allocation** (`auto_calculate_presets` + `apply_presets`) sets each rep's per-product
  ceiling from their sales share × stock, on the configured reset day.
- **New / no-history products** can't use a sales share yet, so they go into a shared **FCFS
  pool** sized to `new_product_pool_pct`% of stock (default 70%, editable in the Auto-Calculate
  rules). Once they build sales history, auto-calculate converts them to per-rep presets and
  zeroes the pool.
- **Stock shortfall** (synced stock < what's still allocated) shows an admin warning in the
  Allocation tab — it does **not** hard-block orders.
- **Manual shared pools are protected:** a pool the admin sets by hand (`FcfsPool.manual`) is
  never wiped, resized or converted by the weekly reset or the new-product auto-fallback.

---

## 8. Open Questions (confirm with me before assuming)

- Sage 300 version, and whether the Web API is installed/enabled or desktop-client only.
- On-premise vs hosted, and the server OS.
- Platform: mobile app, web app, or both.
- Are prices fixed per customer in Sage, or can salespeople adjust them?
- Scale: rough number of salespeople, customers, and SKUs.

---

## 9. How to Work With Me

- I'm a capable beginner, not a developer. Explain in plain language.
- Work in small, reviewable steps. Show me what and why before large changes.
- Comment code clearly so I can understand and maintain it.
- Flag any decision with security, cost, or data-integrity implications.
- When in doubt, ask. Never invent endpoints, numbers, or file contents.
