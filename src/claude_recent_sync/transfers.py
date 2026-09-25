from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from .accounts import AccountCatalog, account_context, context_signature, default_selections, project_key
from .bundles import GLOBAL_NAMES, PathMapping, materialize_file, private_dir, regular_files, validate_bundle
from .cli import ProfileRef, SyncError, file_sha256, local_json_files, safe_load_json
from .workflow import (
    detailed_plan, iso_now, profile_summary, run_sync_workflow,
    stale_resume_ids, stop_target_backends, target_backend_processes,
)


def atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name("." + target.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def checked_destination(root: Path, relative: str) -> Path:
    path = root / relative
    if (path.is_symlink() or root.resolve() not in path.resolve().parents
            or any(parent.is_symlink() for parent in path.parents if parent != root and root in parent.parents)):
        raise SyncError("目标目录包含越界路径或符号链接，请检查后重试。")
    return path


class AccountTransfers:
    def __init__(self, layout, state_dir: Path):
        self.state_dir = private_dir(state_dir)
        self.catalog = AccountCatalog(layout, self.state_dir / "account-emails.json")
        self.plans: dict[str, dict[str, Any]] = {}
        self.lock = threading.RLock()

    def packages(self) -> dict[str, tuple[Path, dict]]:
        results = {}
        for root in (self.state_dir / "imports").glob("*"):
            data = safe_load_json(root / "manifest.json")
            if isinstance(data, dict) and data.get("format") == 1:
                results["package:" + root.name] = (root, data)
        return results

    def accounts(self) -> list[dict[str, Any]]:
        rows = self.catalog.accounts()
        for key, (_, data) in self.packages().items():
            rows.append({"key": key, "accountId": data["accountId"], "deployment": data["deployment"],
                         **self.catalog.identities.lookup(key, data.get("accountEmail")),
                         "modeLabel": "迁移包 · " + ("第三方模式" if data["deployment"] == "Claude-3p" else "普通模式"),
                         "isPackage": True, "createdAt": data["createdAt"], "isLoggedIn": False,
                         "defaultProfileId": data["profileId"], "sessionCount": data["indexCount"],
                         "profiles": [{"profileId": data["profileId"], "sessionCount": data["indexCount"]}]})
        for number, row in enumerate(sorted(rows, key=lambda row: row["key"]), 1):
            row["displayName"] = row["email"] or f"待绑定账号 {number}"
        return rows

    def bind_email(self, key, email):
        if not any(row["key"] == key for row in self.accounts()):
            raise SyncError("选中的账号已不存在，请刷新账号列表。")
        self.catalog.identities.bind(key, email)
        return next(row for row in self.accounts() if row["key"] == key)

    def identity(self, key):
        row = next(row for row in self.accounts() if row["key"] == key)
        return {"accountKey": key, "email": row["email"], "displayName": row["displayName"],
                "deployment": row["deployment"]}

    def memory_plan(self, layout, ref, *, package=None, mapping=None, paths=None, include_global=True):
        rows = []
        if package is None:
            roots = account_context(layout, ref)["projectRoots"]
            files = [p for root in roots for p in regular_files(Path(root) / "memory")]
            if include_global:
                for name in GLOBAL_NAMES:
                    p = layout.projects_dir.parent / name
                    if p.is_file() and not p.is_symlink():
                        files.append(p)
                    elif p.is_dir() and not p.is_symlink():
                        files.extend(regular_files(p))
            rows = [{"path": str(p.relative_to(layout.projects_dir.parent)), "kind": "shared", "size": p.stat().st_size,
                     "currentSha256": file_sha256(p)}
                    for p in files]
        else:
            root, data = package
            key_map = {row["key"]: project_key(row["target"]) for row in paths}
            with tempfile.TemporaryDirectory(dir=self.state_dir) as folder:
                expected = set()
                for record in data["files"]:
                    rel = Path(record["path"])
                    if rel.parts[0] == "projects" and "memory" in rel.parts[2:]:
                        destination = Path("projects", key_map[rel.parts[1]], *rel.parts[2:])
                    elif rel.parts[0] == "global" and include_global:
                        destination = Path(*rel.parts[1:])
                    else:
                        continue
                    expected.add(destination.as_posix())
                    target = checked_destination(layout.projects_dir.parent, destination.as_posix())
                    staged = Path(folder) / destination
                    materialize_file(root / rel, staged, mapping,
                                     metadata_only=rel.parts[:3] == ("global", "codex-context", "index"))
                    kind = "added" if not target.exists() else "unchanged" if file_sha256(staged) == file_sha256(target) else "updated"
                    rows.append({"path": destination.as_posix(), "kind": kind, "size": staged.stat().st_size,
                                 "currentSha256": file_sha256(target) if target.is_file() else None})
                for key in key_map.values():
                    for p in regular_files(layout.projects_dir / key / "memory"):
                        rel = p.relative_to(layout.projects_dir.parent).as_posix()
                        if rel not in expected:
                            rows.append({"path": rel, "kind": "deleted", "size": p.stat().st_size,
                                         "currentSha256": file_sha256(p)})
        counts = {kind: sum(row["kind"] == kind for row in rows)
                  for kind in ("added", "updated", "deleted", "unchanged", "shared")}
        return {"files": sorted(rows, key=lambda row: row["path"]), "counts": counts,
                "changed": sum(counts[kind] for kind in ("added", "updated", "deleted"))}

    def _package_view(self, key, target_layout, mappings, include_global):
        pair = self.packages().get(key)
        if pair is None:
            raise SyncError("账号迁移包已不存在，请重新导入。")
        root, data = pair
        mapping = PathMapping(data["sourceHome"], str(target_layout.projects_dir.parent.parent), mappings)
        project_paths = [{"source": row["cwd"], "target": mapping.text(row["cwd"]), "key": row["key"]}
                         for row in data["roots"]]
        for row in project_paths:
            mapping.rules[data["sourceHome"] + "/.claude/projects/" + row["key"]] = (
                str(target_layout.projects_dir / project_key(row["target"])))
        keys = [project_key(row["target"]) for row in project_paths]
        if len(keys) != len(set(keys)):
            raise SyncError("多个来源项目映射到相同目录，请调整项目路径。")
        cache_id = hashlib.sha256(json.dumps([key, mappings, str(target_layout.projects_dir)], sort_keys=True).encode()).hexdigest()
        index_dir = private_dir(self.state_dir / "previews" / cache_id / "indexes")
        for p in local_json_files(root / "indexes"):
            temporary = index_dir / ("." + p.name + "." + uuid.uuid4().hex)
            try:
                materialize_file(p, temporary, mapping, index=True)
                os.replace(temporary, index_dir / p.name)
            finally:
                temporary.unlink(missing_ok=True)
        ref = ProfileRef(data["accountId"], data["profileId"], index_dir, None)
        scope = dict(data["context"])
        scope.pop("projectRoots", None)
        scope["storage"] = "package"
        scope["globalMemoryPresent"] = include_global and (root / "global/CLAUDE.md").exists()
        return root, data, mapping, project_paths, ref, scope

    def preview(self, source_key=None, target_key=None, source_profile=None, target_profile=None,
                include_global=True, mappings=None):
        rows = self.accounts()
        source_default, target_default = default_selections(self.catalog)
        source_key = source_key or source_default
        target_key = target_key or target_default
        empty = {"sessions": [], "changed": False, "counts": dict.fromkeys(
            ["all", "added", "updated", "deleted", "unchanged", "branchChanged"], 0)}
        if not source_key or not target_key:
            target_summary = None
            if target_key:
                target_layout, target = self.catalog.resolve(target_key, target_profile)
                target_summary = profile_summary(target_layout, target)
                target_summary.update(self.identity(target_key))
            message = "请选择来源账号。" if rows and target_key else "请先在 Claude Desktop 登录并创建一次 Code 会话。"
            return {"accounts": rows, "source": None, "target": target_summary, "plan": empty,
                    "planId": None, "context": None, "blockers": [message]}
        target_layout, target = self.catalog.resolve(target_key, target_profile)
        is_package = source_key.startswith("package:")
        paths = []
        if is_package:
            root, data, mapping, paths, source, scope = self._package_view(
                source_key, target_layout, mappings or {}, include_global)
            source_layout = None
            source_summary = {"accountId": source.account_id, "profileId": source.profile_id,
                              "label": source_key, "sessionCount": data["indexCount"],
                              "newestSessionIso": data["createdAt"],
                              "health": {"ok": True, "invalidIndexes": [], "missingTranscripts": []}}
            fingerprint = file_sha256(root / "manifest.json")
            memory = self.memory_plan(target_layout, target, package=(root, data), mapping=mapping,
                                      paths=paths, include_global=include_global)
        else:
            source_layout, source = self.catalog.resolve(source_key, source_profile)
            source_summary = profile_summary(source_layout, source)
            scope = account_context(source_layout, source)
            scope.pop("projectRoots", None)
            fingerprint = context_signature(source_layout, source)
            memory = self.memory_plan(source_layout, source, include_global=include_global)
        source_summary.update(self.identity(source_key))
        target_summary = profile_summary(target_layout, target)
        target_summary.update(self.identity(target_key))
        blockers = []
        if source.session_dir.resolve() == target.session_dir.resolve():
            blockers.append("来源与目标相同。")
        if not source_summary["sessionCount"]:
            blockers.append("来源账号没有会话。")
        if not source_summary["health"]["ok"]:
            blockers.append("来源账号有缺失或无效的对话记录。")
        missing_paths = sorted({row["target"] for row in paths if not Path(row["target"]).is_dir()})
        if missing_paths:
            blockers.append("目标项目路径不存在，请先同步项目文件或调整路径。")
        plan_id = uuid.uuid4().hex
        params = {"source_key": source_key, "target_key": target_key, "source_profile": source.profile_id,
                  "target_profile": target.profile_id, "include_global": include_global, "mappings": mappings or {}}
        with self.lock:
            self.plans[plan_id] = {"params": params, "fingerprint": fingerprint,
                                   "targetFingerprint": hashlib.sha256(
                                       (context_signature(target_layout, target)
                                        + json.dumps(memory["files"], sort_keys=True)).encode()).hexdigest(),
                                   "blockers": blockers}
            if len(self.plans) > 128:
                self.plans.pop(next(iter(self.plans)))
        return {"accounts": rows, "source": source_summary, "target": target_summary,
                "plan": detailed_plan(source, target), "planId": plan_id, "context": scope,
                "projectMappings": paths, "missingPaths": missing_paths, "blockers": blockers,
                "generatedAt": iso_now(), "includeGlobal": include_global, "memoryPlan": memory}

    def run(self, plan_id, progress=None, settle_delay=5):
        with self.lock:
            frozen = self.plans.pop(plan_id, None)
        if frozen is None:
            raise SyncError("比较结果已过期，请刷新后重试。")
        if frozen["blockers"]:
            raise SyncError(" ".join(frozen["blockers"]))
        params = frozen["params"]
        fresh = self.preview(**params)
        with self.lock:
            current = self.plans.pop(fresh["planId"])
        if (current["fingerprint"] != frozen["fingerprint"]
                or current["targetFingerprint"] != frozen["targetFingerprint"] or current["blockers"]):
            raise SyncError("账号数据自比较后已改变，请刷新差异再同步。")
        target_layout, target = self.catalog.resolve(params["target_key"], params["target_profile"])
        if params["source_key"].startswith("package:"):
            result = self._import(params, target_layout, target, progress, settle_delay)
            result["source"].update(self.identity(params["source_key"]))
            result["target"].update(self.identity(params["target_key"]))
            return result
        source_layout, source = self.catalog.resolve(params["source_key"], params["source_profile"])
        before_context = account_context(source_layout, source)
        result = run_sync_workflow(target_layout, source, target, progress=progress, settle_delay=settle_delay)
        context = account_context(target_layout, target)
        if context["memoryFileCount"] != before_context["memoryFileCount"] or context["missingTranscripts"]:
            raise SyncError("目标会话与共享项目记忆的关联验证失败。")
        context.pop("projectRoots", None)
        result.update({"context": context, "sourceAccountKey": params["source_key"], "targetAccountKey": params["target_key"]})
        result["source"].update(self.identity(params["source_key"]))
        result["target"].update(self.identity(params["target_key"]))
        if progress:
            progress("memory", "核验关联记忆", "completed", f"{context['memoryFileCount']} 个共享项目记忆文件")
        return result

    def _import(self, params, layout, target, progress, settle_delay):
        def emit(key, title, state, detail=None):
            if progress:
                progress(key, title, state, detail)

        root, data, mapping, paths, source, scope = self._package_view(
            params["source_key"], layout, params["mappings"], params["include_global"])
        emit("scan", "验证迁移包", "running")
        validate_bundle(root)
        emit("scan", "验证迁移包", "completed", f"{data['indexCount']} 个会话")
        key_map = {row["key"]: project_key(row["target"]) for row in paths}
        initial = detailed_plan(source, target)
        backup = private_dir(layout.backup_root / (time.strftime("%Y%m%dT%H%M%S") + "-import-" + uuid.uuid4().hex[:8]))
        stage = private_dir(self.state_dir / "staging" / uuid.uuid4().hex)
        mutations = []
        home = layout.projects_dir.parent
        try:
            emit("prepare", "准备路径与记忆", "running")
            for number, record in enumerate(data["files"]):
                rel = Path(record["path"])
                if rel.parts[0] == "indexes":
                    continue
                if rel.parts[0] == "global":
                    if not params["include_global"]:
                        continue
                    destination = Path(*rel.parts[1:])
                elif rel.parts[0] == "file-history":
                    destination = rel
                else:
                    destination = Path("projects", key_map[rel.parts[1]], *rel.parts[2:])
                checked_destination(home, destination.as_posix())
                staged = stage / destination
                materialize_file(root / rel, staged, mapping,
                                 metadata_only=rel.parts[:3] == ("global", "codex-context", "index"))
                mutations.append({"relative": destination.as_posix(), "sha256": file_sha256(staged)})
                if number % 500 == 0:
                    emit("prepare", "准备路径与记忆", "running", f"{number + 1} / {len(data['files'])}")
            # Only selected project memory directories are mirrored; unrelated transcripts stay intact.
            covered = {row["relative"] for row in mutations}
            for mapped_key in key_map.values():
                memory = layout.projects_dir / mapped_key / "memory"
                for p in memory.rglob("*"):
                    if p.is_file():
                        rel = p.relative_to(home).as_posix()
                        if rel not in covered:
                            checked_destination(home, rel)
                            mutations.append({"relative": rel, "sha256": None})
            emit("prepare", "准备路径与记忆", "completed", f"{len(mutations)} 个文件")
            emit("backup", "备份目标账号", "running")
            index_backup = backup / "target-before-sync"
            index_backup.mkdir()
            for p in local_json_files(target.session_dir):
                shutil.copy2(p, index_backup / p.name)
            for row in mutations:
                dest = checked_destination(home, row["relative"])
                row["existed"] = dest.exists()
                if dest.exists():
                    if not dest.is_file():
                        raise SyncError("目标文件位置被目录占用。")
                    atomic_copy(dest, backup / "context-before" / row["relative"])
                    row["beforeSha256"] = file_sha256(backup / "context-before" / row["relative"])
            manifest = {"kind": "account-import", "createdAt": iso_now(), "claudeHome": str(home),
                        "source": {"account_id": source.account_id, "profile_id": source.profile_id},
                        "target": {"account_id": target.account_id, "profile_id": target.profile_id,
                                   "session_dir": str(target.session_dir)},
                        "sourceCount": data["indexCount"], "targetCountBefore": initial["targetCountBefore"],
                        "added": initial["added"], "updated": initial["updated"], "deleted": initial["deleted"],
                        "mutations": mutations,
                        "indexHashes": {p.name: file_sha256(p) for p in local_json_files(index_backup)}}
            (backup / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=True, indent=2))
            emit("backup", "备份目标账号", "completed")
            emit("conflicts", "检查旧会话后台", "running")
            stopped = stop_target_backends(target_backend_processes(target, stale_resume_ids(initial)))
            emit("conflicts", "检查旧会话后台", "completed", f"已结束 {len(stopped)} 个旧后台")
            emit("mirror", "写入上下文与记忆", "running")
            try:
                for row in mutations:
                    dest = checked_destination(home, row["relative"])
                    if row["sha256"] is None:
                        dest.unlink(missing_ok=True)
                    else:
                        atomic_copy(stage / row["relative"], dest)
                target.session_dir.mkdir(parents=True, exist_ok=True)
                expected = {p.name for p in local_json_files(source.session_dir)}
                for p in local_json_files(target.session_dir):
                    if p.name not in expected:
                        p.unlink()
                for p in local_json_files(source.session_dir):
                    atomic_copy(p, target.session_dir / p.name)
                for row in mutations:
                    dest = checked_destination(home, row["relative"])
                    if (row["sha256"] is None and dest.exists()) or (
                        row["sha256"] is not None and file_sha256(dest) != row["sha256"]
                    ):
                        raise SyncError("写入后的文件校验失败。")
                emit("mirror", "写入上下文与记忆", "completed")
                emit("settle", "延迟复查", "running", f"{settle_delay} 秒")
                time.sleep(settle_delay)
                final = detailed_plan(source, target)
                context = account_context(layout, target)
                for row in mutations:
                    dest = checked_destination(home, row["relative"])
                    if (row["sha256"] is None and dest.exists()) or (
                        row["sha256"] is not None and (not dest.is_file() or file_sha256(dest) != row["sha256"])
                    ):
                        raise SyncError("上下文或记忆在延迟复查时发生变化，同步已回滚。请结束正在运行的会话后重试。")
                if final["changed"] or context["missingTranscripts"]:
                    raise SyncError("Claude 再次改变了目标记录，同步已回滚。请退出 Claude 后重试。")
            except Exception:
                restore_import(backup, layout, create_safety_backup=False)
                raise
            emit("settle", "延迟复查", "completed", "索引、转录与记忆验证通过")
            context.pop("projectRoots", None)
            return {"id": uuid.uuid4().hex, "operation": "sync", "status": "success",
                    "completedAt": iso_now(), "counts": initial["counts"], "context": context,
                    "source": {"accountId": source.account_id, "profileId": source.profile_id},
                    "target": {"accountId": target.account_id, "profileId": target.profile_id},
                    "targetCountAfter": len(local_json_files(target.session_dir)), "backup": str(backup),
                    "exactSame": True, "semanticSame": True, "stoppedBackends": stopped,
                    "targetHealth": {"missingTranscripts": [], "invalidIndexes": [], "ok": True}}
        finally:
            shutil.rmtree(stage, ignore_errors=True)


def restore_import(backup: Path, layout, create_safety_backup=True):
    data = safe_load_json(backup / "manifest.json")
    if not isinstance(data, dict) or data.get("kind") != "account-import":
        raise SyncError("无效的上下文备份。")
    home = layout.projects_dir.parent
    if Path(data["claudeHome"]).resolve() != home.resolve():
        raise SyncError("备份属于另一台电脑。")
    target_dir = Path(data["target"]["session_dir"])
    valid_target = any(
        row["accountId"] == data["target"]["account_id"]
        and any(p["profileId"] == data["target"]["profile_id"] for p in row["profiles"])
        and catalog.layouts[row["deployment"]].profile_ref(row["accountId"], data["target"]["profile_id"]).session_dir.resolve() == target_dir.resolve()
        for catalog in [AccountCatalog(layout)] for row in catalog.accounts()
    )
    if not valid_target:
        raise SyncError("备份目标目录无效。")
    for row in data["mutations"]:
        checked_destination(home, row["relative"])
        if row["existed"] and file_sha256(backup / "context-before" / row["relative"]) != row["beforeSha256"]:
            raise SyncError("备份记忆文件校验失败。")
    if {p.name: file_sha256(p) for p in local_json_files(backup / "target-before-sync")} != data["indexHashes"]:
        raise SyncError("备份会话索引校验失败。")
    current_ids = set()
    target_ref = ProfileRef(data["target"]["account_id"], data["target"]["profile_id"], target_dir, None)
    expected_ids = {(safe_load_json(p) or {}).get("cliSessionId") for p in local_json_files(backup / "target-before-sync")}
    for p in local_json_files(target_dir):
        sid = (safe_load_json(p) or {}).get("cliSessionId")
        if isinstance(sid, str) and sid not in expected_ids:
            current_ids.add(sid)
    stop_target_backends(target_backend_processes(target_ref, current_ids))
    safety = None
    if create_safety_backup:
        safety = private_dir(layout.backup_root / (time.strftime("%Y%m%dT%H%M%S") + "-restore-" + uuid.uuid4().hex[:8]))
        (safety / "target-before-sync").mkdir()
        for p in local_json_files(target_dir):
            shutil.copy2(p, safety / "target-before-sync" / p.name)
        reverse = dict(data)
        reverse["mutations"] = []
        reverse["createdAt"] = iso_now()
        reverse["targetCountBefore"] = len(local_json_files(target_dir))
        reverse["indexHashes"] = {p.name: file_sha256(p) for p in local_json_files(safety / "target-before-sync")}
        for row in data["mutations"]:
            dest = checked_destination(home, row["relative"])
            reverse["mutations"].append({"relative": row["relative"], "existed": dest.is_file(), "sha256": None,
                                         "beforeSha256": file_sha256(dest) if dest.is_file() else None})
            if dest.is_file():
                atomic_copy(dest, safety / "context-before" / row["relative"])
        (safety / "manifest.json").write_text(json.dumps(reverse, ensure_ascii=True, indent=2))
    for row in reversed(data["mutations"]):
        dest = checked_destination(home, row["relative"])
        if row["existed"]:
            atomic_copy(backup / "context-before" / row["relative"], dest)
        else:
            dest.unlink(missing_ok=True)
    expected = {p.name for p in local_json_files(backup / "target-before-sync")}
    for p in local_json_files(target_dir):
        if p.name not in expected:
            p.unlink()
    for p in local_json_files(backup / "target-before-sync"):
        atomic_copy(p, target_dir / p.name)
    if {p.name: file_sha256(p) for p in local_json_files(target_dir)} != data["indexHashes"]:
        raise SyncError("恢复后的会话索引校验失败。")
    for row in data["mutations"]:
        dest = checked_destination(home, row["relative"])
        if row["existed"] and file_sha256(dest) != row["beforeSha256"]:
            raise SyncError("恢复后的记忆文件校验失败。")
        if not row["existed"] and dest.exists():
            raise SyncError("恢复后出现多余的上下文文件。")
    return {"id": uuid.uuid4().hex, "operation": "restore", "status": "success", "completedAt": iso_now(),
            "target": {"accountId": data["target"]["account_id"], "profileId": data["target"]["profile_id"]},
            "targetCountAfter": len(expected), "backup": str(safety) if safety else None}
