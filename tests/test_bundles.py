from __future__ import annotations

import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_accounts import Fixture, write
from claude_recent_sync.bundles import PathMapping, decrypt, encrypt, export_bundle, open_bundle
from claude_recent_sync.cli import SyncError, file_sha256
from claude_recent_sync.transfers import AccountTransfers, atomic_copy, restore_import
from claude_recent_sync.accounts import project_key

PASSWORD = "synthetic-test-passphrase"


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = Fixture(self.root / "source")
        self.target = Fixture(self.root / "target")
        self.state = self.target.home / "state"
        self.bundle = self.root / "account.crsync"
        export_bundle(self.source.layout, self.source.source, self.bundle, PASSWORD)

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self):
        dest = self.state / "imports/snapshot"
        dest.parent.mkdir(parents=True, exist_ok=True)
        open_bundle(self.bundle, dest, PASSWORD)
        service = AccountTransfers(self.target.layout, self.state)
        return service, service.preview("package:snapshot", "Claude/account-b")

    def test_cross_mac_context_paths_dialogue_memory_and_restore(self):
        write(self.target.memory / "obsolete.md", "obsolete target memory")
        original_memory = (self.target.memory / "MEMORY.md").read_text()
        service, preview = self.prepare()
        self.assertEqual(preview["blockers"], [])
        result = service.run(preview["planId"], settle_delay=0)
        self.assertEqual(result["status"], "success")
        self.assertFalse((self.target.memory / "obsolete.md").exists())
        self.assertIn(str(self.target.project), (self.target.memory / "MEMORY.md").read_text())
        index = json.loads((self.target.target.session_dir / "local_main.json").read_text())
        self.assertEqual(index["cwd"], str(self.target.project))
        self.assertEqual(index["bridgeSessionIds"], [])
        transcript = self.target.layout.projects_dir / project_key(str(self.target.project)) / "source-session.jsonl"
        row = json.loads(transcript.read_text())
        self.assertEqual(row["cwd"], str(self.target.project))
        self.assertIn(str(self.source.project), row["message"]["content"])
        self.assertTrue((transcript.parent / "old-session.jsonl").exists())
        restore = restore_import(Path(result["backup"]), self.target.layout)
        self.assertTrue(Path(restore["backup"]).exists())
        self.assertEqual((self.target.memory / "MEMORY.md").read_text(), original_memory)
        self.assertTrue((self.target.memory / "obsolete.md").exists())
        self.assertTrue((self.target.target.session_dir / "local_other.json").exists())

    def test_wrong_password_and_modified_ciphertext(self):
        with self.assertRaises(SyncError):
            decrypt(self.bundle, self.root / "no.tar", "wrong-password-value")
        changed = bytearray(self.bundle.read_bytes())
        changed[-40] ^= 1
        self.bundle.write_bytes(changed)
        with self.assertRaises(SyncError):
            decrypt(self.bundle, self.root / "no.tar", PASSWORD)
        self.assertFalse((self.root / "no.tar").exists())

    def test_archive_traversal_and_symlinks_rejected(self):
        for malicious_name, symlink in [("../outside", False), ("global/CLAUDE.md", True),
                                        ("global/.credentials.json", False)]:
            archive = self.root / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                info = tarfile.TarInfo(malicious_name)
                if symlink:
                    info.type = tarfile.SYMTYPE
                    info.linkname = "/etc/passwd"
                    tar.addfile(info)
                else:
                    info.size = 4
                    tar.addfile(info, io.BytesIO(b"test"))
            encrypted = self.root / "bad.crsync"
            encrypted.unlink(missing_ok=True)
            encrypt(archive, encrypted, PASSWORD)
            with self.assertRaises(SyncError):
                open_bundle(encrypted, self.root / "unpacked", PASSWORD)
            self.assertFalse((self.root / "unpacked").exists())
        self.assertFalse((self.root / "outside").exists())

    def test_missing_target_project_blocks_writes(self):
        service, _ = self.prepare()
        preview = service.preview("package:snapshot", "Claude/account-b",
                                  mappings={str(self.source.project): str(self.root / "missing-project")})
        self.assertTrue(preview["missingPaths"])
        with self.assertRaises(SyncError):
            service.run(preview["planId"], settle_delay=0)
        self.assertTrue((self.target.target.session_dir / "local_other.json").exists())

    def test_failed_import_rolls_back_memory_and_indexes(self):
        service, preview = self.prepare()
        before = file_sha256(self.target.memory / "MEMORY.md")
        failed = False

        def fail_once(source, target):
            nonlocal failed
            if target == self.target.target.session_dir / "local_main.json" and not failed:
                failed = True
                raise OSError("simulated disk failure")
            atomic_copy(source, target)

        with patch("claude_recent_sync.transfers.atomic_copy", side_effect=fail_once):
            with self.assertRaises(OSError):
                service.run(preview["planId"], settle_delay=0)
        self.assertEqual(file_sha256(self.target.memory / "MEMORY.md"), before)
        self.assertEqual({p.name for p in self.target.target.session_dir.glob("local_*.json")}, {"local_other.json"})

    def test_global_memory_is_optional(self):
        write(self.target.home / ".claude/CLAUDE.md", "Target-specific global preference")
        service, _ = self.prepare()
        preview = service.preview("package:snapshot", "Claude/account-b", include_global=False)
        service.run(preview["planId"], settle_delay=0)
        self.assertEqual((self.target.home / ".claude/CLAUDE.md").read_text(), "Target-specific global preference")

    def test_path_mapping_is_not_reapplied_and_preserves_text(self):
        mapping = PathMapping("/Users/old", "/Users/new", {"/Users/old/Desktop/Work": "/Users/new/Desktop/Work"})
        self.assertEqual(mapping.text("/Users/old/Desktop/Work"), "/Users/new/Desktop/Work")
        self.assertEqual(mapping.text("/Users/older/Desktop/Work"), "/Users/older/Desktop/Work")
        self.assertEqual(mapping.text("/Users/old/Desktop/Working"), "/Users/new/Desktop/Working")

    def test_bundle_contains_only_linked_transcripts_and_shared_memory(self):
        folder = self.root / "inspection"
        data = open_bundle(self.bundle, folder, PASSWORD)
        files = [record["path"] for record in data["files"]]
        self.assertTrue(any(p.endswith("/source-session.jsonl") for p in files))
        self.assertFalse(any(p.endswith("/old-session.jsonl") for p in files))
        self.assertTrue(any(p.endswith("/memory/MEMORY.md") for p in files))

    def test_prior_branches_subagents_and_file_history_are_included(self):
        f = self.source
        d = json.loads((f.source.session_dir / "local_main.json").read_text())
        d["priorCliSessionIds"] = ["old-session"]
        write(f.source.session_dir / "local_main.json", d)
        parent = f.layout.projects_dir / project_key(str(f.project))
        write(parent / "source-session/subagents/agent-example.jsonl", '{"type":"user"}\n')
        write(f.home / ".claude/file-history/source-session/checkpoint@v1", "old file bytes")
        output = self.root / "linked.crsync"
        export_bundle(f.layout, f.source, output, PASSWORD)
        data = open_bundle(output, self.root / "linked", PASSWORD)
        paths = [r["path"] for r in data["files"]]
        self.assertTrue(any(p.endswith("/old-session.jsonl") for p in paths))
        self.assertTrue(any(p.endswith("/subagents/agent-example.jsonl") for p in paths))
        self.assertIn("file-history/source-session/checkpoint@v1", paths)

    @unittest.skipUnless(Path("/opt/homebrew/opt/openssl@3/bin/openssl").exists()
                         and Path("/usr/bin/openssl").exists(), "Both OpenSSL and LibreSSL are required")
    def test_openssl_libressl_cross_mac_compatibility(self):
        plaintext = self.root / "sample"
        plaintext.write_bytes(b"portable context" * 100)
        for number, (writer, reader) in enumerate([
            ("/opt/homebrew/opt/openssl@3/bin/openssl", "/usr/bin/openssl"),
            ("/usr/bin/openssl", "/opt/homebrew/opt/openssl@3/bin/openssl"),
        ]):
            encrypted = self.root / f"portable-{number}.crsync"
            restored = self.root / f"portable-{number}.txt"
            encrypt(plaintext, encrypted, PASSWORD, executable=writer)
            decrypt(encrypted, restored, PASSWORD, executable=reader)
            self.assertEqual(restored.read_bytes(), plaintext.read_bytes())
