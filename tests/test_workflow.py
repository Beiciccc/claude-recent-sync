from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from claude_recent_sync.cli import ClaudeLayout
from claude_recent_sync.workflow import (
    detailed_plan,
    restore_backup_workflow,
    run_sync_workflow,
)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def write_session(directory: Path, local_id: str, cli_id: str, title: str, turns: int) -> None:
    write_json(
        directory / f"local_{local_id}.json",
        {
            "sessionId": f"local_{local_id}",
            "cliSessionId": cli_id,
            "title": title,
            "completedTurns": turns,
            "lastActivityAt": turns * 1000,
            "cwd": f"/work/{title}",
            "originCwd": f"/work/{title}",
        },
    )


def write_transcript(projects_dir: Path, cli_id: str) -> None:
    path = projects_dir / "project" / f"{cli_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"type":"user","message":"test"}\n', encoding="utf-8")


class DetailedPlanTests(unittest.TestCase):
    def test_reports_branch_level_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            layout = ClaudeLayout(root / "Claude", root / "projects")
            source = layout.profile_ref("source", "profile")
            target = layout.profile_ref("target", "profile")
            write_session(source.session_dir, "keep", "new-branch", "PHD", 15)
            write_session(source.session_dir, "add", "new-session", "EPS", 3)
            write_session(target.session_dir, "keep", "old-branch", "PHD", 8)
            write_session(target.session_dir, "remove", "target-only", "Old", 2)

            plan = detailed_plan(source, target)

            self.assertEqual(plan["counts"]["added"], 1)
            self.assertEqual(plan["counts"]["updated"], 1)
            self.assertEqual(plan["counts"]["deleted"], 1)
            updated = next(item for item in plan["sessions"] if item["indexName"] == "local_keep.json")
            self.assertTrue(updated["branchChanged"])
            self.assertIn("cliSessionId", updated["fieldChanges"])
            self.assertEqual(updated["source"]["completedTurns"], 15)
            self.assertEqual(updated["target"]["completedTurns"], 8)


class WorkflowTests(unittest.TestCase):
    def test_sync_validates_and_creates_restorable_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_dir = root / "Claude"
            projects_dir = root / "projects"
            layout = ClaudeLayout(claude_dir, projects_dir)
            layout.backup_root = root / "backups"
            source = layout.profile_ref("source", "profile-source")
            target = layout.profile_ref("target", "profile-target")

            write_session(source.session_dir, "keep", "new-branch", "PHD", 15)
            write_session(source.session_dir, "add", "new-session", "EPS", 3)
            write_session(target.session_dir, "keep", "old-branch", "PHD", 8)
            write_session(target.session_dir, "remove", "target-only", "Old", 2)
            for cli_id in ("new-branch", "new-session", "old-branch", "target-only"):
                write_transcript(projects_dir, cli_id)

            progress: list[tuple[str, str]] = []
            result = run_sync_workflow(
                layout,
                source,
                target,
                settle_delay=0,
                progress=lambda step_id, _label, state, _detail: progress.append((step_id, state)),
            )

            self.assertEqual(result["status"], "success")
            self.assertTrue(result["exactSame"])
            self.assertEqual(result["targetCountAfter"], 2)
            self.assertEqual(result["targetHealth"]["missingTranscripts"], [])
            self.assertTrue(Path(result["backup"]).exists())
            self.assertIn(("validate", "completed"), progress)

            restore = restore_backup_workflow(layout, Path(result["backup"]).name)
            self.assertEqual(restore["status"], "success")
            self.assertEqual(restore["targetCountAfter"], 2)
            restored_keep = json.loads(
                (target.session_dir / "local_keep.json").read_text(encoding="utf-8")
            )
            self.assertEqual(restored_keep["cliSessionId"], "old-branch")
            self.assertTrue((target.session_dir / "local_remove.json").exists())
            self.assertFalse((target.session_dir / "local_add.json").exists())


if __name__ == "__main__":
    unittest.main()
