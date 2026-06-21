---
name: start
description: Start the Keongco dev server. Use when the user says "start the server" or types /start. Pulls the latest from GitHub first (keeps the desktop and laptop in sync), launches the FastAPI server, then shares the local link.
---

# Start the server

When the user asks to start the server, do these steps in order. Run all commands
from the project root (cd there first).

1. **Sync from GitHub FIRST.** Run `git pull` on the current branch so this machine
   has the latest work from the other machine (desktop ↔ laptop sync goes through
   GitHub, never Dropbox). If the pull reports a conflict, STOP and show it to the
   user — do not start the server until it's resolved.

2. **Free port 8000 if a stale server is squatting on it.** A leftover `python.exe`
   can hold port 8000 and serve *old* code (this has bitten us before). Check and
   kill it if present, e.g. (PowerShell):
   `Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }`

3. **Launch the server** via the Claude Preview tool: `preview_start` with name
   `keongco-backend` (configured in `.claude/launch.json` — runs uvicorn with
   `--reload` from `backend/`).
   - Manual fallback, from `backend/`:
     `python -m uvicorn app.main:app --reload --port 8000`

4. **Confirm startup** (check the logs for "Application startup complete") and
   **share the link:** http://127.0.0.1:8000

Notes:
- Admin login is `admin`; salesperson passwords are `0000`.
- uvicorn `--reload` picks up template and Python changes automatically; a full
  restart is only needed if `--reload` imported a stale file (rare; do a clean
  stop/start if routes 404 unexpectedly).
