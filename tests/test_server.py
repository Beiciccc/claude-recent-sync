from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from test_accounts import Fixture
from claude_recent_sync.server import AppContext, AppHTTPServer


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = Fixture(Path(self.temp.name) / "mac")
        self.context = AppContext(self.fixture.layout, port=0, state_dir=Path(self.temp.name) / "state")
        self.server = AppHTTPServer(("127.0.0.1", 0), self.context, Path(self.temp.name))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, body=None, headers=None):
        request = urllib.request.Request(self.url + path, data=body, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            exc.close()
            raise

    def post(self, path, body):
        return json.loads(self.request(path, json.dumps(body).encode(),
            {"Content-Type": "application/json", "X-CSRF-Token": self.context.csrf_token}))

    def wait_job(self, job_id):
        end = time.monotonic() + 10
        while time.monotonic() < end:
            job = self.context.jobs.get(job_id)
            if job["status"] not in {"queued", "running"}:
                self.assertEqual(job["status"], "success", job.get("error"))
                return job
            time.sleep(.02)
        self.fail("Task did not finish")

    def test_state_lists_accounts_and_memory(self):
        state = json.loads(self.request("/api/state?source=Claude/account-a&target=Claude/account-b"))
        self.assertEqual(len(state["accounts"]), 2)
        self.assertEqual(state["context"]["storage"], "shared-projects")
        self.assertTrue(state["memoryPlan"]["files"])
        self.assertIn("planId", state)

    def test_remote_host_and_cross_origin_mutations_are_rejected(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.request("/api/state", headers={"Host": "untrusted.example"})
        with self.assertRaises(urllib.error.HTTPError):
            self.request("/api/settings", b'{"autoSync":true}', {"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError):
            self.request("/api/settings", b'{"autoSync":true}', {
                "Content-Type": "application/json", "X-CSRF-Token": self.context.csrf_token,
                "Origin": "https://untrusted.example"})
        self.assertFalse(self.context.settings.get()["autoSync"])

    def test_export_upload_open_round_trip_does_not_apply(self):
        self.post("/api/accounts/email", {"accountKey": "Claude/account-a", "email": "source@example.com"})
        job = self.post("/api/packages/export", {"source": "Claude/account-a", "sourceProfile": "team-a",
                        "password": "synthetic-password-123", "includeGlobal": True})
        exported = self.wait_job(job["jobId"])
        ciphertext = self.request(exported["result"]["downloadUrl"])
        self.assertNotIn(b"Historical path", ciphertext)
        upload = json.loads(self.request("/api/packages/upload", ciphertext, {
            "Content-Type": "application/octet-stream", "X-CSRF-Token": self.context.csrf_token}))
        opened = self.post("/api/packages/open", {"uploadId": upload["uploadId"],
                                                 "password": "synthetic-password-123"})
        result = self.wait_job(opened["jobId"])
        state = json.loads(self.request("/api/state?source=" + result["result"]["sourceAccountKey"]
                                       + "&target=Claude/account-b"))
        self.assertTrue(state["source"]["accountKey"].startswith("package:"))
        self.assertEqual(state["source"]["email"], "source@example.com")
        self.assertNotIn(b"source@example.com", ciphertext)
        self.assertTrue((self.fixture.target.session_dir / "local_other.json").exists())
        self.assertNotIn("synthetic-password-123", self.context.history.path.read_text())

    def test_email_binding_changes_label_not_selected_identity(self):
        before = self.fixture.target.session_dir / "local_other.json"
        original = before.read_bytes()
        result = self.post("/api/accounts/email", {"accountKey": "Claude/account-b", "email": "target@example.com"})
        self.assertEqual(result["account"]["displayName"], "target@example.com")
        state = json.loads(self.request("/api/state?source=Claude/account-a&target=Claude/account-b"))
        self.assertEqual(state["target"]["accountKey"], "Claude/account-b")
        self.assertEqual(state["target"]["email"], "target@example.com")
        self.assertEqual(before.read_bytes(), original)
        with self.assertRaises(urllib.error.HTTPError):
            self.request("/api/accounts/email", b'{"accountKey":"Claude/account-b","email":"bad@example.com"}',
                         {"Content-Type": "application/json"})
