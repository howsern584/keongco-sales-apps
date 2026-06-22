# /stop — Push latest code to GitHub and stop the dev server

Follow these steps in order:

## Step 1 — Push latest code to GitHub
Run these from the repo root (`keongco-sales-apps/`):
```
git add .
git status
```
Show the user what files will be committed. Then commit and push:
```
git commit -m "auto-save: work in progress"
git push
```
Report the result (files pushed, or "nothing to commit").

## Step 2 — Stop the backend server
Stop the uvicorn process that is running in the background:
```
Stop-Process -Name "uvicorn" -ErrorAction SilentlyContinue
```
Or if running by PID, kill it. Confirm the process has stopped.

## Step 3 — Verify the server is stopped
Show these links and note that they should now be unreachable (connection refused):

- **App:** http://127.0.0.1:8000
- **API Docs:** http://127.0.0.1:8000/docs

Tell the user: "Server is stopped. The links above should no longer load in your browser."
