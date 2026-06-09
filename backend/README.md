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

## Database note

Phase 1 & 2 use **SQLite** (a single local file, zero install).
For production we switch to **PostgreSQL** by changing only the `DATABASE_URL`
line in `.env` — no code changes needed.
