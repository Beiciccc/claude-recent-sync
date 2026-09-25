from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .cli import ClaudeLayout, ProfileRef, SyncError, local_json_files, read_session_index, safe_load_json
from .identities import AccountIdentities


def project_key(path: str) -> str:
    import re

    return re.sub(r"[^a-zA-Z0-9]", "-", path)


class AccountCatalog:
    """Explicit identities keep ordinary and third-party accounts separate."""

    def __init__(self, layout: ClaudeLayout, identity_path: Path | None = None) -> None:
        self.primary = layout
        self.identities = AccountIdentities(layout.projects_dir.parent.parent, identity_path)
        self.layouts = {layout.claude_dir.name: layout}
        self._discover_layouts()

    def _discover_layouts(self):
        layout = self.primary
        if layout.claude_dir.name in {"Claude", "Claude-3p"}:
            for name in ("Claude", "Claude-3p"):
                path = layout.claude_dir.parent / name
                if path.is_dir() and name not in self.layouts:
                    other = ClaudeLayout(path, layout.projects_dir)
                    other.backup_root = layout.backup_root
                    self.layouts[name] = other

    def accounts(self) -> list[dict[str, Any]]:
        self._discover_layouts()
        self.identities.discover()
        rows = []
        for deployment, layout in self.layouts.items():
            active = layout.active_account_id()
            for account in layout.account_ids():
                if account in {"", ".", ".."} or any(c in account for c in ("/", "\\", "\x00")):
                    continue
                profiles = []
                for profile in layout.profile_ids_for_account(account):
                    ref = layout.profile_ref(account, profile)
                    files = local_json_files(ref.session_dir)
                    profiles.append({"profileId": profile, "sessionCount": len(files),
                                     "newestSessionMtime": max((p.stat().st_mtime for p in files), default=0)})
                if not profiles:
                    continue
                default = layout.resolve_profile(account).profile_id
                key = deployment + "/" + account
                rows.append({"key": key, "accountId": account,
                             **self.identities.lookup(key),
                             "deployment": deployment,
                             "modeLabel": "第三方模式" if deployment == "Claude-3p" else "普通模式",
                             "isLoggedIn": account == active, "profiles": profiles,
                             "defaultProfileId": default,
                             "sessionCount": sum(p["sessionCount"] for p in profiles)})
        return sorted(rows, key=lambda row: (not row["isLoggedIn"], row["deployment"], row["accountId"]))

    def resolve(self, key: str, profile: str | None = None) -> tuple[ClaudeLayout, ProfileRef]:
        row = next((row for row in self.accounts() if row["key"] == key), None)
        if row is None:
            raise SyncError("选中的账号已不存在，请刷新账号列表。")
        selected = profile or row["defaultProfileId"]
        if selected not in {p["profileId"] for p in row["profiles"]}:
            raise SyncError("选中的组织目录已不存在，请重新选择。")
        layout = self.layouts[row["deployment"]]
        ref = layout.profile_ref(row["accountId"], selected)
        if (layout.code_sessions_dir.resolve() not in ref.session_dir.resolve().parents
                or ref.session_dir.resolve().parent != (layout.code_sessions_dir / ref.account_id).resolve()
                or any(p.is_symlink() for p in local_json_files(ref.session_dir))):
            raise SyncError("Invalid account directory.")
        return layout, ref


def account_context(layout: ClaudeLayout, ref: ProfileRef) -> dict[str, Any]:
    transcript_index = layout.build_transcript_index()
    roots: set[Path] = set()
    missing: list[str] = []
    session_ids: set[str] = set()
    for path in local_json_files(ref.session_dir):
        data = read_session_index(path)
        identifier = data.get("cliSessionId")
        if not isinstance(identifier, str) or identifier not in transcript_index:
            missing.append(path.name)
            continue
        session_ids.add(identifier)
        relative = transcript_index[identifier].relative_to(layout.projects_dir)
        roots.add(layout.projects_dir / relative.parts[0])
        for cwd in (data.get("cwd"), data.get("originCwd")):
            if isinstance(cwd, str):
                candidate = layout.projects_dir / project_key(cwd)
                if candidate.is_dir():
                    roots.add(candidate)
    memories = [p for root in roots for p in (root / "memory").rglob("*")
                if p.is_file() and not p.is_symlink() and p.name != ".DS_Store"]
    global_file = layout.projects_dir.parent / "CLAUDE.md"
    return {"projectCount": len(roots), "transcriptCount": len(session_ids),
            "memoryFileCount": len(memories), "memoryBytes": sum(p.stat().st_size for p in memories),
            "globalMemoryPresent": global_file.is_file(), "missingTranscripts": missing,
            "storage": "shared-projects", "projectRoots": sorted(str(p) for p in roots)}


def context_signature(layout: ClaudeLayout, ref: ProfileRef) -> str:
    digest = hashlib.sha256()
    for path in local_json_files(ref.session_dir):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    for root in account_context(layout, ref)["projectRoots"]:
        for path in sorted((Path(root) / "memory").rglob("*")):
            if path.is_file() and not path.is_symlink():
                digest.update(str(path).encode())
                digest.update(path.read_bytes())
    return digest.hexdigest()


def default_selections(catalog: AccountCatalog) -> tuple[str | None, str | None]:
    rows = catalog.accounts()
    config = safe_load_json(catalog.primary.claude_dir.parent / "Claude" / "claude_desktop_config.json") or {}
    mode = "Claude-3p" if config.get("deploymentMode") == "3p" else "Claude"
    target = next((r for r in rows if r["isLoggedIn"] and r["deployment"] == mode), None)
    target = target or next((r for r in rows if r["isLoggedIn"]), None)
    # The source is always chosen by the user, never inferred from another account's mtime.
    return None, target["key"] if target else None
