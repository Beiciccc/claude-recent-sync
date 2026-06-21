# claude-recent-sync

`claude-recent-sync` mirrors **Claude Desktop Code Recents** between multiple Claude accounts on the same Mac.

It solves the annoying case where Claude Code transcripts still exist under `~/.claude/projects`, but the Claude Desktop sidebar only shows recent Code sessions for the currently logged-in account.

## What It Syncs

Claude Desktop stores Code sidebar entries here:

```text
~/Library/Application Support/Claude/claude-code-sessions/<account-id>/<profile-id>/local_*.json
```

Those small `local_*.json` files are the Desktop "recent session" indexes. They point at real Claude Code transcripts through `cliSessionId`, usually under:

```text
~/.claude/projects/**/<cliSessionId>.jsonl
```

This tool mirrors the index files between local account/profile directories. It does **not** copy OAuth tokens, cookies, or transcript JSONL files.

## Install

Run directly without installing:

```bash
./bin/claude-recent-sync list
```

From the repo:

```bash
python3 -m pip install -e .
```

Or run without installing:

```bash
PYTHONPATH=src python3 -m claude_recent_sync list
```

## Quick Use

List detected accounts and profile directories:

```bash
claude-recent-sync list
```

Preview syncing the previous account into the currently logged-in account:

```bash
claude-recent-sync mirror --from previous --to current --dry-run
```

Apply it:

```bash
claude-recent-sync mirror --from previous --to current
```

Sync the currently logged-in account back to the previous account:

```bash
claude-recent-sync mirror --from current --to previous
```

Validate that the index entries point to transcript JSONL files:

```bash
claude-recent-sync doctor current
```

Poll and mirror while you work:

```bash
claude-recent-sync watch --from previous --to current --interval 10
```

## Safety Defaults

- Dry-run support for every mirror.
- Backs up target `local_*.json` files before writing.
- Refuses to mirror an empty source by default.
- Mirrors additions, updates, and deletions unless `--no-delete` is used.
- Uses runtime detection; no account IDs are hardcoded in the source.

Backups go to:

```text
~/.claude/backups/claude-recent-sync/
```

## How "current" and "previous" Work

`current` is read from:

```text
~/Library/Application Support/Claude/cowork-enabled-cli-ops.json
```

`previous` is the non-current account with the most recently modified `local_*.json` session index.

You can also pass account IDs or unique prefixes explicitly:

```bash
claude-recent-sync mirror --from acct-a-prefix --to acct-b-prefix --dry-run
```

If an account has multiple profile/org directories, the tool picks the profile with session files and recent activity. You can override that:

```bash
claude-recent-sync mirror --from current --source-profile profile-a-prefix --to previous --target-profile profile-b-prefix
```

## Commands

```text
claude-recent-sync list
claude-recent-sync diff --from previous --to current
claude-recent-sync mirror --from previous --to current [--dry-run]
claude-recent-sync doctor [current|previous|account-id]
claude-recent-sync watch --from previous --to current
```

## Notes

Quit Claude Desktop before a large mirror if you want a perfectly stable snapshot. The tool verifies the final target state, but Claude may rewrite Recents while the app is running.
