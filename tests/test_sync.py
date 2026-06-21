from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from claude_recent_sync.cli import ClaudeLayout, SyncError, compute_plan, mirror_sessions


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


class MirrorTests(unittest.TestCase):
    def test_mirror_add_update_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_dir = root / "Claude"
            projects_dir = root / "projects"
            layout = ClaudeLayout(claude_dir, projects_dir)
            source = layout.profile_ref("acct-a", "profile-a")
            target = layout.profile_ref("acct-b", "profile-b")

            write_json(source.session_dir / "local_keep.json", {"sessionId": "local_keep", "cliSessionId": "new"})
            write_json(source.session_dir / "local_add.json", {"sessionId": "local_add", "cliSessionId": "add"})
            write_json(target.session_dir / "local_keep.json", {"sessionId": "local_keep", "cliSessionId": "old"})
            write_json(target.session_dir / "local_delete.json", {"sessionId": "local_delete", "cliSessionId": "delete"})

            plan = compute_plan(source, target)
            self.assertEqual([item.name for item in plan.added], ["local_add.json"])
            self.assertEqual([item.name for item in plan.updated], ["local_keep.json"])
            self.assertEqual([item.name for item in plan.deleted], ["local_delete.json"])

            result = mirror_sessions(
                layout,
                source,
                target,
                dry_run=False,
                delete=True,
                allow_empty_source=False,
                backup_root=root / "backups",
            )

            self.assertTrue(result["overallSame"])
            self.assertEqual(result["targetCountAfter"], 2)
            self.assertTrue((target.session_dir / "local_add.json").exists())
            self.assertFalse((target.session_dir / "local_delete.json").exists())
            self.assertEqual(
                json.loads((target.session_dir / "local_keep.json").read_text(encoding="utf-8"))["cliSessionId"],
                "new",
            )
            self.assertTrue(Path(result["backup"]).exists())

    def test_refuses_empty_source_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            layout = ClaudeLayout(root / "Claude", root / "projects")
            source = layout.profile_ref("acct-a", "profile-a")
            target = layout.profile_ref("acct-b", "profile-b")
            write_json(target.session_dir / "local_keep.json", {"sessionId": "local_keep", "cliSessionId": "old"})

            with self.assertRaises(SyncError):
                mirror_sessions(
                    layout,
                    source,
                    target,
                    dry_run=False,
                    delete=True,
                    allow_empty_source=False,
                    backup_root=root / "backups",
                )


class DiscoveryTests(unittest.TestCase):
    def test_current_and_previous_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_dir = root / "Claude"
            write_json(claude_dir / "cowork-enabled-cli-ops.json", {"ownerAccountId": "current-acct"})
            write_json(
                claude_dir / "claude-code-sessions" / "previous-acct" / "profile-p" / "local_one.json",
                {"sessionId": "local_one", "cliSessionId": "one"},
            )
            write_json(
                claude_dir / "claude-code-sessions" / "current-acct" / "profile-c" / "local_two.json",
                {"sessionId": "local_two", "cliSessionId": "two"},
            )

            layout = ClaudeLayout(claude_dir, root / "projects")
            self.assertEqual(layout.resolve_account_alias("current"), "current-acct")
            self.assertEqual(layout.resolve_account_alias("previous"), "previous-acct")
            self.assertEqual(layout.resolve_profile("current").profile_id, "profile-c")


if __name__ == "__main__":
    unittest.main()

