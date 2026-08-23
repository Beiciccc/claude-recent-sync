# Claude Recent Sync

[![macOS 12+](https://img.shields.io/badge/macOS-12%2B-111111?logo=apple)](https://www.apple.com/macos/)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-16735e.svg)](LICENSE)

[简体中文](docs/README.zh-CN.md)

A local, backup-first macOS app for comparing and mirroring Claude Desktop Code Recents between accounts on the same Mac.

![Claude Recent Sync dashboard](docs/dashboard.png)

> [!IMPORTANT]
> Claude Recent Sync is an unofficial local utility and is not affiliated with Anthropic. It mirrors the local Claude Desktop Code Recents index only. It cannot update Claude.ai web or mobile conversation history, cloud memory, or any server-side account data.

## Why It Exists

Claude Desktop keeps Code sidebar entries in account-specific local directories. After switching accounts, existing Claude Code transcripts can remain on disk while their Recents entries are missing or stale for the newly signed-in account.

Claude Recent Sync treats one local account as the authoritative **source**, previews the difference, backs up the **target**, and makes the target index match the source. The mirror includes additions, updates, branch changes, and target-only deletions; it is not limited to a fixed number of sessions.

## Highlights

- Visual discovery of local accounts and profile/organization directories.
- Session-by-session comparison of titles, branch IDs, turn counts, activity times, and working directories.
- One action performs source validation, conflict handling, backup, mirroring, transcript checks, and a delayed stability check.
- Stale target-account Claude backends are stopped only when they could rewrite an older session branch.
- Timestamped target backups with restore support from the History tab.
- Optional synchronization after an account switch and optional launch at login.
- Local service bound to `127.0.0.1`; no credentials, cookies, or transcript bodies are copied.

## Requirements

- macOS 12 or newer
- Python 3.10 or newer (`python3 --version`)
- Claude Desktop and local Claude Code session data on the same Mac

Node.js is required only for frontend development, not for the installed app.

## Quick Start

```bash
git clone https://github.com/Beiciccc/claude-recent-sync.git
cd claude-recent-sync
./scripts/install-macos-app.sh
open "$HOME/Applications/Claude Recent Sync.app"
```

The installer creates `~/Applications/Claude Recent Sync.app`, bundles the Python modules and compiled interface, and applies a local ad-hoc signature. It does not require administrator access or Node.js.

In the app:

1. Sign in to the account that should receive the local Recents entries.
2. Confirm that **上个账号 · 来源 (Previous / Source)** contains the authoritative sessions and **当前账号 · 目标 (Current / Target)** is the newly signed-in account.
3. Review the added, updated, deleted, and unchanged rows.
4. Select **开始安全同步 (Start safe sync)**.
5. Keep the app open until the delayed verification finishes.

> [!CAUTION]
> Mirroring is directional. Target-only `local_*.json` entries are deleted so the target matches the source. A complete target-index backup is created before every write, but you should still verify the selected direction before starting.

To upgrade, pull the repository and run the installer again:

```bash
git pull --ff-only
./scripts/install-macos-app.sh
```

## What Is Synced

Claude Desktop stores Code Recents indexes under:

```text
~/Library/Application Support/Claude/claude-code-sessions/<account-id>/<profile-id>/local_*.json
```

Each small index references a durable Claude Code transcript through `cliSessionId`, usually located under:

```text
~/.claude/projects/.../<cliSessionId>.jsonl
```

Claude Recent Sync mirrors the index files and verifies that their referenced transcripts exist. It does **not** copy transcript JSONL files, OAuth tokens, cookies, credentials, Claude.ai conversations, or cloud memory.

## Safe Sync Workflow

Each visual sync performs the following steps:

1. Rediscover the current account and the selected source account.
2. Resolve the active profile/organization directory for each account.
3. Validate every source index and its transcript reference.
4. Compare each source and target session.
5. Stop only stale target backends that reference a branch being replaced or removed.
6. Back up the complete target index directory.
7. Mirror additions, updates, and deletions.
8. Validate the resulting target and compare it with the source.
9. Wait for Claude Desktop to settle, verify again, and reconcile once if needed.

An empty source is rejected by default to prevent accidental target clearing.

## Automatic Account Switches

The Settings tab provides two opt-in controls:

- **账号切换同步 (Account-switch sync)** tracks the active account and uses the account active immediately before a switch as the next source.
- **登录时运行 (Run at login)** installs a user LaunchAgent that starts the local service after macOS login.

Both options are disabled by default. Review the first few account switches manually before enabling unattended synchronization.

## Backups and Restore

Backups and app state are stored locally:

```text
~/.claude/backups/claude-recent-sync/
~/.claude/claude-recent-sync/
```

Open the **运行记录 (History)** tab to inspect prior runs, reveal a backup in Finder, or restore it. Restoring also backs up the target's current state before replacing it.

## Command Line

Run the CLI directly from the repository:

```bash
./bin/claude-recent-sync list
./bin/claude-recent-sync diff --from previous --to current
./bin/claude-recent-sync mirror --from previous --to current --dry-run
./bin/claude-recent-sync mirror --from previous --to current
./bin/claude-recent-sync doctor current
./bin/claude-recent-sync ui
```

Install Python entry points for shell-wide use:

```bash
python3 -m pip install -e .
claude-recent-sync ui
```

The polling CLI is also available:

```bash
claude-recent-sync watch --from previous --to current --interval 10
```

Use `--no-delete` for a union-style copy that preserves target-only indexes. Use explicit account IDs or unique prefixes when aliases are ambiguous:

```bash
claude-recent-sync mirror \
  --from <source-account-prefix> \
  --to <target-account-prefix> \
  --dry-run
```

## Account Resolution

`current` is read from:

```text
~/Library/Application Support/Claude/cowork-enabled-cli-ops.json
```

For manual commands, `previous` resolves to the non-current account with the newest Code Recents index. The automatic account-switch watcher separately records the account that was active immediately before the switch.

If an account contains multiple profile or organization directories, the app selects the profile with session files and the newest local activity. CLI users can override this with `--source-profile` and `--target-profile`.

## Troubleshooting

- **Current account is not detected:** open Claude Desktop, complete sign-in, and refresh the dashboard.
- **Source validation fails:** confirm that the referenced transcript still exists under `~/.claude/projects`; invalid or missing source references block writes.
- **The app does not open a browser:** run `./bin/claude-recent-sync-ui` and open `http://127.0.0.1:47631`.
- **Port `47631` is occupied:** run `./bin/claude-recent-sync-ui --port 47632`.
- **Installed-app startup fails:** inspect `~/.claude/claude-recent-sync/launcher.log`.

## Privacy and Limitations

- All synchronization is local to one Mac. The app does not upload session data to GitHub, Anthropic, or another service.
- Indexes and backups contain local metadata such as titles, paths, account/profile IDs, and timestamps. Backups remain on disk and should be protected like other local Claude data.
- The tool depends on Claude Desktop's current, undocumented local storage layout. A future Claude Desktop update may require a compatibility update.
- Only macOS is supported by the app installer and process-management workflow.
- The current visual interface is in Simplified Chinese; the CLI remains usable from an English shell.
- This tool cannot make local Recents visible on claude.ai or a phone; those surfaces use server-side data controlled by Anthropic.

## Development

Run backend tests:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Rebuild the React interface:

```bash
cd frontend
npm ci
npm run build
```

The production bundle is written to `src/claude_recent_sync/web_dist` and served by the Python standard-library HTTP server.

## License

[MIT](LICENSE)
