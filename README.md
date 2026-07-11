# Sales Order-Entry App

A mobile-friendly order-entry app for Keongco's sales team, built to remove the
double data-entry between salespeople and the Sage 300 (Accpac) ERP.

## The problem it solves

Today salespeople take orders by WhatsApp/phone and type them up; invoicing then
re-keys everything into Sage 300. This app lets salespeople enter an order **once**.
Orders wait in a review area; invoicing approves them, and only then are they written
into Sage 300.

## How it works

```
Salesperson enters order
        ↓
"Pending review" staging  ← NOT in Sage yet
        ↓
Admin/invoicing approves (or rejects with a note)
        ↓
Order written into Sage 300 (Phase 3)
```

See [CLAUDE.md](CLAUDE.md) for the full project context, rules, and constraints.

## Key features

- **Order workflow** with staging and human approval before anything reaches Sage.
- **Per-salesperson stock allocation** (per product + unit), with shared, manual, or
  first-come-first-serve modes.
- **Price ranges** — salespeople adjust prices only within admin-set floor/ceiling.
- **Quality photos per lot** — warehouse staff upload photos salespeople can reference.
- **Roles**: salesperson, admin/invoicing, warehouse.

## Tech stack

- **Backend:** Python (FastAPI) + SQLAlchemy
- **Database:** SQLite for development → PostgreSQL for production
- **Frontend:** Mobile-friendly web app (Phase 2)
- **Sage 300:** official .NET SDK / Web API write path (Phase 3)

## Project structure

```
.
├── CLAUDE.md      # project context and rules
├── backend/       # the API and database (see backend/README.md to run it)
└── README.md
```

## Build phases

- **Phase 1 — Foundation** ✅ project skeleton + database model (no Sage, no screens)
- **Phase 2 — Core app** salesperson & admin screens, mocked Sage push
- **Phase 3 — Sage integration** real write path into Sage 300

To run the backend, see [backend/README.md](backend/README.md).

## License

MIT
