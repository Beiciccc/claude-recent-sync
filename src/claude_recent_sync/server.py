from __future__ import annotations

import argparse
import json
import mimetypes
import plistlib
import re
import secrets
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import __version__
from .cli import ClaudeLayout, SyncError, default_claude_dir, default_projects_dir
from .bundles import MAX_BYTES, export_bundle, open_bundle, private_dir
from .transfers import AccountTransfers, restore_import
from .workflow import (
    HistoryStore,
    JsonStore,
    app_state_dir,
    iso_now,
    list_backups,
    restore_backup_workflow,
    run_sync_workflow,
)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 47631
LAUNCH_AGENT_LABEL = "com.beiciccc.claude-recent-sync"


class SettingsStore:
    def __init__(self) -> None:
        self.store = JsonStore(app_state_dir() / "settings.json")

    def get(self) -> dict[str, Any]:
        value = self.store.read({})
        if not isinstance(value, dict):
            value = {}
        return {
            "autoSync": bool(value.get("autoSync", False)),
            "launchAtLogin": launch_agent_path().exists(),
            "lastActiveAccountId": value.get("lastActiveAccountId"),
            "lastAutoSyncAt": value.get("lastAutoSyncAt"),
            "lastAutoError": value.get("lastAutoError"),
        }

    def update(self, values: dict[str, Any]) -> dict[str, Any]:
        current = self.store.read({})
        if not isinstance(current, dict):
            current = {}
        current.update(values)
        self.store.write(current)
        return self.get()


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def ui_launcher_path() -> Path:
    source_launcher = repo_root() / "bin" / "claude-recent-sync-ui"
    if source_launcher.exists():
        return source_launcher
    installed = shutil.which("claude-recent-sync-ui")
    if installed:
        return Path(installed)
    raise SyncError("The UI launcher could not be located.")


def set_launch_at_login(enabled: bool, *, port: int) -> None:
    path = launch_agent_path()
    if not enabled:
        path.unlink(missing_ok=True)
        return

    state_dir = app_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    launcher = ui_launcher_path()
    payload = {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [str(launcher), "--no-browser", "--port", str(port)],
        "WorkingDirectory": str(repo_root()),
        "RunAtLoad": True,
        "ProcessType": "Interactive",
        "LimitLoadToSessionType": "Aqua",
        "StandardOutPath": str(state_dir / "server.log"),
        "StandardErrorPath": str(state_dir / "server-error.log"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".plist.tmp")
    with temporary.open("wb") as fh:
        plistlib.dump(payload, fh, sort_keys=True)
    temporary.replace(path)


class JobManager:
    def __init__(self, layout: ClaudeLayout, history: HistoryStore) -> None:
        self.layout = layout
        self.history = history
        self.lock = threading.RLock()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.running_id: str | None = None

    def _new_job(self, operation: str, *, automatic: bool) -> dict[str, Any]:
        job_id = str(uuid.uuid4())
        job = {
            "id": job_id,
            "operation": operation,
            "automatic": automatic,
            "status": "queued",
            "createdAt": iso_now(),
            "completedAt": None,
            "steps": [],
            "result": None,
            "error": None,
        }
        self.jobs[job_id] = job
        self.running_id = job_id
        return job

    def _progress(self, job_id: str, step_id: str, label: str, state: str, detail: str | None) -> None:
        with self.lock:
            job = self.jobs[job_id]
            existing = next((item for item in job["steps"] if item["id"] == step_id), None)
            payload = {"id": step_id, "label": label, "state": state, "detail": detail}
            if existing is None:
                job["steps"].append(payload)
            else:
                existing.update(payload)
            job["status"] = "running"

    def _finish(self, job_id: str, result: dict[str, Any] | None, error: Exception | None) -> None:
        with self.lock:
            job = self.jobs[job_id]
            job["completedAt"] = iso_now()
            if error is None and result is not None:
                job["status"] = result.get("status", "success")
                job["result"] = result
                for step in job["steps"]:
                    if step["state"] == "running":
                        step["state"] = "completed"
                self.history.append(result | {"jobId": job_id, "automatic": job["automatic"]})
            else:
                job["status"] = "failed"
                job["error"] = str(error or "Unknown error")
                running_step = next(
                    (item for item in reversed(job["steps"]) if item["state"] == "running"),
                    None,
                )
                if running_step:
                    running_step.update({"state": "failed", "detail": job["error"]})
                elif not job["steps"]:
                    job["steps"].append(
                        {"id": "error", "label": "任务失败", "state": "failed", "detail": job["error"]}
                    )
                self.history.append(
                    {
                        "jobId": job_id,
                        "id": job_id,
                        "operation": job["operation"],
                        "automatic": job["automatic"],
                        "status": "failed",
                        "startedAt": job["createdAt"],
                        "completedAt": job["completedAt"],
                        "error": job["error"],
                    }
                )
            if self.running_id == job_id:
                self.running_id = None

    def start_task(self, operation, run) -> str:
        with self.lock:
            if self.running_id:
                raise SyncError("另一个任务正在运行，请等待完成。")
            job_id = self._new_job(operation, automatic=False)["id"]

        def worker():
            try:
                result = run(lambda *args: self._progress(job_id, *args))
                self._finish(job_id, result, None)
            except Exception as exc:
                self._finish(job_id, None, exc)

        threading.Thread(target=worker, name=f"account-{job_id[:8]}", daemon=True).start()
        return job_id

    def start_sync(
        self,
        *,
        source_value: str,
        target_value: str,
        source_profile: str | None = None,
        target_profile: str | None = None,
        automatic: bool = False,
        settle_delay: float = 5.0,
    ) -> str:
        with self.lock:
            if self.running_id:
                raise SyncError("Another synchronization task is already running.")
            job = self._new_job("sync", automatic=automatic)
            job_id = job["id"]

        def worker() -> None:
            try:
                source = self.layout.resolve_profile(source_value, source_profile)
                target = self.layout.resolve_profile(target_value, target_profile)
                result = run_sync_workflow(
                    self.layout,
                    source,
                    target,
                    progress=lambda *args: self._progress(job_id, *args),
                    settle_delay=settle_delay,
                )
                self._finish(job_id, result, None)
            except Exception as exc:
                self._finish(job_id, None, exc)

        threading.Thread(target=worker, name=f"sync-{job_id[:8]}", daemon=True).start()
        return job_id

    def start_restore(self, *, backup_id: str) -> str:
        with self.lock:
            if self.running_id:
                raise SyncError("Another synchronization task is already running.")
            job = self._new_job("restore", automatic=False)
            job_id = job["id"]

        def worker() -> None:
            try:
                from .workflow import resolve_backup
                directory, manifest = resolve_backup(self.layout, backup_id)
                if manifest.get("kind") == "account-import":
                    self._progress(job_id, "restore", "恢复账号与记忆", "running", None)
                    result = restore_import(directory, self.layout)
                else:
                    result = restore_backup_workflow(
                        self.layout,
                        backup_id,
                        progress=lambda *args: self._progress(job_id, *args),
                    )
                self._finish(job_id, result, None)
            except Exception as exc:
                self._finish(job_id, None, exc)

        threading.Thread(target=worker, name=f"restore-{job_id[:8]}", daemon=True).start()
        return job_id

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            job = self.jobs.get(job_id)
            return json.loads(json.dumps(job)) if job else None

    def latest(self) -> dict[str, Any] | None:
        with self.lock:
            if not self.jobs:
                return None
            job = next(reversed(self.jobs.values()))
            return json.loads(json.dumps(job))


class AutoSyncWatcher:
    def __init__(self, layout: ClaudeLayout, jobs: JobManager, settings: SettingsStore) -> None:
        self.layout = layout
        self.jobs = jobs
        self.settings = settings
        self.stop_event = threading.Event()
        self.pending: tuple[str, str] | None = None
        self.thread = threading.Thread(target=self._run, name="account-switch-watcher", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def _run(self) -> None:
        while not self.stop_event.wait(3.0):
            current = self.layout.active_account_id()
            if not current:
                continue
            settings = self.settings.get()
            previous_active = settings.get("lastActiveAccountId")
            if not isinstance(previous_active, str) or not previous_active:
                self.settings.update({"lastActiveAccountId": current})
                continue

            if self.pending:
                job_id, pending_target = self.pending
                job = self.jobs.get(job_id)
                if job and job["status"] in {"queued", "running"}:
                    continue
                values: dict[str, Any] = {"lastActiveAccountId": pending_target}
                if job and job["status"] in {"success", "warning"}:
                    values.update({"lastAutoSyncAt": iso_now(), "lastAutoError": None})
                else:
                    values["lastAutoError"] = (job or {}).get("error", "Automatic synchronization failed.")
                self.settings.update(values)
                self.pending = None
                continue

            if current == previous_active:
                continue
            if not settings["autoSync"]:
                self.settings.update({"lastActiveAccountId": current})
                continue
            try:
                job_id = self.jobs.start_sync(
                    source_value=previous_active,
                    target_value=current,
                    automatic=True,
                )
            except SyncError:
                continue
            self.pending = (job_id, current)


class AppContext:
    def __init__(self, layout: ClaudeLayout, *, port: int, state_dir: Path | None = None) -> None:
        self.layout = layout
        self.port = port
        self.csrf_token = secrets.token_urlsafe(32)
        self.state_dir = private_dir(state_dir or app_state_dir())
        if state_dir is not None:
            layout.backup_root = self.state_dir / "backups"
        self.settings = SettingsStore()
        self.settings.store = JsonStore(self.state_dir / "settings.json")
        self.history = HistoryStore(self.state_dir / "history.jsonl")
        self.jobs = JobManager(layout, self.history)
        self.transfers = AccountTransfers(layout, self.state_dir)
        self.watcher = AutoSyncWatcher(layout, self.jobs, self.settings)
        current = layout.active_account_id()
        if current and not self.settings.get().get("lastActiveAccountId"):
            self.settings.update({"lastActiveAccountId": current})

    def state(self, query: dict[str, list[str]]) -> dict[str, Any]:
        source_value = first_query_value(query, "source", None)
        target_value = first_query_value(query, "target", None)
        source_profile = first_query_value(query, "sourceProfile", None)
        target_profile = first_query_value(query, "targetProfile", None)
        try:
            mappings = json.loads(first_query_value(query, "mappings", "{}"))
            if not isinstance(mappings, dict):
                raise ValueError()
        except (ValueError, TypeError) as exc:
            raise SyncError("项目路径映射无效。") from exc
        snapshot = self.transfers.preview(
            source_key=source_value,
            target_key=target_value,
            source_profile=source_profile,
            target_profile=target_profile,
            include_global=first_query_value(query, "includeGlobal", "true") == "true",
            mappings=mappings,
        )
        return snapshot | {
            "csrfToken": self.csrf_token,
            "version": __version__,
            "settings": self.settings.get(),
            "latestJob": self.jobs.latest(),
        }

    def export_account(self, body):
        key = str(body.get("source") or "")
        layout, ref = self.transfers.catalog.resolve(key, body.get("sourceProfile"))
        identity = self.transfers.identity(key)
        token = uuid.uuid4().hex
        path = private_dir(self.state_dir / "exports") / (token + ".crsync")

        def run(progress):
            result = export_bundle(layout, ref, path, str(body.get("password") or ""),
                                   include_global=body.get("includeGlobal", True), progress=progress,
                                   account_email=identity["email"])
            result["downloadUrl"] = "/api/packages/" + path.name
            return result

        return self.jobs.start_task("export", run)

    def open_account(self, body):
        upload = str(body.get("uploadId") or "")
        if not re.fullmatch(r"[a-f0-9]{32}", upload):
            raise SyncError("无效的上传文件。")
        path = self.state_dir / "uploads" / (upload + ".crsync")
        if not path.is_file():
            raise SyncError("上传文件不存在。")
        token = uuid.uuid4().hex
        destination = private_dir(self.state_dir / "imports") / token

        def run(progress):
            progress("package", "解密并校验迁移包", "running", None)
            data = open_bundle(path, destination, str(body.get("password") or ""), progress=progress)
            path.unlink()
            return {"id": token, "operation": "import", "status": "success", "completedAt": iso_now(),
                    "sourceAccountKey": "package:" + token, "targetCountAfter": data["indexCount"],
                    "source": {"accountId": data["accountId"], "profileId": data["profileId"],
                               **self.transfers.identity("package:" + token)},
                    "context": data["context"]}

        return self.jobs.start_task("import", run)


def first_query_value(query: dict[str, list[str]], key: str, default: str | None) -> str | None:
    values = query.get(key)
    if not values:
        return default
    value = values[0].strip()
    return value or default


class AppRequestHandler(BaseHTTPRequestHandler):
    server_version = "ClaudeRecentSync/0.3"

    @property
    def context(self) -> AppContext:
        return self.server.context  # type: ignore[attr-defined]

    @property
    def static_dir(self) -> Path:
        return self.server.static_dir  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status: int, message: str) -> None:
        self.send_json({"error": message}, status=status)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        try:
            self.require_local_host()
            if parsed.path == "/api/ping":
                self.send_json({"app": "claude-recent-sync", "version": __version__})
                return
            if parsed.path == "/api/state":
                self.send_json(self.context.state(query))
                return
            if parsed.path == "/api/history":
                self.send_json(
                    {
                        "events": self.context.history.list(),
                        "backups": list_backups(self.context.layout),
                    }
                )
                return
            if parsed.path.startswith("/api/packages/"):
                name = parsed.path.rsplit("/", 1)[-1]
                if not re.fullmatch(r"[a-f0-9]{32}\.crsync", name):
                    raise SyncError("未知的迁移包。")
                path = self.context.state_dir / "exports" / name
                if not path.is_file():
                    raise SyncError("迁移包不存在。")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", f'attachment; filename="account-{name}"')
                self.send_header("Content-Length", str(path.stat().st_size))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                with path.open("rb") as stream:
                    shutil.copyfileobj(stream, self.wfile)
                return
            if parsed.path.startswith("/api/jobs/"):
                job_id = parsed.path.rsplit("/", 1)[-1]
                job = self.context.jobs.get(job_id)
                if not job:
                    self.send_error_json(HTTPStatus.NOT_FOUND, "Task not found.")
                    return
                self.send_json(job)
                return
            if parsed.path.startswith("/api/"):
                self.send_error_json(HTTPStatus.NOT_FOUND, "Endpoint not found.")
                return
            self.serve_static(parsed.path)
        except SyncError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            self.require_local_host()
            self.require_csrf()
            if parsed.path == "/api/packages/upload":
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_BYTES or length > shutil.disk_usage(self.context.state_dir).free:
                    raise SyncError("迁移包大小无效或磁盘空间不足。")
                token = uuid.uuid4().hex
                path = private_dir(self.context.state_dir / "uploads") / (token + ".crsync")
                self.connection.settimeout(60)
                try:
                    with path.open("xb") as output:
                        path.chmod(0o600)
                        remaining = length
                        while remaining:
                            block = self.rfile.read(min(1024 ** 2, remaining))
                            if not block:
                                raise SyncError("迁移包上传中断。")
                            output.write(block)
                            remaining -= len(block)
                except Exception:
                    path.unlink(missing_ok=True)
                    raise
                self.send_json({"uploadId": token})
                return
            body = self.read_json_body()
            if parsed.path == "/api/accounts/email":
                account = self.context.transfers.bind_email(str(body.get("accountKey") or ""), body.get("email"))
                self.send_json({"account": account})
                return
            if parsed.path == "/api/sync":
                plan_id = str(body.get("planId") or "")
                job_id = self.context.jobs.start_task(
                    "sync", lambda progress: self.context.transfers.run(plan_id, progress))
                self.send_json({"jobId": job_id}, status=HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/packages/export":
                self.send_json({"jobId": self.context.export_account(body)}, status=HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/packages/open":
                self.send_json({"jobId": self.context.open_account(body)}, status=HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/restore":
                backup_id = optional_string(body.get("backupId"))
                if not backup_id:
                    raise SyncError("Backup id is required.")
                job_id = self.context.jobs.start_restore(backup_id=backup_id)
                self.send_json({"jobId": job_id}, status=HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/settings":
                updates: dict[str, Any] = {}
                if "autoSync" in body:
                    updates["autoSync"] = bool(body["autoSync"])
                if "launchAtLogin" in body:
                    enabled = bool(body["launchAtLogin"])
                    set_launch_at_login(enabled, port=self.context.port)
                    updates["launchAtLogin"] = enabled
                settings = self.context.settings.update(updates)
                self.send_json({"settings": settings})
                return
            if parsed.path == "/api/reveal":
                path_value = optional_string(body.get("path"))
                if not path_value:
                    raise SyncError("Path is required.")
                path = Path(path_value).expanduser().resolve()
                allowed = [self.context.layout.backup_root.resolve(), (self.context.state_dir / "exports").resolve()]
                if not any(root in path.parents or path == root for root in allowed):
                    raise SyncError("只能打开本工具的备份或迁移包。")
                subprocess.Popen(["open", "-R", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.send_json({"ok": True})
                return
            self.send_error_json(HTTPStatus.NOT_FOUND, "Endpoint not found.")
        except SyncError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except json.JSONDecodeError:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "Invalid JSON body.")
        except Exception as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def require_csrf(self) -> None:
        token = self.headers.get("X-CSRF-Token")
        if not secrets.compare_digest(token or "", self.context.csrf_token):
            raise SyncError("Invalid request token. Refresh the app and try again.")
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if origin and host and urllib.parse.urlparse(origin).netloc != host:
            raise SyncError("Cross-origin requests are not allowed.")

    def require_local_host(self) -> None:
        host = urllib.parse.urlsplit("//" + self.headers.get("Host", "")).hostname
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise SyncError("只允许本机访问。")

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 64 * 1024:
            raise SyncError("Request body is too large.")
        data = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise SyncError("JSON body must be an object.")
        return data

    def serve_static(self, request_path: str) -> None:
        relative = request_path.lstrip("/") or "index.html"
        candidate = (self.static_dir / relative).resolve()
        static_root = self.static_dir.resolve()
        if static_root not in candidate.parents and candidate != static_root:
            self.send_error_json(HTTPStatus.NOT_FOUND, "File not found.")
            return
        if not candidate.is_file():
            candidate = self.static_dir / "index.html"
        if not candidate.is_file():
            self.send_error_json(HTTPStatus.SERVICE_UNAVAILABLE, "The web interface has not been built.")
            return
        body = candidate.read_bytes()
        mime_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(body)))
        cache = "no-cache" if candidate.name == "index.html" else "public, max-age=31536000, immutable"
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'",
        )
        self.end_headers()
        self.wfile.write(body)


def optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


class AppHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], context: AppContext, static_dir: Path) -> None:
        super().__init__(address, AppRequestHandler)
        self.context = context
        self.static_dir = static_dir


def existing_server_url(host: str, port: int) -> str | None:
    url = f"http://{host}:{port}"
    try:
        with urllib.request.urlopen(f"{url}/api/ping", timeout=0.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return None
    return url if payload.get("app") == "claude-recent-sync" else None


def run_ui(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
    claude_dir: Path | None = None,
    projects_dir: Path | None = None,
    state_dir: Path | None = None,
) -> int:
    if host not in {"127.0.0.1", "localhost"}:
        raise SyncError("账号数据服务只支持本机地址 127.0.0.1 或 localhost。")
    existing = existing_server_url(host, port)
    if existing:
        if open_browser:
            webbrowser.open(existing)
        return 0

    static_dir = Path(__file__).resolve().parent / "web_dist"
    layout = ClaudeLayout(claude_dir or default_claude_dir(), projects_dir or default_projects_dir())
    context = AppContext(layout, port=port, state_dir=state_dir)
    try:
        server = AppHTTPServer((host, port), context, static_dir)
    except OSError as exc:
        raise SyncError(f"Cannot start the local interface on {host}:{port}: {exc}") from exc

    context.watcher.start()
    url = f"http://{host}:{port}"
    if open_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    print(f"Claude Recent Sync is running at {url}")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        context.watcher.stop()
        server.server_close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Launch the Claude Recent Sync local interface.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--claude-dir", type=Path, default=default_claude_dir())
    parser.add_argument("--projects-dir", type=Path, default=default_projects_dir())
    parser.add_argument("--state-dir", type=Path, help="Local settings and migration package directory.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run_ui(
            host=args.host,
            port=args.port,
            open_browser=not args.no_browser,
            claude_dir=args.claude_dir,
            projects_dir=args.projects_dir,
            state_dir=args.state_dir,
        )
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
