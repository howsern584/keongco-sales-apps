# /start — Pull latest code and start the dev server

Follow these steps in order:

## Step 1 — Pull latest code from GitHub
Run this from the repo root (`keongco-sales-apps/`):
```
git pull
```
Report what was pulled (files changed, or "Already up to date.").

## Step 2 — Start the backend server
Run this from the `backend/` folder with the virtual environment active:
```
cd backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload
```
Run the server in the background so the terminal stays responsive.

## Step 3 — Show the server links
Once the server is running, display these links clearly so the user can click them:

- **App:** http://127.0.0.1:8000
- **API Docs:** http://127.0.0.1:8000/docs
- **Health check:** http://127.0.0.1:8000/health
