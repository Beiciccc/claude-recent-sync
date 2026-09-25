from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import shutil
import struct
import subprocess
import tarfile
import tempfile
import uuid
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

from .accounts import account_context, project_key
from .cli import ClaudeLayout, ProfileRef, SyncError, file_sha256, local_json_files, read_session_index
from .workflow import iso_now

MAGIC = b"CRSYNC1\n"
ITERATIONS = 200_000
MAX_BYTES = 32 * 1024 ** 3
GLOBAL_NAMES = {"CLAUDE.md", "rules", "commands", "agents", "skills", "codex-context"}
PATH_FIELDS = {"cwd", "originCwd", "projectPath", "fullPath", "worktreePath", "originalCwd", "transcriptPath"}


def private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def check_password(password: str) -> None:
    if len(password) < 12 or "\n" in password or "\r" in password or len(password) > 1024:
        raise SyncError("迁移口令至少 12 个字符，不能包含换行。")


@lru_cache(maxsize=4)
def salt_options(executable: str) -> list[str]:
    help_result = subprocess.run([executable, "enc", "-help"], capture_output=True)
    # Pin the salt size for interoperability with macOS LibreSSL.
    return ["-saltlen", "8"] if b"-saltlen" in help_result.stdout + help_result.stderr else []


def encrypt(source: Path, destination: Path, password: str, *, executable="openssl") -> None:
    check_password(password)
    salt = secrets.token_bytes(16)
    header = json.dumps({"version": 1, "salt": salt.hex(), "iterations": ITERATIONS}).encode()
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    with tempfile.TemporaryDirectory(dir=destination.parent) as folder:
        cipher = Path(folder) / "cipher"
        result = subprocess.run(
            [executable, "enc", "-aes-256-cbc", "-pbkdf2", *salt_options(executable), "-iter", str(ITERATIONS),
             "-md", "sha256", "-salt", "-in", str(source), "-out", str(cipher), "-pass", "stdin"],
            input=(password + "\n").encode(), capture_output=True,
        )
        if result.returncode:
            raise SyncError("加密失败，请确认本机 OpenSSL 支持 PBKDF2。")
        prefix = MAGIC + struct.pack(">I", len(header)) + header
        digest = hmac.new(key, prefix, hashlib.sha256)
        with destination.open("xb") as output, cipher.open("rb") as stream:
            destination.chmod(0o600)
            output.write(prefix)
            for block in iter(lambda: stream.read(1024 ** 2), b""):
                digest.update(block)
                output.write(block)
            output.write(digest.digest())


def decrypt(source: Path, destination: Path, password: str, *, executable="openssl") -> None:
    check_password(password)
    with source.open("rb") as stream:
        if stream.read(len(MAGIC)) != MAGIC:
            raise SyncError("不是受支持的账号迁移包。")
        length_bytes = stream.read(4)
        if len(length_bytes) != 4:
            raise SyncError("迁移包头部不完整。")
        length = struct.unpack(">I", length_bytes)[0]
        if length > 4096:
            raise SyncError("迁移包头部无效。")
        raw = stream.read(length)
        try:
            header = json.loads(raw)
            salt = bytes.fromhex(header["salt"])
            if header["version"] != 1 or header["iterations"] != ITERATIONS or len(salt) != 16:
                raise ValueError()
        except (ValueError, KeyError, TypeError) as exc:
            raise SyncError("迁移包头部无效。") from exc
        size = source.stat().st_size - len(MAGIC) - 4 - length - 32
        if size <= 0 or size > MAX_BYTES:
            raise SyncError("迁移包大小无效。")
        key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
        digest = hmac.new(key, MAGIC + length_bytes + raw, hashlib.sha256)
        with tempfile.TemporaryDirectory(dir=destination.parent) as folder:
            cipher = Path(folder) / "cipher"
            with cipher.open("wb") as output:
                remaining = size
                while remaining:
                    block = stream.read(min(1024 ** 2, remaining))
                    if not block:
                        raise SyncError("迁移包不完整。")
                    output.write(block)
                    digest.update(block)
                    remaining -= len(block)
            if not hmac.compare_digest(digest.digest(), stream.read(32)):
                raise SyncError("口令不正确，或迁移包已损坏。")
            result = subprocess.run(
                [executable, "enc", "-d", "-aes-256-cbc", "-pbkdf2", *salt_options(executable), "-iter", str(ITERATIONS),
                 "-md", "sha256", "-in", str(cipher), "-out", str(destination), "-pass", "stdin"],
                input=(password + "\n").encode(), capture_output=True,
            )
            if result.returncode:
                raise SyncError("迁移包解密失败。")
            destination.chmod(0o600)


def valid_payload_path(value: str) -> PurePosixPath:
    p = PurePosixPath(value)
    if p.is_absolute() or ".." in p.parts or "\\" in value or "\x00" in value or str(p) != value:
        raise SyncError("迁移包包含非法路径。")
    parts = p.parts
    if len(parts) == 2 and parts[0] == "indexes" and parts[1].startswith("local_") and p.suffix == ".json":
        return p
    if len(parts) >= 3 and parts[0] == "projects" and parts[1].startswith("-"):
        return p
    if len(parts) >= 3 and parts[0] == "file-history":
        return p
    if len(parts) >= 2 and parts[0] == "global" and parts[1] in GLOBAL_NAMES:
        return p
    raise SyncError("迁移包包含未允许的文件。")


def regular_files(root: Path):
    for path in sorted(root.rglob("*")):
        if (path.is_file() and not path.is_symlink() and path.name != ".DS_Store"
                and "__pycache__" not in path.parts and root.resolve() in path.resolve().parents):
            yield path


def export_bundle(layout: ClaudeLayout, ref: ProfileRef, output: Path, password: str,
                  include_global: bool = True, progress=None, account_email: str | None = None) -> dict[str, Any]:
    check_password(password)
    if progress:
        progress("scan", "关联会话分支与记忆", "running", None)
    context = account_context(layout, ref)
    if context["missingTranscripts"] or not local_json_files(ref.session_dir):
        raise SyncError("来源为空或缺失完整对话记录，无法导出。")
    private_dir(output.parent)
    indexes = local_json_files(ref.session_dir)
    roots = [Path(root) for root in context["projectRoots"]]
    transcript_index = layout.build_transcript_index()
    cwd_by_root: dict[str, str] = {}
    linked: dict[str, str] = {}

    def references(value, cwd):
        if isinstance(value, str) and value in transcript_index:
            linked.setdefault(value, cwd)
        elif isinstance(value, list):
            for item in value:
                references(item, cwd)
        elif isinstance(value, dict):
            for item in value.values():
                references(item, cwd)

    for p in indexes:
        d = read_session_index(p)
        cwd = d.get("cwd")
        if not isinstance(cwd, str) or not Path(cwd).is_absolute():
            raise SyncError("来源会话缺少有效项目路径。")
        actual = transcript_index[d["cliSessionId"]].relative_to(layout.projects_dir).parts[0]
        cwd_by_root.setdefault(actual, cwd)
        cwd_by_root.setdefault(project_key(cwd), cwd)
        references(d, cwd)
    selected = set()
    scanned = set()
    while set(linked) - scanned:
        sid = next(iter(set(linked) - scanned))
        scanned.add(sid)
        path = transcript_index[sid]
        if path.is_symlink():
            raise SyncError("关联转录是符号链接，请先将它还原为本机普通文件。")
        selected.add(path)
        key = path.relative_to(layout.projects_dir).parts[0]
        cwd_by_root.setdefault(key, linked[sid])
        related_root = layout.projects_dir / key
        if related_root not in roots:
            roots.append(related_root)
        # Older CLI versions stored subagent files at the project root.
        with path.open("rb") as stream:
            for line in stream:
                if not any(marker in line for marker in (b'"agentId"', b'"parentSessionId"', b'"forkedFromSessionId"')):
                    continue
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        continue
                except (ValueError, UnicodeDecodeError):
                    continue
                for key in ("parentSessionId", "forkedFromSessionId"):
                    references(row.get(key), linked[sid])
                tool_result = row.get("toolUseResult")
                for agent in (row.get("agentId"), tool_result.get("agentId") if isinstance(tool_result, dict) else None):
                    if isinstance(agent, str):
                        references(agent if agent.startswith("agent-") else "agent-" + agent, linked[sid])
    inputs = [(p, "indexes/" + p.name) for p in indexes]
    for root in roots:
        if root.name not in cwd_by_root:
            raise SyncError("无法确定项目目录的原始路径。")
        for p in regular_files(root):
            rel = p.relative_to(root)
            if rel.parts[0] == "memory" or p in selected or any(part in linked for part in rel.parts):
                inputs.append((p, "projects/" + root.name + "/" + rel.as_posix()))
    history = layout.projects_dir.parent / "file-history"
    for sid in linked:
        folder = history / sid
        if folder.is_dir() and not folder.is_symlink():
            inputs.extend((p, "file-history/" + sid + "/" + p.relative_to(folder).as_posix())
                          for p in regular_files(folder))
    if include_global:
        for name in sorted(GLOBAL_NAMES):
            root = layout.projects_dir.parent / name
            if root.is_symlink():
                continue
            if root.is_file():
                inputs.append((root, "global/" + name))
            elif root.is_dir():
                inputs.extend((p, "global/" + name + "/" + p.relative_to(root).as_posix())
                              for p in regular_files(root))
    signatures = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p, _ in inputs}
    context["projectCount"] = len(roots)
    context["memoryFileCount"] = sum(name.startswith("projects/") and "/memory/" in name for _, name in inputs)
    context["transcriptFileCount"] = sum(name.startswith("projects/") and name.endswith(".jsonl") for _, name in inputs)
    if progress:
        progress("scan", "关联会话分支与记忆", "completed", f"{len(indexes)} 个会话")
    total = sum(size for size, _ in signatures.values())
    if total > MAX_BYTES:
        raise SyncError("账号数据超过当前 32 GiB 迁移包上限。")
    records = []
    with tempfile.TemporaryDirectory(dir=output.parent) as folder:
        archive = Path(folder) / "account.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            for number, (path, name) in enumerate(inputs, 1):
                valid_payload_path(name)
                digest = file_sha256(path)
                tar.add(path, arcname=name, recursive=False)
                records.append({"path": name, "size": path.stat().st_size, "sha256": digest})
                if progress and (number == 1 or number % 250 == 0):
                    progress("package", "打包账号上下文", "running", f"{number} / {len(inputs)}")
            manifest = {"format": 1, "createdAt": iso_now(), "sourceHome": str(layout.projects_dir.parent.parent),
                        "accountId": ref.account_id, "profileId": ref.profile_id, "accountEmail": account_email,
                        "deployment": layout.claude_dir.name, "indexCount": len(indexes),
                        "roots": [{"key": root.name, "cwd": cwd_by_root[root.name]} for root in roots],
                        "context": context, "includeGlobal": include_global, "files": records}
            manifest_path = Path(folder) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=True))
            tar.add(manifest_path, arcname="manifest.json", recursive=False)
        if {p.name for p in local_json_files(ref.session_dir)} != {p.name for p in indexes}:
            raise SyncError("来源会话列表在打包期间改变，请重试。")
        for path, signature in signatures.items():
            if not path.exists() or (path.stat().st_size, path.stat().st_mtime_ns) != signature:
                raise SyncError("来源记录在打包期间改变，请等待会话结束后重试。")
        if progress:
            progress("package", "打包账号上下文", "completed", f"{len(inputs)} 个文件")
            progress("encrypt", "加密迁移包", "running", None)
        encrypt(archive, output, password)
    return {"id": str(uuid.uuid4()), "operation": "export", "status": "success",
            "completedAt": iso_now(), "packagePath": str(output), "size": output.stat().st_size,
            "source": {"accountId": ref.account_id, "profileId": ref.profile_id, "email": account_email,
                       "accountKey": layout.claude_dir.name + "/" + ref.account_id},
            "targetCountAfter": len(indexes), "context": context}


def open_bundle(encrypted: Path, destination: Path, password: str, progress=None) -> dict[str, Any]:
    if destination.exists():
        raise SyncError("迁移包导入目录已存在。")
    private_dir(destination)
    try:
        with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
            archive = Path(temp) / "archive.tar.gz"
            decrypt(encrypted, archive, password)
            seen: set[str] = set()
            total = 0
            with tarfile.open(archive, "r:gz") as tar:
                for member in tar:
                    if (not member.isfile() or member.name in seen or member.size < 0
                            or len(seen) >= 200_000):
                        raise SyncError("迁移包包含重复文件或非普通文件。")
                    seen.add(member.name)
                    if member.name != "manifest.json":
                        valid_payload_path(member.name)
                    elif member.size > 64 * 1024 ** 2:
                        raise SyncError("迁移清单过大。")
                    total += member.size
                    if total > MAX_BYTES or member.size + 64 * 1024 ** 2 > shutil.disk_usage(destination).free:
                        raise SyncError("迁移包过大或磁盘空间不足。")
                    path = destination / member.name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    stream = tar.extractfile(member)
                    if stream is None:
                        raise SyncError("迁移包文件无法读取。")
                    with stream, path.open("xb") as output:
                        shutil.copyfileobj(stream, output)
                    path.chmod(0o600)
            data = validate_bundle(destination)
            if progress:
                progress("package", "校验账号迁移包", "completed", f"{len(data['files'])} 个文件")
            return data
    except Exception:
        shutil.rmtree(destination)
        raise


def validate_bundle(root: Path) -> dict[str, Any]:
    try:
        data = json.loads((root / "manifest.json").read_text())
        if data["format"] != 1 or not isinstance(data["files"], list) or not data["indexCount"]:
            raise ValueError()
        records = data["files"]
        expected = set()
        for record in records:
            relative = str(valid_payload_path(record["path"]))
            if relative in expected:
                raise ValueError()
            expected.add(relative)
            path = root / relative
            if (path.is_symlink() or not path.is_file() or path.stat().st_size != record["size"]
                    or file_sha256(path) != record["sha256"]):
                raise ValueError()
        actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
        if actual != expected | {"manifest.json"}:
            raise ValueError()
        if len(list((root / "indexes").glob("local_*.json"))) != data["indexCount"]:
            raise ValueError()
        if not isinstance(data["sourceHome"], str) or not Path(data["sourceHome"]).is_absolute():
            raise ValueError()
        keys = set()
        for row in data["roots"]:
            key = row["key"]
            if (not isinstance(key, str) or "/" in key or "\\" in key or not key.startswith("-")
                    or key in keys or not isinstance(row["cwd"], str) or not row["cwd"].startswith("/")):
                raise ValueError()
            keys.add(key)
        if any(Path(name).parts[1] not in keys for name in expected if name.startswith("projects/")):
            raise ValueError()
        for key in ("accountId", "profileId", "deployment", "createdAt"):
            if not isinstance(data[key], str) or not data[key]:
                raise ValueError()
        if not isinstance(data["context"], dict):
            raise ValueError()
        transcript_ids = {Path(name).stem for name in expected if name.startswith("projects/") and name.endswith(".jsonl")}
        for path in (root / "indexes").glob("local_*.json"):
            session = json.loads(path.read_text())
            if (not isinstance(session, dict) or session.get("cliSessionId") not in transcript_ids
                    or not isinstance(session.get("cwd"), str) or not session["cwd"].startswith("/")):
                raise ValueError()
        return data
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise SyncError("迁移包清单或文件校验失败。") from exc


class PathMapping:
    def __init__(self, source_home: str, target_home: str, overrides: dict[str, str] | None = None):
        self.rules = dict(overrides or {})
        self.rules.setdefault(source_home, target_home)
        for a, b in self.rules.items():
            if (not isinstance(a, str) or not isinstance(b, str) or not a.startswith("/") or not b.startswith("/")
                    or a == "/" or a.endswith("/") or b == "/" or "\x00" in a or "\x00" in b):
                raise SyncError("项目映射必须是完整的绝对路径。")

    def text(self, value: str) -> str:
        import re

        # Longest matches win in one pass; replacements never get mapped a second time.
        pattern = "|".join(re.escape(a.rstrip("/")) for a in sorted(self.rules, key=len, reverse=True))
        return re.sub(r"(?:" + pattern + r")(?=/|$|[\s\"'<>])",
                      lambda match: self.rules[match.group(0)].rstrip("/"), value)

    def value(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, dict):
            return {self.text(key): self.value(item) for key, item in value.items()}
        return value


def materialize_file(source: Path, target: Path, mapping: PathMapping, *, index: bool = False,
                     metadata_only: bool = False) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix == ".jsonl":
        with source.open("rb") as reader, target.open("wb") as writer:
            for line in reader:
                try:
                    data = json.loads(line)
                    if not isinstance(data, dict):
                        raise ValueError()
                    mapped = mapping.value(data) if metadata_only else dict(data)
                    for key in PATH_FIELDS.intersection(data):
                        mapped[key] = mapping.value(data[key])
                    if mapped != data:
                        line = (json.dumps(mapped, ensure_ascii=True) + "\n").encode()
                except (ValueError, UnicodeDecodeError):
                    pass
                writer.write(line)
    elif source.suffix == ".json":
        try:
            data = json.loads(source.read_text())
        except (ValueError, UnicodeDecodeError):
            shutil.copyfile(source, target)
        else:
            mapped = mapping.value(data)
            if index:
                for key in ("bridgeSessionIds", "remoteMcpServersConfig"):
                    if key in mapped:
                        mapped[key] = []
            target.write_text(json.dumps(mapped, ensure_ascii=True, indent=2) + "\n")
    elif source.suffix == ".md":
        target.write_text(mapping.text(source.read_text()))
    else:
        shutil.copyfile(source, target)
    shutil.copystat(source, target)
