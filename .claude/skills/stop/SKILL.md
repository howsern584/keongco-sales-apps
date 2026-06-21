---
name: stop
description: Stop the Keongco dev server and save work to GitHub. Use when the user says "stop the server" or types /stop. Stops the server, commits the changed app files, and pushes to the current branch so the other machine can pull it.
---

# Stop the server

When the user asks to stop the server, do these steps in order. Run git commands
from the project root.

1. **Stop the running server** (Claude Preview `preview_stop`, or stop the uvicorn
   process / free port 8000).

2. **Review what changed:** `git status --short` and skim the diff so the commit
   message is accurate.

3. **Stage the real source changes — NOT machine-specific files.** Stage source and
   template files. Do **not** commit `.claude/launch.json` or `.claude/settings.local.json`
   (they hold absolute Windows paths and local settings). The `.claude/skills/`
   folder IS portable and may be committed if it changed.

4. **Commit** with a clear message describing the work, ending with this trailer:
   ```
   Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
   ```

5. **Push to the CURRENT branch** (never directly to `master`):
   `git push origin <current-branch>`. If currently on `master`, branch first.

6. **Confirm:** report the commit hash and that it pushed, so the user knows the
   laptop can now `git pull` to get the same state.

Why: the desktop and laptop stay in sync through GitHub. Pushing here is what makes
the work available on the other machine — Dropbox is NOT the sync path for code
(syncing code + `keongco.db` through Dropbox causes "conflicted copy" corruption).
