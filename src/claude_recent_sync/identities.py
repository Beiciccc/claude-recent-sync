from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path

from .cli import SyncError, safe_load_json


def valid_email(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if len(value) > 254 or not re.fullmatch(r"[^\s@<>\x00-\x1f,;]+@[^\s@<>\x00-\x1f,;]+\.[^\s@<>\x00-\x1f,;.]+", value):
        return None
    return value


class AccountIdentities:
    """Display labels only; immutable account keys still select sync destinations."""

    def __init__(self, home: Path, path: Path | None = None):
        self.home = home
        self.path = path
        self.lock = threading.RLock()
        self.detected = {}
        self.cache = {}

    def discover(self):
        backups = self.home / ".claude/backups"
        candidates = [self.home / ".claude.json.backup"]
        for pattern in (".claude.json.backup.*", "*/.claude.json", "*/claude.json"):
            candidates.extend(backups.glob(pattern))
        def signature(path):
            try:
                stat = path.stat()
                return (stat.st_mtime_ns, stat.st_size) if path.is_file() and not path.is_symlink() else None
            except OSError:
                return None

        candidates = sorted((p for p in candidates if signature(p)), key=lambda p: signature(p) or (0, 0))[-512:]
        # Live identity wins over backups, but only for its exact account UUID.
        candidates.append(self.home / ".claude.json")
        found = {}
        for path in candidates:
            stamp = signature(path)
            if not stamp or stamp[1] > 8 * 1024 ** 2:
                continue
            cached = self.cache.get(path)
            if cached and cached[0] == stamp:
                identity = cached[1]
            else:
                try:
                    data = safe_load_json(path)
                except (OSError, UnicodeError):
                    continue
                account = data.get("oauthAccount") if isinstance(data, dict) else None
                # Cache only the display identity, never tokens or the complete config.
                identity = {k: account.get(k) for k in ("accountUuid", "emailAddress")} if isinstance(account, dict) else None
                self.cache[path] = (stamp, identity)
            if not isinstance(identity, dict):
                continue
            account = identity.get("accountUuid")
            email = valid_email(identity.get("emailAddress"))
            if email and isinstance(account, str) and account and not any(c in account for c in ("/", "\\", "\x00")):
                found["Claude/" + account] = {"email": email, "emailSource": "detected"}
        with self.lock:
            self.detected = found
            saved = self._read()
            updated = dict(saved)
            for key, identity in found.items():
                if saved.get(key, {}).get("emailSource") != "manual":
                    updated[key] = identity
            if self.path and updated != saved:
                self._write(updated)

    def _read(self):
        data = safe_load_json(self.path) if self.path else None
        rows = data.get("accounts", {}) if isinstance(data, dict) else {}
        return {key: row for key, row in rows.items()
                if isinstance(row, dict) and valid_email(row.get("email"))} if isinstance(rows, dict) else {}

    def _write(self, rows):
        if self.path is None:
            raise SyncError("邮箱绑定存储不可用。")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        temporary = self.path.with_name("." + self.path.name + "." + uuid.uuid4().hex)
        try:
            with temporary.open("x", encoding="utf-8") as output:
                temporary.chmod(0o600)
                json.dump({"version": 1, "accounts": rows}, output, ensure_ascii=True)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def lookup(self, key, package_email=None):
        with self.lock:
            row = self._read().get(key) or self.detected.get(key)
        if row:
            return {"email": row["email"], "emailSource": row.get("emailSource", "detected")}
        email = valid_email(package_email)
        return {"email": email, "emailSource": "package" if email else None}

    def bind(self, key, email):
        if email != "" and not valid_email(email):
            raise SyncError("请输入有效的邮箱地址。")
        with self.lock:
            rows = self._read()
            if email:
                rows[key] = {"email": valid_email(email), "emailSource": "manual"}
            else:
                rows.pop(key, None)
            self._write(rows)
        self.discover()
