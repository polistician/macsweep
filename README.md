# MacSweep

A local-only Mac storage analyzer with a smart-deletion system. Indexes your home directory, classifies every file, and surfaces the things you'd actually want to clean — not just what's biggest.

## Run it

**From Finder:** double-click `MacSweep.app` (drag it into `/Applications` or your Dock to make it permanent).

**From terminal:**
```bash
./run.sh
```

Either way you get a native window. No Terminal opens, no browser tab. Closing the window exits cleanly. First launch installs deps in the background (~30s, with a system notification).

If you ever change the project's location on disk, rebuild the bundle:
```bash
./build_app.sh
```

## What it does

- **Indexes** every file under `~` into SQLite, classified into 12 categories (caches, dev artifacts, trash, logs, installers, downloads, iOS backups, media, documents, ML weights, apps, other).
- **Suggests** smart cleanups in two tiers:
  - *Tier 1 — safe / regenerable*: trash, application caches, old logs.
  - *Tier 2 — review*: stale build artifacts in dormant repos, old downloads, installers, forgotten 100 MB+ files untouched 1+ year, iOS backups.
- **Visualizes** disk distribution as a sunburst (DaisyDisk-style) with category coloring.
- **Detects projects** (anything with a `.git/HEAD`) and tallies *source vs. artifact* size, so you can wipe `node_modules`/`target`/`.venv` from repos you haven't touched in months.
- **Quarantines** instead of deleting. Files move to `~/.macsweep_quarantine/<timestamp>/<original_path>/` with full restore capability for 30 days. Never `rm`s anything directly.

## Smart-deletion design

Three principles, all visible in the UI:

1. **Regenerability tier**. Caches / build artifacts / trash are flagged green ("recoverable") because re-downloading or rerunning a build gets them back. Personal files are never auto-grouped.
2. **Atime, not mtime**. "Last opened" is the truth that matters — a file you wrote a year ago and re-opened yesterday is alive. The forgotten-files view filters on `atime`.
3. **Quarantine, not delete**. Every "delete" is a `shutil.move` into a dated quarantine folder, indexed in SQLite. Restore puts files back at their exact original path. Auto-purge after 30 days. The hard guard list (`/System`, `/Library`, `/usr`, etc.) refuses to quarantine system paths even if asked.

## Architecture

```
backend/
  db.py          SQLite schema + connection wrapper
  rules.py       Path → (category, subcategory, regenerable) classification
  scanner.py     Walks ~ in 1k-row batches; skips system & cloud paths; treats .app as opaque
  projects.py    Builds the projects table from indexed .git/HEAD entries
  suggestions.py Generates ranked Tier 1 / Tier 2 cleanups from the index
  quarantine.py  shutil.move into dated batches; restore / purge / auto-purge
  main.py        FastAPI app + static frontend mount
frontend/
  index.html     Single-page UI
  styles.css     Dark theme, designed against DaisyDisk / CleanMyMac / Linear
  app.js         D3 sunburst, category cards, suggestion panel, quarantine drawer
```

## API

| Method | Path                              | Description                          |
|--------|-----------------------------------|--------------------------------------|
| POST   | `/api/scan`                       | Start a full scan (background)       |
| GET    | `/api/scan/status`                | Live progress                        |
| GET    | `/api/overview`                   | Totals + per-category breakdown      |
| GET    | `/api/sunburst?depth=4`           | Hierarchical size tree for D3        |
| GET    | `/api/category/{name}`            | Files in a category, sorted by size  |
| GET    | `/api/action/{slug}`              | Files matching a suggestion's action |
| GET    | `/api/projects`                   | Detected git repos with sizes        |
| GET    | `/api/forgotten`                  | Big files not opened in N days       |
| GET    | `/api/suggestions`                | Ranked Quick Wins                    |
| POST   | `/api/quarantine`                 | Move paths to quarantine             |
| GET    | `/api/quarantine`                 | List items currently quarantined     |
| POST   | `/api/quarantine/{id}/restore`    | Restore an item                      |
| POST   | `/api/quarantine/{id}/purge`      | Permanently delete a quarantined item|

## Safety

- Never deletes — only moves to `~/.macsweep_quarantine/`.
- Hard-coded refusal list: `/System`, `/usr`, `/bin`, `/sbin`, `/private`, `/dev`, `/Volumes`, `/Library`, iCloud paths, the quarantine folder itself.
- No telemetry, no network. The server binds to `127.0.0.1` only.
