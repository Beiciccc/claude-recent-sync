# Claude Recent Sync

A local macOS app for comparing, backing up, and mirroring Claude Desktop Code Recents across accounts on the same Mac.

![Claude Recent Sync dashboard](docs/dashboard.png)

## Highlights

- Visual account and profile discovery with a session-by-session comparison.
- One action runs preflight validation, backup, mirror, transcript checks, and a delayed stability check.
- Additions, updates, branch changes, and target-only deletions are shown before writing.
- Stale target-account Claude backends are stopped gracefully when they would rewrite an old branch.
- Every write has a timestamped backup with Finder access and restore support.
- Optional account-switch automation and macOS login startup.
- Runs on `127.0.0.1`; no credentials, cookies, or transcript bodies are copied.

## Install the macOS App

Requirements:

- macOS 12 or newer
- Python 3.10 or newer

From the repository:

```bash
./scripts/install-macos-app.sh
```

The app is installed at:

```text
~/Applications/Claude Recent Sync.app
```

Open it from Finder or run:

```bash
open "$HOME/Applications/Claude Recent Sync.app"
```

The installer bundles the Python modules and built interface into the app, adds an ad-hoc local signature, and does not require Node.js at runtime.

## What It Syncs

Claude Desktop stores Code sidebar entries under:

```text
~/Library/Application Support/Claude/claude-code-sessions/<account-id>/<profile-id>/local_*.json
```

Each index points to a durable Claude Code transcript through `cliSessionId`, usually under:

```text
~/.claude/projects/.../<cliSessionId>.jsonl
```

Claude Recent Sync mirrors the small index files. It does not copy OAuth tokens, cookies, account credentials, or transcript JSONL files.

## Safe Workflow

Each visual sync performs:

1. Rediscover the currently logged-in account and the most recent non-current account.
2. Resolve the active profile for both accounts.
3. Validate every source index and transcript reference.
4. Compare each session, including title, branch ID, turn count, activity time, and working directory.
5. Stop only stale target-account backends that would rewrite an old branch.
6. Back up the complete target index directory.
7. Mirror additions, updates, and deletions.
8. Validate the target and compare it back to the source.
9. Wait for Claude Desktop to settle, then verify again and reconcile once if required.

Backups and local app state are stored under:

```text
~/.claude/backups/claude-recent-sync/
~/.claude/claude-recent-sync/
```

## Automatic Account Switches

The Settings tab includes:

- **Account-switch sync:** tracks the active account and uses the account that was active immediately before the switch as the source.
- **Run at login:** installs a user LaunchAgent that starts the bundled local service after macOS login.

Both settings are off by default.

## Command Line

Run directly from the repository:

```bash
./bin/claude-recent-sync list
./bin/claude-recent-sync diff --from previous --to current
./bin/claude-recent-sync mirror --from previous --to current --dry-run
./bin/claude-recent-sync mirror --from previous --to current
./bin/claude-recent-sync doctor current
./bin/claude-recent-sync ui
```

Install the Python entry points:

```bash
python3 -m pip install -e .
claude-recent-sync ui
```

The polling CLI remains available:

```bash
claude-recent-sync watch --from previous --to current --interval 10
```

## Account Resolution

`current` is read from:

```text
~/Library/Application Support/Claude/cowork-enabled-cli-ops.json
```

`previous` is the non-current account with the newest Code Recents index. The visual app rediscovers both sides on every refresh and at the start of every sync.

Account IDs or unique prefixes can also be selected explicitly:

```bash
claude-recent-sync mirror --from <source-prefix> --to <target-prefix> --dry-run
```

If an account has multiple profile or organization directories, the profile with session files and the newest local activity is selected. CLI users can override it with `--source-profile` and `--target-profile`.

## Frontend Development

Node.js is only needed to rebuild the React interface:

```bash
cd frontend
npm install
npm run build
```

The production bundle is written to `src/claude_recent_sync/web_dist` and served by the Python standard-library HTTP server.

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The tests use isolated temporary Claude layouts and cover add/update/delete mirroring, branch-aware comparisons, source validation, backups, and restore.
