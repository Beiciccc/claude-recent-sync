from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .cli import (
    ClaudeLayout,
    MirrorPlan,
    ProfileRef,
    SyncError,
    compute_plan,
    doctor_profile,
    local_json_files,
    mirror_sessions,
    plan_to_dict,
    read_session_index,
    safe_load_json,
)


SESSION_COMPARE_FIELDS = (
    "title",
    "cliSessionId",
    "completedTurns",
    "lastActivityAt",
    "cwd",
    "originCwd",
    "isArchived",
)
SEMANTIC_FIELDS = frozenset(SESSION_COMPARE_FIELDS)
RESUME_PATTERN = re.compile(r"(?:^|\s)--resume(?:=|\s+)([0-9a-fA-F-]{36})(?:\s|$)")

ProgressCallback = Callable[[str, str, str, Optional[str]], None]


def app_state_dir() -> Path:
    return Path.home() / ".claude" / "claude-recent-sync"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def timestamp_to_iso(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    seconds = value / 1000 if value > 10_000_000_000 else value
    try:
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def session_view(path: Path | None, *, name: str) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    data = read_session_index(path)
    if not data:
        return {
            "indexName": name,
            "title": "Invalid session index",
            "invalid": True,
        }
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        title = "Untitled session"
    completed_turns = data.get("completedTurns")
    if not isinstance(completed_turns, int):
        completed_turns = 0
    return {
        "indexName": name,
        "sessionId": data.get("sessionId"),
        "title": title,
        "cliSessionId": data.get("cliSessionId"),
        "completedTurns": completed_turns,
        "createdAt": data.get("createdAt"),
        "createdAtIso": timestamp_to_iso(data.get("createdAt")),
        "lastActivityAt": data.get("lastActivityAt"),
        "lastActivityAtIso": timestamp_to_iso(data.get("lastActivityAt")),
        "lastFocusedAt": data.get("lastFocusedAt"),
        "lastFocusedAtIso": timestamp_to_iso(data.get("lastFocusedAt")),
        "cwd": data.get("cwd"),
        "originCwd": data.get("originCwd"),
        "model": data.get("model"),
        "isArchived": bool(data.get("isArchived", False)),
        "invalid": False,
    }


def session_field_changes(source: dict[str, Any] | None, target: dict[str, Any] | None) -> list[str]:
    if source is None or target is None:
        return []
    return [field for field in SESSION_COMPARE_FIELDS if source.get(field) != target.get(field)]


def detailed_plan(source: ProfileRef, target: ProfileRef, *, delete: bool = True) -> dict[str, Any]:
    plan = compute_plan(source, target, delete=delete)
    source_files = {path.name: path for path in local_json_files(source.session_dir)}
    target_files = {path.name: path for path in local_json_files(target.session_dir)}
    added = {item.name for item in plan.added}
    updated = {item.name for item in plan.updated}
    deleted = {item.name for item in plan.deleted}

    sessions: list[dict[str, Any]] = []
    for name in sorted(set(source_files) | set(target_files)):
        source_data = session_view(source_files.get(name), name=name)
        target_data = session_view(target_files.get(name), name=name)
        if name in added:
            kind = "added"
        elif name in updated:
            kind = "updated"
        elif name in deleted:
            kind = "deleted"
        else:
            kind = "unchanged"
        field_changes = session_field_changes(source_data, target_data)
        activity_values = [
            value
            for value in (
                (source_data or {}).get("lastActivityAt"),
                (target_data or {}).get("lastActivityAt"),
            )
            if isinstance(value, (int, float))
        ]
        sessions.append(
            {
                "indexName": name,
                "kind": kind,
                "fieldChanges": field_changes,
                "branchChanged": "cliSessionId" in field_changes,
                "source": source_data,
                "target": target_data,
                "sortActivity": max(activity_values, default=0),
            }
        )

    sessions.sort(key=lambda item: (-item["sortActivity"], item["indexName"]))
    counts = {
        "all": len(sessions),
        "added": len(plan.added),
        "updated": len(plan.updated),
        "deleted": len(plan.deleted),
        "unchanged": sum(item["kind"] == "unchanged" for item in sessions),
        "branchChanged": sum(item["branchChanged"] for item in sessions),
    }
    return plan_to_dict(plan) | {"sessions": sessions, "counts": counts}


def profile_summary(layout: ClaudeLayout, ref: ProfileRef) -> dict[str, Any]:
    files = local_json_files(ref.session_dir)
    newest = max((path.stat().st_mtime for path in files), default=None)
    health = doctor_profile(layout, ref)
    return {
        "accountId": ref.account_id,
        "profileId": ref.profile_id,
        "label": ref.label,
        "sessionDir": str(ref.session_dir),
        "sessionCount": len(files),
        "newestSessionMtime": newest,
        "newestSessionIso": datetime.fromtimestamp(newest, timezone.utc).isoformat() if newest else None,
        "health": health,
    }


def dashboard_snapshot(
    layout: ClaudeLayout,
    *,
    source_value: str = "previous",
    target_value: str = "current",
    source_profile: str | None = None,
    target_profile: str | None = None,
) -> dict[str, Any]:
    source = layout.resolve_profile(source_value, source_profile)
    target = layout.resolve_profile(target_value, target_profile)
    profiles = []
    for row in layout.summarize_profiles():
        profiles.append(
            {
                "accountId": row.ref.account_id,
                "profileId": row.ref.profile_id,
                "label": row.ref.label,
                "sessionCount": row.session_count,
                "newestSessionMtime": row.newest_session_mtime,
                "newestSessionIso": (
                    datetime.fromtimestamp(row.newest_session_mtime, timezone.utc).isoformat()
                    if row.newest_session_mtime
                    else None
                ),
                "isCurrentAccount": row.is_current_account,
                "isPreviousAccount": row.is_previous_account,
            }
        )
    return {
        "generatedAt": iso_now(),
        "activeAccountId": layout.active_account_id(),
        "previousAccountId": layout.previous_account_id(),
        "source": profile_summary(layout, source),
        "target": profile_summary(layout, target),
        "plan": detailed_plan(source, target),
        "profiles": profiles,
    }


def stale_resume_ids(plan: dict[str, Any]) -> set[str]:
    stale: set[str] = set()
    for item in plan["sessions"]:
        if item["kind"] not in {"updated", "deleted"}:
            continue
        source_id = (item.get("source") or {}).get("cliSessionId")
        target_id = (item.get("target") or {}).get("cliSessionId")
        if isinstance(target_id, str) and target_id and target_id != source_id:
            stale.add(target_id)
    return stale


def target_backend_processes(target: ProfileRef, resume_ids: set[str] | None = None) -> list[dict[str, Any]]:
    try:
        output = subprocess.check_output(
            ["ps", "-Ao", "pid=,ppid=,comm="],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        raise SyncError("无法检查 Claude 后台进程，请稍后重试。")

    markers = (
        f"/{target.account_id}/{target.profile_id}/",
        f"/{target.profile_id}/{target.account_id}/",
    )
    matches: list[dict[str, Any]] = []
    for line in output.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        pid_text, ppid_text, command = parts
        executable = Path(command).name
        if executable not in {"claude", "disclaimer"}:
            continue
        try:
            args = subprocess.check_output(["ps", "-p", pid_text, "-o", "args="], text=True,
                                           stderr=subprocess.DEVNULL).strip()
        except subprocess.CalledProcessError:
            continue
        if not any(marker in args for marker in markers):
            continue
        resume_match = RESUME_PATTERN.search(args)
        if not resume_match:
            continue
        resume_id = resume_match.group(1)
        if resume_ids is not None and resume_id not in resume_ids:
            continue
        matches.append(
            {
                "pid": int(pid_text),
                "ppid": int(ppid_text),
                "executable": executable,
                "resumeId": resume_id,
            }
        )
    return matches


def stop_target_backends(processes: list[dict[str, Any]], *, timeout: float = 2.0) -> list[dict[str, Any]]:
    if not processes:
        return []
    pids = {int(item["pid"]) for item in processes}
    parent_ids = {int(item["ppid"]) for item in processes}
    ordered = sorted(processes, key=lambda item: (int(item["pid"]) in parent_ids, int(item["pid"])))
    stopped: list[dict[str, Any]] = []
    for item in ordered:
        try:
            os.kill(int(item["pid"]), signal.SIGTERM)
            stopped.append(item)
        except ProcessLookupError:
            stopped.append(item | {"alreadyStopped": True})
        except PermissionError as exc:
            raise SyncError(f"Cannot stop stale Claude backend process {item['pid']}: {exc}") from exc

    deadline = time.monotonic() + timeout
    remaining = set(pids)
    while remaining and time.monotonic() < deadline:
        for pid in list(remaining):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                remaining.remove(pid)
            except PermissionError:
                remaining.remove(pid)
        if remaining:
            time.sleep(0.05)
    if remaining:
        raise SyncError("旧会话后台尚未退出，请退出 Claude 后重试。")
    return stopped


def semantic_plan_same(plan: dict[str, Any]) -> bool:
    for item in plan["sessions"]:
        if item["kind"] in {"added", "deleted"}:
            return False
        if SEMANTIC_FIELDS.intersection(item["fieldChanges"]):
            return False
    return True


def no_op_mirror_result(plan: MirrorPlan) -> dict[str, Any]:
    return plan_to_dict(plan) | {
        "dryRun": False,
        "targetCountAfter": plan.target_count,
        "overallSame": True,
        "backup": None,
    }


def run_sync_workflow(
    layout: ClaudeLayout,
    source: ProfileRef,
    target: ProfileRef,
    *,
    progress: ProgressCallback | None = None,
    settle_delay: float = 5.0,
    retry_count: int = 1,
) -> dict[str, Any]:
    def emit(step_id: str, label: str, state: str, detail: str | None = None) -> None:
        if progress:
            progress(step_id, label, state, detail)

    if source.session_dir.resolve() == target.session_dir.resolve():
        raise SyncError("Source and target profiles must be different.")

    started_at = iso_now()
    emit("scan", "扫描会话索引", "running", None)
    source_health = doctor_profile(layout, source)
    if not source_health["ok"]:
        raise SyncError(
            "Source validation failed: "
            f"{len(source_health['invalidIndexes'])} invalid indexes, "
            f"{len(source_health['missingTranscripts'])} missing transcripts."
        )
    initial_detail = detailed_plan(source, target)
    initial_plan = compute_plan(source, target)
    emit("scan", "扫描会话索引", "completed", f"发现 {initial_plan.source_count} 个会话")

    emit("conflicts", "检查运行中的会话分支", "running", None)
    conflicts = target_backend_processes(target, stale_resume_ids(initial_detail))
    stopped = stop_target_backends(conflicts)
    emit(
        "conflicts",
        "检查运行中的会话分支",
        "completed",
        f"已结束 {len(stopped)} 个旧后台" if stopped else "未发现旧分支后台",
    )

    emit("mirror", "备份并镜像", "running", None)
    if initial_plan.changed:
        mirror_result = mirror_sessions(
            layout,
            source,
            target,
            dry_run=False,
            delete=True,
            allow_empty_source=False,
        )
    else:
        mirror_result = no_op_mirror_result(initial_plan)
    emit("mirror", "备份并镜像", "completed", f"目标已有 {mirror_result['targetCountAfter']} 个会话")

    emit("validate", "验证完整对话记录", "running", None)
    target_health = doctor_profile(layout, target)
    immediate_plan = detailed_plan(source, target)
    if not target_health["ok"]:
        raise SyncError(
            "Target validation failed: "
            f"{len(target_health['invalidIndexes'])} invalid indexes, "
            f"{len(target_health['missingTranscripts'])} missing transcripts."
        )
    if immediate_plan["changed"]:
        raise SyncError("Immediate verification failed: target does not match source.")
    emit("validate", "验证完整对话记录", "completed", "全部会话引用有效")

    attempts: list[dict[str, Any]] = []
    final_plan = immediate_plan
    emit("settle", "等待 Claude Desktop 稳定", "running", f"{settle_delay:g} 秒延迟复查")
    if settle_delay > 0:
        time.sleep(settle_delay)
    final_plan = detailed_plan(source, target)

    for attempt in range(retry_count):
        if not final_plan["changed"]:
            break
        retry_conflicts = target_backend_processes(target, stale_resume_ids(final_plan))
        retry_stopped = stop_target_backends(retry_conflicts)
        retry_result = mirror_sessions(
            layout,
            source,
            target,
            dry_run=False,
            delete=True,
            allow_empty_source=False,
        )
        stopped.extend(retry_stopped)
        attempts.append(
            {
                "attempt": attempt + 1,
                "stoppedBackends": retry_stopped,
                "backup": retry_result.get("backup"),
            }
        )
        if not mirror_result.get("backup") and retry_result.get("backup"):
            mirror_result["backup"] = retry_result["backup"]
        if settle_delay > 0:
            time.sleep(settle_delay)
        final_plan = detailed_plan(source, target)

    exact_same = not final_plan["changed"]
    semantic_same = semantic_plan_same(final_plan)
    final_health = doctor_profile(layout, target)
    if exact_same:
        settle_detail = "延迟复查无差异"
    elif semantic_same:
        settle_detail = "对话分支一致，Claude 刷新了本地元数据"
    else:
        settle_detail = "同步后再次出现对话分支差异"
    emit("settle", "等待 Claude Desktop 稳定", "completed" if semantic_same else "failed", settle_detail)

    result = {
        "id": str(uuid.uuid4()),
        "operation": "sync",
        "status": "success" if exact_same else ("warning" if semantic_same else "failed"),
        "startedAt": started_at,
        "completedAt": iso_now(),
        "source": {
            "accountId": source.account_id,
            "profileId": source.profile_id,
            "label": source.label,
        },
        "target": {
            "accountId": target.account_id,
            "profileId": target.profile_id,
            "label": target.label,
        },
        "counts": initial_detail["counts"],
        "sourceCount": initial_plan.source_count,
        "targetCountBefore": initial_plan.target_count,
        "targetCountAfter": len(local_json_files(target.session_dir)),
        "backup": mirror_result.get("backup"),
        "stoppedBackends": stopped,
        "retryAttempts": attempts,
        "sourceHealth": source_health,
        "targetHealth": final_health,
        "exactSame": exact_same,
        "semanticSame": semantic_same,
        "remainingPlan": final_plan,
    }
    if not semantic_same:
        raise SyncError("Delayed verification failed: conversation branches no longer match the source.")
    return result


class JsonStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()

    def read(self, default: Any) -> Any:
        with self.lock:
            value = safe_load_json(self.path)
            return value if value is not None else default

    def write(self, value: Any) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            with temp_path.open("w", encoding="utf-8") as fh:
                json.dump(value, fh, indent=2, ensure_ascii=False, sort_keys=True)
                fh.write("\n")
            temp_path.replace(self.path)


class HistoryStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_state_dir() / "history.jsonl"
        self.lock = threading.RLock()

    def append(self, item: dict[str, Any]) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            if not self.path.exists():
                return []
            items: list[dict[str, Any]] = []
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    items.append(value)
            return list(reversed(items[-limit:]))


def list_backups(layout: ClaudeLayout, limit: int = 100) -> list[dict[str, Any]]:
    if not layout.backup_root.exists():
        return []
    backups: list[dict[str, Any]] = []
    for directory in sorted(layout.backup_root.iterdir(), reverse=True):
        if not directory.is_dir():
            continue
        manifest = safe_load_json(directory / "manifest.json")
        if not isinstance(manifest, dict):
            continue
        target_files = local_json_files(directory / "target-before-sync")
        backups.append(
            {
                "id": directory.name,
                "path": str(directory),
                "createdAt": manifest.get("createdAt"),
                "source": manifest.get("source"),
                "target": manifest.get("target"),
                "sourceCount": manifest.get("sourceCount", 0),
                "targetCountBefore": manifest.get("targetCountBefore", len(target_files)),
                "backupFileCount": len(target_files),
                "contextFileCount": len(manifest.get("mutations", [])),
                "counts": {
                    "added": len(manifest.get("added", [])),
                    "updated": len(manifest.get("updated", [])),
                    "deleted": len(manifest.get("deleted", [])),
                },
            }
        )
        if len(backups) >= limit:
            break
    return backups


def resolve_backup(layout: ClaudeLayout, backup_id: str) -> tuple[Path, dict[str, Any]]:
    backup_root = layout.backup_root.resolve()
    backup_dir = (backup_root / backup_id).resolve()
    if backup_dir.parent != backup_root or not backup_dir.is_dir():
        raise SyncError("Unknown backup.")
    manifest = safe_load_json(backup_dir / "manifest.json")
    if not isinstance(manifest, dict):
        raise SyncError("Backup manifest is missing or invalid.")
    return backup_dir, manifest


def restore_backup_workflow(
    layout: ClaudeLayout,
    backup_id: str,
    *,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    def emit(step_id: str, label: str, state: str, detail: str | None = None) -> None:
        if progress:
            progress(step_id, label, state, detail)

    backup_dir, manifest = resolve_backup(layout, backup_id)
    target_data = manifest.get("target")
    if not isinstance(target_data, dict):
        raise SyncError("Backup target is missing.")
    account_id = target_data.get("account_id") or target_data.get("accountId")
    profile_id = target_data.get("profile_id") or target_data.get("profileId")
    if not isinstance(account_id, str) or not isinstance(profile_id, str):
        raise SyncError("Backup target identifiers are invalid.")

    source = ProfileRef("backup", backup_id, backup_dir / "target-before-sync", None)
    target = layout.profile_ref(account_id, profile_id)
    emit("scan", "读取备份", "completed", f"发现 {len(local_json_files(source.session_dir))} 个会话")
    source_health = doctor_profile(layout, source)
    if not source_health["ok"]:
        raise SyncError("Backup transcript validation failed.")
    plan_detail = detailed_plan(source, target)
    conflicts = target_backend_processes(target, stale_resume_ids(plan_detail))
    stopped = stop_target_backends(conflicts)
    emit("mirror", "恢复备份", "running", None)
    result = mirror_sessions(
        layout,
        source,
        target,
        dry_run=False,
        delete=True,
        allow_empty_source=True,
    )
    health = doctor_profile(layout, target)
    if not health["ok"]:
        raise SyncError("Restored target failed transcript validation.")
    emit("mirror", "恢复备份", "completed", f"已恢复 {result['targetCountAfter']} 个会话")
    return {
        "id": str(uuid.uuid4()),
        "operation": "restore",
        "status": "success",
        "startedAt": iso_now(),
        "completedAt": iso_now(),
        "backupId": backup_id,
        "source": {"accountId": "backup", "profileId": backup_id, "label": backup_id},
        "target": {"accountId": account_id, "profileId": profile_id, "label": target.label},
        "counts": plan_detail["counts"],
        "targetCountAfter": result["targetCountAfter"],
        "backup": result.get("backup"),
        "stoppedBackends": stopped,
        "targetHealth": health,
        "exactSame": True,
        "semanticSame": True,
    }
