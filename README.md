# Claude Recent Sync

[![macOS 12+](https://img.shields.io/badge/macOS-12%2B-111111?logo=apple)](https://www.apple.com/macos/)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-16735e.svg)](LICENSE)

[简体中文](docs/README.zh-CN.md)

Choose a **source account** and a **target account** to transfer Claude Desktop Code session indexes, conversation context, and project memory. Use the same interface on one Mac or move an encrypted account package to another Mac.

![Account-based context transfer](docs/dashboard.png)

## Version 0.3

- Accounts are shown by email, separately from their organization/workspace profiles.
- Ordinary `Claude` and third-party `Claude-3p` deployments have distinct identities.
- The source must be selected explicitly; modification time never silently chooses a different source.
- Session comparison and memory comparison show what will change.
- A preview is bound to exact account/profile identities. Changed data requires a refreshed preview.
- Encrypted `.crsync` packages carry the selected account's indexed conversations, linked prior branches, subagents, associated artifacts, file history, and project memory.
- Cross-Mac import maps home directories automatically and supports explicit project path overrides.
- Target backups, write verification, delayed checks, and failed-import rollback are included.

## Install

Requirements: macOS 12+, Python 3.9+, and an `openssl enc` implementation supporting PBKDF2. The current interface is in Simplified Chinese. Node.js is needed only to rebuild the frontend.

```bash
git clone https://github.com/Beiciccc/claude-recent-sync.git
cd claude-recent-sync
./scripts/install-macos-app.sh
open "$HOME/Applications/Claude Recent Sync.app"
```

The app opens its local interface at `http://127.0.0.1:47631`. The installer bundles the modules and built interface, uses the selected local Python interpreter, and applies an ad-hoc signature.

## Transfer Between Local Accounts

1. Select the email under **来源账号 (Source account)** and **目标账号 (Target account)**.
2. Select the organization/workspace under each account. Check the ordinary/third-party mode label.
3. Review **会话比较 (Sessions)** and **记忆比较 (Memory)**.
4. Click **同步到目标账号 (Sync to target account)**.
5. Wait for validation and the delayed check to finish.

The target session list becomes a mirror of the selected source list, including target-only deletions. The source remains unchanged.

Claude's transcripts and project memory are shared by project on the same Mac, not isolated per account. Local transfer verifies and reconnects those existing records; it does not invent separate historical memory versions for each account.

### Account Emails

The app reads exact account UUID/email pairs from the local CLI login metadata and its known configuration backups. Discovered addresses are remembered locally across account switches. It does not guess identity from conversation text or decrypt credential caches.

Accounts without reliable email metadata are marked **待绑定账号 (Email not linked)**. Select one and use the pencil beside its source/target heading to enter its email. Manual labels can be cleared in the same dialog. They only change the display label, not Claude login state or the underlying sync destination. The same email in ordinary and third-party modes remains two distinct destinations.

Mappings are stored privately in `~/.claude/claude-recent-sync/account-emails.json`. The selected source email travels inside the encrypted migration package; packages from earlier versions without an email can be labeled after import.

## Move to Another Mac

On the source Mac:

1. Select the source account and its workspace.
2. Choose whether to include global memory.
3. Click **导出来源账号 (Export source account)**.
4. Record the migration passphrase, then download the encrypted `.crsync` file.

Move that file through a channel of your choice, such as a removable drive, AirDrop, or iCloud Drive. The application does not automatically upload it anywhere.

On the destination Mac:

1. Install this app and sign in to the intended Claude account. Open Code once so the account/workspace can be discovered.
2. Click **导入迁移包 (Import package)**, select the file, and enter its passphrase.
3. The imported account snapshot becomes the source. Select the destination account and workspace.
4. Review the session and memory differences. Correct any missing project paths in **项目路径映射 (Project path mapping)**.
5. Click **同步到目标账号**.

Opening a package only decrypts and previews it. Target records are changed only by the sync action.

### Migration Scope

| Data | Behavior |
| --- | --- |
| Desktop Code indexes | Mirror into the explicitly selected target account/profile |
| Main and linked prior transcripts | Copy with runtime path mapping; recorded message bodies remain intact |
| Subagent transcripts and related artifacts | Include those linked to selected sessions |
| Unrelated sessions in the same project | Excluded from export unless referenced by the selected sessions |
| Project memory | Include the relevant shared project memory; imported memory directories mirror the source |
| Global memory | Optional; included global files overwrite matching target files |
| Existing unrelated target transcripts | Retained |
| Project code, documents, project-level `CLAUDE.md` | Continue using your existing project transfer, Git, or iCloud setup |
| Login credentials, cookies, Keychain, provider settings | Not transferred |
| Claude.ai web/mobile conversations or cloud memory | Not transferred |

Global memory can include `~/.claude/CLAUDE.md`, rules, commands, agents, skills, and an existing local context bridge. Project and global memory can affect other accounts using the same Mac. The interface shows this shared scope.

Packages preserve local data, not running processes, unsaved messages, or remote connector authorization. Custom tools and dependencies may need to be configured on the destination.

## Encryption and Integrity

Packages use OpenSSL/LibreSSL AES-256-CBC with PBKDF2 and an HMAC-SHA256 envelope. The passphrase is supplied to the crypto process through standard input and is not written to settings or task history.

Import checks the authentication tag before decryption, rejects unsafe archive paths and links, verifies the file manifest, and limits unpacked payloads to 32 GiB. Ordinary and third-party authentication state is kept on the destination machine.

After opening a package, its decrypted working copy is kept in the app's private local state directory. Protect that directory and the exported packages as you would other Claude data.

## Backups and Restore

The **运行记录 (History)** tab lists operations and backups. Cross-Mac import backs up both the target indexes and every context or memory file it changes. A failed write or validation rolls those changes back. Restoring a backup creates another backup of the current state first.

Default locations:

```text
~/.claude/backups/claude-recent-sync/
~/.claude/claude-recent-sync/exports/
~/.claude/claude-recent-sync/imports/
```

The ordinary-mode account-switch watcher and launch-at-login controls remain optional and off by default. Manual transfers always use the accounts explicitly selected in the interface.

## CLI Compatibility

The existing index-only commands remain available:

```bash
./bin/claude-recent-sync list
./bin/claude-recent-sync diff --from previous --to current
./bin/claude-recent-sync mirror --from previous --to current --dry-run
./bin/claude-recent-sync doctor current
./bin/claude-recent-sync ui
```

CLI aliases retain their original meaning. The visual account selector and encrypted migration packages use explicit account/profile identities instead.

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
cd frontend
npm ci
npm run build
```

The production bundle is stored in `src/claude_recent_sync/web_dist`. Development servers can use `--claude-dir`, `--projects-dir`, and `--state-dir` to isolate all test data and backups.

Tests cover email detection and persistence, account/profile selection, deployment separation, stale previews, transcript and memory integrity, package authentication, archive path rejection, path mapping, rollback, restore, and the HTTP export/import flow.

## Limits

This is an unofficial utility, not affiliated with Anthropic. It relies on Claude Desktop's local storage layout, which may change with future releases. Back up important records, verify the selected direction, and finish active Claude work before replacing its local records.

[MIT License](LICENSE)
