from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SESSION_GLOB = "local_*.json"
NON_ACCOUNT_DIRS = {"skills-plugin"}


@dataclasses.dataclass(frozen=True)
class ProfileRef:
    account_id: str
    profile_id: str
    session_dir: Path
    local_agent_dir: Path | None

    @property
    def label(self) -> str:
        return f"{self.account_id}/{self.profile_id}"


@dataclasses.dataclass(frozen=True)
class ProfileSummary:
    ref: ProfileRef
    session_count: int
    newest_session_mtime: float | None
    local_agent_mtime: float | None
    is_current_account: bool
    is_previous_account: bool


@dataclasses.dataclass(frozen=True)
class FileChange:
    name: str
    kind: str
    source_size: int | None = None
    target_size: int | None = None


@dataclasses.dataclass(frozen=True)
class MirrorPlan:
    source: ProfileRef
    target: ProfileRef
    added: list[FileChange]
    updated: list[FileChange]
    deleted: list[FileChange]
    source_count: int
    target_count: int

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated or self.deleted)


class SyncError(RuntimeError):
    pass


def default_claude_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "Claude"


def default_projects_dir() -> Path:
    return Path.home() / ".claude" / "projects"


def safe_load_json(path: Path) -> Any | None:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def newest_mtime(paths: Iterable[Path]) -> float | None:
    newest: float | None = None
    for path in paths:
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            continue
        if newest is None or mtime > newest:
            newest = mtime
    return newest


def local_json_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(path for path in directory.glob(SESSION_GLOB) if path.is_file())


def count_local_json(directory: Path) -> int:
    return len(local_json_files(directory))


class ClaudeLayout:
    def __init__(self, claude_dir: Path, projects_dir: Path | None = None) -> None:
        self.claude_dir = claude_dir
        self.projects_dir = projects_dir or default_projects_dir()
        self.code_sessions_dir = claude_dir / "claude-code-sessions"
        self.local_agent_dir = claude_dir / "local-agent-mode-sessions"
        self.backup_root = Path.home() / ".claude" / "backups" / "claude-recent-sync"

    def active_account_id(self) -> str | None:
        data = safe_load_json(self.claude_dir / "cowork-enabled-cli-ops.json")
        if isinstance(data, dict):
            value = data.get("ownerAccountId")
            if isinstance(value, str) and value:
                return value
        return None

    def account_ids(self) -> list[str]:
        ids: set[str] = set()
        for root in (self.code_sessions_dir, self.local_agent_dir):
            if not root.exists():
                continue
            for child in root.iterdir():
                if child.is_dir() and not child.name.startswith(".") and child.name not in NON_ACCOUNT_DIRS:
                    ids.add(child.name)
        active = self.active_account_id()
        if active:
            ids.add(active)
        return sorted(ids)

    def profile_ids_for_account(self, account_id: str) -> list[str]:
        ids: set[str] = set()
        for root in (self.code_sessions_dir, self.local_agent_dir):
            acct_dir = root / account_id
            if not acct_dir.exists():
                continue
            for child in acct_dir.iterdir():
                if child.is_dir() and not child.name.startswith("."):
                    ids.add(child.name)
        return sorted(ids)

    def profile_ref(self, account_id: str, profile_id: str) -> ProfileRef:
        session_dir = self.code_sessions_dir / account_id / profile_id
        agent_dir = self.local_agent_dir / account_id / profile_id
        return ProfileRef(
            account_id=account_id,
            profile_id=profile_id,
            session_dir=session_dir,
            local_agent_dir=agent_dir if agent_dir.exists() else None,
        )

    def summarize_profiles(self) -> list[ProfileSummary]:
        active = self.active_account_id()
        previous = self.previous_account_id()
        summaries: list[ProfileSummary] = []
        for account_id in self.account_ids():
            for profile_id in self.profile_ids_for_account(account_id):
                ref = self.profile_ref(account_id, profile_id)
                files = local_json_files(ref.session_dir)
                agent_files = list(ref.local_agent_dir.rglob("*")) if ref.local_agent_dir else []
                agent_mtime = newest_mtime(path for path in agent_files if path.is_file())
                summaries.append(
                    ProfileSummary(
                        ref=ref,
                        session_count=len(files),
                        newest_session_mtime=newest_mtime(files),
                        local_agent_mtime=agent_mtime,
                        is_current_account=account_id == active,
                        is_previous_account=account_id == previous,
                    )
                )
        return sorted(
            summaries,
            key=lambda item: (
                not item.is_current_account,
                not item.is_previous_account,
                -(item.newest_session_mtime or 0),
                item.ref.account_id,
                item.ref.profile_id,
            ),
        )

    def account_newest_session_mtime(self, account_id: str) -> float | None:
        account_dir = self.code_sessions_dir / account_id
        if not account_dir.exists():
            return None
        return newest_mtime(account_dir.glob(f"*/{SESSION_GLOB}"))

    def previous_account_id(self) -> str | None:
        active = self.active_account_id()
        candidates: list[tuple[float, str]] = []
        for account_id in self.account_ids():
            if active and account_id == active:
                continue
            mtime = self.account_newest_session_mtime(account_id)
            if mtime is not None:
                candidates.append((mtime, account_id))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        return candidates[0][1]

    def resolve_account_alias(self, value: str) -> str:
        if value == "current":
            active = self.active_account_id()
            if not active:
                raise SyncError("Cannot resolve 'current': active Claude account was not found.")
            return active
        if value == "previous":
            previous = self.previous_account_id()
            if not previous:
                raise SyncError("Cannot resolve 'previous': no non-current account has session indexes.")
            return previous
        account_ids = self.account_ids()
        if value in account_ids:
            return value
        matches = [account_id for account_id in account_ids if account_id.startswith(value)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise SyncError(f"Account prefix {value!r} is ambiguous: {', '.join(matches)}")
        if value in account_ids or looks_like_uuid(value):
            return value
        raise SyncError(f"Unknown account {value!r}. Run 'claude-recent-sync list'.")

    def resolve_profile(self, account_value: str, profile_value: str | None = None) -> ProfileRef:
        account_id = self.resolve_account_alias(account_value)
        if profile_value:
            profile_ids = self.profile_ids_for_account(account_id)
            if profile_value in profile_ids:
                return self.profile_ref(account_id, profile_value)
            matches = [profile_id for profile_id in profile_ids if profile_id.startswith(profile_value)]
            if len(matches) == 1:
                profile_id = matches[0]
            elif len(matches) > 1:
                raise SyncError(f"Profile prefix {profile_value!r} is ambiguous: {', '.join(matches)}")
            else:
                profile_id = profile_value
            return self.profile_ref(account_id, profile_id)

        candidates = [self.profile_ref(account_id, profile_id) for profile_id in self.profile_ids_for_account(account_id)]
        if not candidates:
            raise SyncError(f"No profile directories found for account {account_id}.")

        # Pick the profile that Claude Desktop is most likely using for Code Recents.
        # Session files are the strongest signal. For a newly logged-in account with
        # no Code Recents yet, local-agent rpm activity is the next best signal.
        def score(ref: ProfileRef) -> tuple[int, float, str]:
            files = local_json_files(ref.session_dir)
            session_mtime = newest_mtime(files) or 0.0
            agent_mtime = 0.0
            if ref.local_agent_dir:
                agent_mtime = newest_mtime(path for path in ref.local_agent_dir.rglob("*") if path.is_file()) or 0.0
            has_sessions = 1 if files else 0
            return (has_sessions, max(session_mtime, agent_mtime), ref.profile_id)

        return sorted(candidates, key=score, reverse=True)[0]

    def build_transcript_index(self) -> dict[str, Path]:
        index: dict[str, Path] = {}
        if not self.projects_dir.exists():
            return index
        for path in self.projects_dir.rglob("*.jsonl"):
            index[path.stem] = path
        return index


def looks_like_uuid(value: str) -> bool:
    return len(value) == 36 and value.count("-") == 4


def compute_plan(source: ProfileRef, target: ProfileRef, delete: bool = True) -> MirrorPlan:
    source_files = {path.name: path for path in local_json_files(source.session_dir)}
    target_files = {path.name: path for path in local_json_files(target.session_dir)}

    added: list[FileChange] = []
    updated: list[FileChange] = []
    deleted: list[FileChange] = []

    for name, source_path in sorted(source_files.items()):
        target_path = target_files.get(name)
        if target_path is None:
            added.append(FileChange(name=name, kind="added", source_size=source_path.stat().st_size))
        elif file_sha256(source_path) != file_sha256(target_path):
            updated.append(
                FileChange(
                    name=name,
                    kind="updated",
                    source_size=source_path.stat().st_size,
                    target_size=target_path.stat().st_size,
                )
            )

    if delete:
        for name, target_path in sorted(target_files.items()):
            if name not in source_files:
                deleted.append(FileChange(name=name, kind="deleted", target_size=target_path.stat().st_size))

    return MirrorPlan(
        source=source,
        target=target,
        added=added,
        updated=updated,
        deleted=deleted,
        source_count=len(source_files),
        target_count=len(target_files),
    )


def create_backup(target: ProfileRef, backup_root: Path, label: str, plan: MirrorPlan) -> Path:
    backup_dir = backup_root / f"{utc_stamp()}-{label}"
    files_dir = backup_dir / "target-before-sync"
    files_dir.mkdir(parents=True, exist_ok=True)

    for path in local_json_files(target.session_dir):
        shutil.copy2(path, files_dir / path.name)

    manifest = {
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "source": dataclasses.asdict(plan.source),
        "target": dataclasses.asdict(plan.target),
        "sourceCount": plan.source_count,
        "targetCountBefore": plan.target_count,
        "added": [dataclasses.asdict(item) for item in plan.added],
        "updated": [dataclasses.asdict(item) for item in plan.updated],
        "deleted": [dataclasses.asdict(item) for item in plan.deleted],
    }
    # Convert Path objects from asdict output.
    def normalize(value: Any) -> Any:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value

    with (backup_dir / "manifest.json").open("w", encoding="utf-8") as fh:
        json.dump(normalize(manifest), fh, indent=2, sort_keys=True)
        fh.write("\n")
    return backup_dir


def mirror_sessions(
    layout: ClaudeLayout,
    source: ProfileRef,
    target: ProfileRef,
    *,
    dry_run: bool,
    delete: bool,
    allow_empty_source: bool,
    backup_root: Path | None = None,
) -> dict[str, Any]:
    plan = compute_plan(source, target, delete=delete)

    if plan.source_count == 0 and not allow_empty_source:
        raise SyncError(
            f"Refusing to mirror an empty source directory: {source.session_dir}. "
            "Pass --allow-empty-source only if you intentionally want to clear the target."
        )

    result: dict[str, Any] = plan_to_dict(plan)
    result["dryRun"] = dry_run

    if dry_run:
        return result

    target.session_dir.mkdir(parents=True, exist_ok=True)
    backup_dir = create_backup(target, backup_root or layout.backup_root, f"{source.account_id[:8]}-to-{target.account_id[:8]}", plan)
    result["backup"] = str(backup_dir)

    source_files = {path.name: path for path in local_json_files(source.session_dir)}
    target_files = {path.name: path for path in local_json_files(target.session_dir)}

    if delete:
        for name, target_path in target_files.items():
            if name not in source_files:
                target_path.unlink()

    for name, source_path in source_files.items():
        shutil.copy2(source_path, target.session_dir / name)

    verify_plan = compute_plan(source, target, delete=True)
    if verify_plan.changed or verify_plan.source_count != len(local_json_files(target.session_dir)):
        raise SyncError("Verification failed: target does not match source after mirror.")

    result["targetCountAfter"] = len(local_json_files(target.session_dir))
    result["overallSame"] = True
    return result


def plan_to_dict(plan: MirrorPlan) -> dict[str, Any]:
    return {
        "source": {
            "accountId": plan.source.account_id,
            "profileId": plan.source.profile_id,
            "sessionDir": str(plan.source.session_dir),
        },
        "target": {
            "accountId": plan.target.account_id,
            "profileId": plan.target.profile_id,
            "sessionDir": str(plan.target.session_dir),
        },
        "sourceCount": plan.source_count,
        "targetCountBefore": plan.target_count,
        "added": [dataclasses.asdict(item) for item in plan.added],
        "updated": [dataclasses.asdict(item) for item in plan.updated],
        "deleted": [dataclasses.asdict(item) for item in plan.deleted],
        "changed": plan.changed,
    }


def read_session_index(path: Path) -> dict[str, Any]:
    data = safe_load_json(path)
    if not isinstance(data, dict):
        return {}
    return data


def doctor_profile(layout: ClaudeLayout, profile: ProfileRef) -> dict[str, Any]:
    transcript_index = layout.build_transcript_index()
    missing: list[dict[str, str]] = []
    invalid: list[str] = []
    for path in local_json_files(profile.session_dir):
        data = read_session_index(path)
        if not data:
            invalid.append(path.name)
            continue
        cli_session_id = data.get("cliSessionId")
        if not isinstance(cli_session_id, str) or not cli_session_id:
            invalid.append(path.name)
            continue
        if cli_session_id not in transcript_index:
            missing.append({"index": path.name, "cliSessionId": cli_session_id})
    return {
        "profile": profile.label,
        "sessionDir": str(profile.session_dir),
        "indexCount": count_local_json(profile.session_dir),
        "invalidIndexes": invalid,
        "missingTranscripts": missing,
        "ok": not invalid and not missing,
    }


def format_mtime(value: float | None) -> str:
    if value is None:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))


def print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False))


def print_plan(result: dict[str, Any]) -> None:
    print(f"source={result['source']['accountId']}/{result['source']['profileId']}")
    print(f"target={result['target']['accountId']}/{result['target']['profileId']}")
    print(f"source_count={result['sourceCount']}")
    print(f"target_count_before={result['targetCountBefore']}")
    print(
        "added={added} updated={updated} deleted={deleted}".format(
            added=len(result["added"]),
            updated=len(result["updated"]),
            deleted=len(result["deleted"]),
        )
    )
    if result.get("dryRun"):
        print("dry_run=true")
    if "targetCountAfter" in result:
        print(f"target_count_after={result['targetCountAfter']}")
    if "backup" in result:
        print(f"backup={result['backup']}")
    if result.get("overallSame"):
        print("overall_same=true")


def command_list(args: argparse.Namespace) -> int:
    layout = ClaudeLayout(args.claude_dir, args.projects_dir)
    rows = layout.summarize_profiles()
    if args.json:
        print_json(
            {
                "activeAccountId": layout.active_account_id(),
                "previousAccountId": layout.previous_account_id(),
                "profiles": [
                    {
                        "accountId": row.ref.account_id,
                        "profileId": row.ref.profile_id,
                        "sessionDir": str(row.ref.session_dir),
                        "sessionCount": row.session_count,
                        "newestSessionMtime": row.newest_session_mtime,
                        "localAgentMtime": row.local_agent_mtime,
                        "isCurrentAccount": row.is_current_account,
                        "isPreviousAccount": row.is_previous_account,
                    }
                    for row in rows
                ],
            }
        )
        return 0

    print(f"Claude dir: {layout.claude_dir}")
    print(f"Current account: {layout.active_account_id() or '-'}")
    print(f"Previous account: {layout.previous_account_id() or '-'}")
    print()
    print(f"{'role':<18} {'sessions':>8} {'newest session':<20} {'account/profile'}")
    for row in rows:
        roles: list[str] = []
        if row.is_current_account:
            roles.append("current")
        if row.is_previous_account:
            roles.append("previous")
        role = ",".join(roles) or "-"
        print(f"{role:<18} {row.session_count:>8} {format_mtime(row.newest_session_mtime):<20} {row.ref.label}")
    return 0


def command_diff(args: argparse.Namespace) -> int:
    layout = ClaudeLayout(args.claude_dir, args.projects_dir)
    source = layout.resolve_profile(args.source, args.source_profile)
    target = layout.resolve_profile(args.target, args.target_profile)
    plan = compute_plan(source, target, delete=not args.no_delete)
    data = plan_to_dict(plan)
    if args.json:
        print_json(data)
    else:
        print_plan(data | {"dryRun": True})
        for key in ("added", "updated", "deleted"):
            values = data[key]
            if values:
                print(f"\n{key}:")
                for item in values:
                    print(f"  {item['name']}")
    return 1 if plan.changed and args.fail_on_change else 0


def command_mirror(args: argparse.Namespace) -> int:
    layout = ClaudeLayout(args.claude_dir, args.projects_dir)
    source = layout.resolve_profile(args.source, args.source_profile)
    target = layout.resolve_profile(args.target, args.target_profile)
    result = mirror_sessions(
        layout,
        source,
        target,
        dry_run=args.dry_run,
        delete=not args.no_delete,
        allow_empty_source=args.allow_empty_source,
        backup_root=args.backup_root,
    )
    if args.json:
        print_json(result)
    else:
        print_plan(result)
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    layout = ClaudeLayout(args.claude_dir, args.projects_dir)
    profile = layout.resolve_profile(args.account, args.profile)
    result = doctor_profile(layout, profile)
    if args.json:
        print_json(result)
    else:
        print(f"profile={result['profile']}")
        print(f"session_dir={result['sessionDir']}")
        print(f"index_count={result['indexCount']}")
        print(f"invalid_indexes={len(result['invalidIndexes'])}")
        print(f"missing_transcripts={len(result['missingTranscripts'])}")
        print(f"ok={str(result['ok']).lower()}")
        if result["invalidIndexes"]:
            print("\ninvalid:")
            for name in result["invalidIndexes"]:
                print(f"  {name}")
        if result["missingTranscripts"]:
            print("\nmissing transcripts:")
            for item in result["missingTranscripts"]:
                print(f"  {item['index']} -> {item['cliSessionId']}")
    return 0 if result["ok"] else 1


def command_watch(args: argparse.Namespace) -> int:
    layout = ClaudeLayout(args.claude_dir, args.projects_dir)
    source = layout.resolve_profile(args.source, args.source_profile)
    target = layout.resolve_profile(args.target, args.target_profile)
    print(f"watching source={source.label} target={target.label} interval={args.interval}s")
    last_signature: str | None = None
    while True:
        signature = directory_signature(source.session_dir)
        if signature != last_signature:
            result = mirror_sessions(
                layout,
                source,
                target,
                dry_run=False,
                delete=not args.no_delete,
                allow_empty_source=args.allow_empty_source,
                backup_root=args.backup_root,
            )
            print(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                f"added={len(result['added'])} updated={len(result['updated'])} deleted={len(result['deleted'])} "
                f"count={result.get('targetCountAfter', result['targetCountBefore'])}"
            )
            last_signature = directory_signature(source.session_dir)
        time.sleep(args.interval)


def command_ui(args: argparse.Namespace) -> int:
    from .server import run_ui

    return run_ui(
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
        claude_dir=args.claude_dir,
        projects_dir=args.projects_dir,
    )


def directory_signature(directory: Path) -> str:
    h = hashlib.sha256()
    for path in local_json_files(directory):
        stat = path.stat()
        h.update(path.name.encode("utf-8"))
        h.update(str(stat.st_mtime_ns).encode("ascii"))
        h.update(str(stat.st_size).encode("ascii"))
    return h.hexdigest()


def add_common_path_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--claude-dir",
        type=Path,
        default=default_claude_dir(),
        help="Claude Desktop application support directory.",
    )
    parser.add_argument(
        "--projects-dir",
        type=Path,
        default=default_projects_dir(),
        help="Claude Code transcript projects directory.",
    )


def add_source_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--from", dest="source", default="previous", help="Source account id or alias: current, previous.")
    parser.add_argument("--to", dest="target", default="current", help="Target account id or alias: current, previous.")
    parser.add_argument("--source-profile", help="Source profile/org id or unique prefix.")
    parser.add_argument("--target-profile", help="Target profile/org id or unique prefix.")
    parser.add_argument("--no-delete", action="store_true", help="Do not delete target local_*.json files missing from source.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-recent-sync",
        description="Mirror Claude Desktop Code Recents between local Claude accounts.",
    )
    parser.add_argument("--version", action="version", version="claude-recent-sync 0.3.0")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List detected Claude accounts and profile directories.")
    add_common_path_args(list_parser)
    list_parser.add_argument("--json", action="store_true")
    list_parser.set_defaults(func=command_list)

    diff_parser = subparsers.add_parser("diff", help="Show session-index differences between two accounts.")
    add_common_path_args(diff_parser)
    add_source_target_args(diff_parser)
    diff_parser.add_argument("--json", action="store_true")
    diff_parser.add_argument("--fail-on-change", action="store_true", help="Exit 1 when any difference is found.")
    diff_parser.set_defaults(func=command_diff)

    mirror_parser = subparsers.add_parser("mirror", help="Mirror source local_*.json indexes into target.")
    add_common_path_args(mirror_parser)
    add_source_target_args(mirror_parser)
    mirror_parser.add_argument("--dry-run", action="store_true", help="Print planned changes without writing.")
    mirror_parser.add_argument("--allow-empty-source", action="store_true", help="Allow an empty source to clear the target.")
    mirror_parser.add_argument("--backup-root", type=Path, help="Backup directory root.")
    mirror_parser.add_argument("--json", action="store_true")
    mirror_parser.set_defaults(func=command_mirror)

    doctor_parser = subparsers.add_parser("doctor", help="Validate that session indexes point to local transcript jsonl files.")
    add_common_path_args(doctor_parser)
    doctor_parser.add_argument("account", nargs="?", default="current", help="Account id or alias to inspect.")
    doctor_parser.add_argument("--profile", help="Profile/org id or unique prefix.")
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.set_defaults(func=command_doctor)

    watch_parser = subparsers.add_parser("watch", help="Poll source and mirror changes into target.")
    add_common_path_args(watch_parser)
    add_source_target_args(watch_parser)
    watch_parser.add_argument("--interval", type=float, default=10.0, help="Polling interval in seconds.")
    watch_parser.add_argument("--allow-empty-source", action="store_true")
    watch_parser.add_argument("--backup-root", type=Path)
    watch_parser.set_defaults(func=command_watch)

    ui_parser = subparsers.add_parser("ui", help="Launch the local visual interface.")
    add_common_path_args(ui_parser)
    ui_parser.add_argument("--host", default="127.0.0.1")
    ui_parser.add_argument("--port", type=int, default=47631)
    ui_parser.add_argument("--no-browser", action="store_true")
    ui_parser.set_defaults(func=command_ui)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
