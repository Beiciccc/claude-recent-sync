from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from test_accounts import Fixture, write
from claude_recent_sync.cli import ClaudeLayout, SyncError
from claude_recent_sync.identities import AccountIdentities, valid_email
from claude_recent_sync.transfers import AccountTransfers


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = Fixture(Path(self.temp.name) / "mac")
        self.state = self.fixture.home / "state"

    def tearDown(self):
        self.temp.cleanup()

    def login(self, path, account, email):
        write(path, {"oauthAccount": {"accountUuid": account, "emailAddress": email},
                     "unrelatedSecret": "must-not-be-cached"})

    def test_exact_login_mapping_preserved_across_switches(self):
        f = self.fixture
        self.login(f.home / ".claude.json", "account-a", "source@example.com")
        service = AccountTransfers(f.layout, self.state)
        rows = {r["key"]: r for r in service.accounts()}
        self.assertEqual(rows["Claude/account-a"]["displayName"], "source@example.com")
        self.assertIsNone(rows["Claude/account-b"]["email"])
        self.login(f.home / ".claude.json", "account-b", "target@example.com")
        rows = {r["key"]: r for r in AccountTransfers(f.layout, self.state).accounts()}
        self.assertEqual(rows["Claude/account-a"]["email"], "source@example.com")
        self.assertEqual(rows["Claude/account-b"]["email"], "target@example.com")
        registry = self.state / "account-emails.json"
        self.assertNotIn("must-not-be-cached", registry.read_text())
        self.assertEqual(registry.stat().st_mode & 0o777, 0o600)
        preview = service.preview("Claude/account-a", "Claude/account-b")
        self.assertEqual(preview["source"]["email"], "source@example.com")
        self.assertEqual(preview["target"]["email"], "target@example.com")

    def test_live_identity_beats_backup_without_prefix_or_mode_matching(self):
        f = self.fixture
        self.login(f.home / ".claude/backups/earlier/.claude.json", "account-a", "old@example.com")
        self.login(f.home / ".claude.json", "account-a", "new@example.com")
        f.index(f.layout.profile_ref("account-a-longer", "team"), "extra", "extra", "Extra", 1)
        third = ClaudeLayout(f.layout.claude_dir.with_name("Claude-3p"), f.layout.projects_dir)
        f.index(third.profile_ref("account-a", "team"), "third", "third", "Third", 1)
        rows = {r["key"]: r for r in AccountTransfers(f.layout, self.state).accounts()}
        self.assertEqual(rows["Claude/account-a"]["email"], "new@example.com")
        self.assertIsNone(rows["Claude/account-a-longer"]["email"])
        self.assertIsNone(rows["Claude-3p/account-a"]["email"])

    def test_manual_binding_persists_and_can_reset(self):
        f = self.fixture
        self.login(f.home / ".claude.json", "account-a", "detected@example.com")
        service = AccountTransfers(f.layout, self.state)
        service.bind_email("Claude/account-a", "manual@example.com")
        restarted = AccountTransfers(f.layout, self.state)
        self.assertEqual(restarted.identity("Claude/account-a")["email"], "manual@example.com")
        restarted.bind_email("Claude/account-a", "")
        self.assertEqual(restarted.identity("Claude/account-a")["email"], "detected@example.com")
        with self.assertRaises(SyncError):
            service.bind_email("Claude/not-present", "person@example.com")
        with self.assertRaises(SyncError):
            service.bind_email("Claude/account-b", "not-an-email")

    def test_package_email_and_manual_override(self):
        f = self.fixture
        write(self.state / "imports/test/manifest.json", {
            "format": 1, "accountId": "account-a", "profileId": "team-a", "deployment": "Claude",
            "accountEmail": "package@example.com", "createdAt": "2026-01-01", "indexCount": 1})
        service = AccountTransfers(f.layout, self.state)
        self.assertEqual(service.identity("package:test")["email"], "package@example.com")
        service.bind_email("package:test", "manual@example.com")
        self.assertEqual(service.identity("package:test")["email"], "manual@example.com")
        self.assertIsNone(service.identity("Claude/account-a")["email"])
        service.bind_email("package:test", "")
        self.assertEqual(service.identity("package:test")["email"], "package@example.com")

    def test_unknown_identity_and_invalid_metadata_remain_unbound(self):
        f = self.fixture
        self.login(f.home / ".claude.json", "account-a", "not-a-real-email")
        # Email text elsewhere in a config must not be treated as an identity pair.
        write(f.home / ".claude.json.backup", {"emailAddress": "stranger@example.com", "accountUuid": "account-b"})
        service = AccountTransfers(f.layout, self.state)
        self.assertTrue(all(r["email"] is None for r in service.accounts()))
        self.assertEqual(len({r["displayName"] for r in service.accounts()}), 2)
        self.assertIsNone(valid_email("user@example.com\r\nInjected: value"))
        self.assertEqual(valid_email("  name+tag@example.com  "), "name+tag@example.com")
        registry = self.state / "account-emails.json"
        registry.write_text(json.dumps({"accounts": ["bad"]}))
        self.assertIsNone(AccountIdentities(f.home, registry).lookup("Claude/account-a")["email"])
