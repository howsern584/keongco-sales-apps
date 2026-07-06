# Backend — Keongco Sales Order-Entry App

This is the **backend** (the "brain"): the API and database for the app.
Phase 1 sets up the foundation only — the database tables and a health-check page.
No salesperson/admin screens yet (those come in Phase 2).

## What's here

```
backend/
├── requirements.txt      # the Python packages needed
├── .env.example          # template for your settings (copy to .env)
└── app/
    ├── database.py       # connects to the database (SQLite now, PostgreSQL later)
    ├── models.py         # every table in the database, in plain Python
    └── main.py           # starts the server and creates the tables
```

## How to run it (Windows)

You need **Python 3.11+** installed. Then open PowerShell in this `backend` folder:

```powershell
# 1. Create a private space for this project's packages (a "virtual environment")
python -m venv .venv

# 2. Turn it on
.\.venv\Scripts\Activate.ps1

# 3. Install the packages
pip install -r requirements.txt

# 4. Create your settings file from the template
Copy-Item .env.example .env

# 5. Start the server
uvicorn app.main:app --reload
```

Then open **http://127.0.0.1:8000** in your browser. You should see a small
"status: ok" message. A file called `keongco.db` will appear — that's your
local database with all the tables created.

## Add sample data (optional but recommended)

To fill the database with a few demo customers, products, and lots so you can
click around, run this once (with the virtual environment active):

```powershell
python -m app.seed
```

## Try the app without writing code

While the server is running, open **http://127.0.0.1:8000/docs** in your
browser. FastAPI builds an interactive page listing every endpoint — you can
search customers, browse products, create an order, add lines, and submit it,
all by filling in forms and clicking "Execute". Great for testing by hand.

## Daily stock sync

The app syncs warehouse stock from the Sage 300 stock report (an Excel export).

- **Where the file lives:** by default the path in `app/import_stock.py` is used. For daily
  automation, set a **fixed** path via the `STOCK_REPORT_PATH` env var (in `.env`) and export
  the latest report to that same path each day.
- **Automatic:** while the server is running it syncs once a day on its own (and catches up on
  startup if it missed a day). See `app/scheduler.py`.
- **Manual:** admins can click **"Sync Stock Now"** in the Allocation tab, or run:
  ```powershell
  python -m app.import_stock
  ```
- **Always-on option (Windows Task Scheduler):** on a machine that's always on, schedule the
  command above to run daily. Create a Basic Task → Daily → *Start a program*:
  - Program: `<path>\backend\.venv\Scripts\python.exe`
  - Arguments: `-m app.import_stock`
  - Start in: `<path>\backend`
- The sync is **idempotent** (safe to run repeatedly) and only refreshes physical stock +
  adds new products; it never changes reps' weekly allocations.

> **Phase 3:** replace the Excel read in `app/import_stock.py` (`_read_report`) with a direct
> Sage 300 stock query — the rest of the sync stays the same.

## Database note

Phase 1 & 2 use **SQLite** (a single local file, zero install).
For production we switch to **PostgreSQL** by changing only the `DATABASE_URL`
line in `.env` — no code changes needed.
