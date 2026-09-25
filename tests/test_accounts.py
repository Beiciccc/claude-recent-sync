from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from claude_recent_sync.accounts import AccountCatalog, account_context, project_key
from claude_recent_sync.cli import ClaudeLayout, SyncError
from claude_recent_sync.transfers import AccountTransfers


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if isinstance(value, (dict, list)) else value)


class Fixture:
    def __init__(self, home):
        self.home = home
        self.layout = ClaudeLayout(home / "Library/Application Support/Claude", home / ".claude/projects")
        self.layout.backup_root = home / ".claude/backups"
        self.project = home / "Desktop/Research"
        self.project.mkdir(parents=True)
        self.source = self.layout.profile_ref("account-a", "team-a")
        self.target = self.layout.profile_ref("account-b", "team-b")
        self.target.session_dir.mkdir(parents=True)
        write(self.layout.claude_dir / "cowork-enabled-cli-ops.json", {"ownerAccountId": "account-b"})
        self.index(self.source, "main", "source-session", "Research", 12)
        self.index(self.target, "other", "old-session", "Old work", 2)
        self.memory = self.layout.projects_dir / project_key(str(self.project)) / "memory"
        write(self.memory / "MEMORY.md", f"Project: {self.project}\nA useful remembered fact.\n")
        write(home / ".claude/CLAUDE.md", "Global preference: respond in Chinese.\n")

    def index(self, ref, name, sid, title, turns):
        write(ref.session_dir / ("local_" + name + ".json"),
              {"title": title, "sessionId": "local_" + name, "cliSessionId": sid,
               "cwd": str(self.project), "originCwd": str(self.project), "completedTurns": turns,
               "lastActivityAt": 1000 * turns, "bridgeSessionIds": ["old-cloud-bridge"]})
        transcript = self.layout.projects_dir / project_key(str(self.project)) / (sid + ".jsonl")
        write(transcript, json.dumps({"type": "user", "cwd": str(self.project),
                                     "message": {"content": f"Historical path {self.project}"}}) + "\n")
        return transcript


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = Fixture(Path(self.temp.name) / "mac")

    def tearDown(self):
        self.temp.cleanup()

    def test_modes_and_profiles_are_explicit(self):
        f = self.fixture
        alternate = ClaudeLayout(f.layout.claude_dir.with_name("Claude-3p"), f.layout.projects_dir)
        write(alternate.claude_dir / "cowork-enabled-cli-ops.json", {"ownerAccountId": "account-a"})
        f.index(alternate.profile_ref("account-a", "team-a"), "main", "third-party-session", "Third party", 20)
        catalog = AccountCatalog(f.layout)
        keys = {r["key"] for r in catalog.accounts()}
        self.assertIn("Claude/account-a", keys)
        self.assertIn("Claude-3p/account-a", keys)
        _, ref = catalog.resolve("Claude/account-a", "team-a")
        self.assertEqual(ref.session_dir, f.source.session_dir)
        with self.assertRaises(SyncError):
            catalog.resolve("Claude/account-a", "../../other")

    def test_local_memory_is_reported_as_shared(self):
        f = self.fixture
        context = account_context(f.layout, f.source)
        self.assertEqual(context["memoryFileCount"], 1)
        self.assertEqual(context["storage"], "shared-projects")
        service = AccountTransfers(f.layout, f.home / "state")
        preview = service.preview("Claude/account-a", "Claude/account-b")
        result = service.run(preview["planId"], settle_delay=0)
        self.assertEqual(result["context"]["memoryFileCount"], 1)
        self.assertEqual(set(p.name for p in f.target.session_dir.glob("local_*.json")), {"local_main.json"})

    def test_source_edit_invalidates_preview_before_write(self):
        f = self.fixture
        service = AccountTransfers(f.layout, f.home / "state")
        preview = service.preview("Claude/account-a", "Claude/account-b")
        write(f.memory / "MEMORY.md", "A newer memory")
        with self.assertRaisesRegex(SyncError, "已改变"):
            service.run(preview["planId"], settle_delay=0)
        self.assertTrue((f.target.session_dir / "local_other.json").exists())

    def test_login_change_does_not_redirect_explicit_target(self):
        f = self.fixture
        service = AccountTransfers(f.layout, f.home / "state")
        preview = service.preview("Claude/account-a", "Claude/account-b")
        write(f.layout.claude_dir / "cowork-enabled-cli-ops.json", {"ownerAccountId": "account-a"})
        service.run(preview["planId"], settle_delay=0)
        self.assertTrue((f.target.session_dir / "local_main.json").exists())
        self.assertEqual(json.loads((f.source.session_dir / "local_main.json").read_text())["cliSessionId"], "source-session")

    def test_empty_catalog_is_renderable(self):
        layout = ClaudeLayout(Path(self.temp.name) / "empty/Claude", Path(self.temp.name) / "empty/projects")
        service = AccountTransfers(layout, Path(self.temp.name) / "empty/state")
        result = service.preview()
        self.assertIsNone(result["source"])
        self.assertTrue(result["blockers"])

    def test_exact_account_and_profile_ids_win_over_prefixes(self):
        f = self.fixture
        extra = f.layout.profile_ref("account-a-extra", "team-a-extra")
        f.index(extra, "extra", "extra-session", "Extra", 1)
        f.layout.profile_ref("account-a", "team-a-extra").session_dir.mkdir()
        self.assertEqual(f.layout.resolve_account_alias("account-a"), "account-a")
        self.assertEqual(f.layout.resolve_profile("account-a", "team-a").profile_id, "team-a")
        self.assertIn("Claude/account-a", {r["key"] for r in AccountCatalog(f.layout).accounts()})
