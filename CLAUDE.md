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

**The solution:** An app where salespeople enter orders once. Orders sit in a staging
area for invoicing to review and approve. On approval, the order is written into Sage 300
via the official write path. Invoicing shifts from data-entry to checking only.

---

## 2. Order Flow (the core of the app)

```
draft -> submitted -> [invoicing reviews] -> approved -> pushed-to-sage
                                          \-> rejected (with note, back to salesperson)
```

- Salesperson: selects customer, adds products + quantities + prices, submits.
- Order enters "pending review" staging — NOT Sage yet.
- Invoicing: reviews, then approves or rejects with a note.
- On approval only: order is written to Sage 300.
- Posting an invoice is always a human action, never automatic.

---

## 3. Hard Rules (NEVER violate)

1. **Never write directly to Sage 300 database tables.** Use only the official Sage 300
   Web API (OData/REST) or the .NET SDK. Direct DB writes corrupt data and void support.
2. **Never fabricate** API endpoints, table names, field names, or capabilities. If
   unsure how a Sage 300 feature works, say so and ask me to confirm or look it up.
3. **Orders must pass through staging/approval** before reaching Sage. No shortcuts.
4. **Keep customer data within our own infrastructure.** Do not route it through external
   third-party services without flagging it to me first and getting approval.
5. **Never auto-post invoices or auto-push to Sage.** Approval is always a human click.
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

- **Salesperson** — create/edit own draft orders, submit them, view their own orders.
- **Invoicing/Admin** — view all submitted orders, approve/reject, trigger push to Sage.
- A salesperson must NOT be able to approve or push to Sage.

---

## 6. Data Model (baseline — refine as needed)

- **users** — id, name, role (salesperson / invoicing / admin), login.
- **customers** — id, sage_customer_code, name, pricing tier, contact.
- **products** — id, sage_item_code, description, unit, base price.
- **orders** — id, customer_id, salesperson_id, status, created_at, approved_by,
  approved_at, sage_order_ref, reject_note.
- **order_line_items** — id, order_id, product_id, quantity, unit_price, line_total.
- **order status** enum: draft | submitted | approved | pushed-to-sage | rejected.

Every field that maps to Sage must store the Sage code/reference, not just our internal id.

---

## 7. Build Phases (proof-of-concept first)

**Phase 1 — Foundation**
- Project skeleton, repo structure, README with run instructions.
- Data model + local database. No Sage connection yet.

**Phase 2 — Core app (no Sage)**
- Salesperson screens: customer search, product search/add, qty + price, summary, submit.
- Invoicing screens: pending list, order detail, approve/reject with note.
- Mock the "push to Sage" step as a placeholder function.

**Phase 3 — Sage integration**
- Replace the mock with the real Sage 300 write path, based on confirmed version/API details.
- Test against a Sage test company before going live.

Do not start Phase 3 until I have confirmed my Sage version and API access.

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
