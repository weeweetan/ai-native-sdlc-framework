#!/usr/bin/env python3
"""AIN-Loop: deterministic orchestration for an AI-native delivery loop.

The runtime deliberately has no third-party dependencies. Models generate and
reason about artifacts; this program validates state, permissions and gates.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


SCRIPT_PATH = Path(__file__).resolve()
if SCRIPT_PATH.parent.name == "scripts":
    DIST_ROOT = SCRIPT_PATH.parent.parent
elif SCRIPT_PATH.parent.name == "runtime":
    DIST_ROOT = SCRIPT_PATH.parent.parent
else:
    DIST_ROOT = SCRIPT_PATH.parent

LEVEL_VALUE = {"R0": 0, "R1": 1, "R2": 2, "R3": 3}
CHANGE_ID_RE = re.compile(r"^CHG-\d{8}-\d{3,}$")
DEFAULT_MAX_LOG_BYTES = 5 * 1024 * 1024
DEFAULT_COMMAND_TIMEOUT_SECONDS = 15 * 60


class AinError(Exception):
    pass


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AinError(f"缺少文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise AinError(f"JSON 格式错误：{path}:{exc.lineno}: {exc.msg}") from exc


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def git_result(target: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(target), *args],
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AinError("需要 Git，但当前环境找不到 git 命令") from exc


def git_output(target: Path, *args: str) -> str:
    result = git_result(target, *args)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "未知 Git 错误"
        raise AinError(f"Git 命令失败（{' '.join(args)}）：{detail}")
    return result.stdout.strip()


def git_head(target: Path, required: bool = False) -> Optional[str]:
    result = git_result(target, "rev-parse", "--verify", "HEAD^{commit}")
    if result.returncode == 0:
        return result.stdout.strip()
    if required:
        detail = result.stderr.strip() or "仓库没有可引用的提交"
        raise AinError(f"需要可引用的 Git HEAD：{detail}")
    return None


def git_commit_exists(target: Path, commit: str) -> bool:
    if not commit:
        return False
    return git_result(target, "rev-parse", "--verify", f"{commit}^{{commit}}").returncode == 0


def git_is_clean(target: Path) -> bool:
    return not git_output(target, "status", "--porcelain")


def config_path(target: Path) -> Path:
    installed = target / ".ain" / "config.json"
    return installed if installed.exists() else resource("config")


def resource(name: str) -> Path:
    candidates = {
        "config": [DIST_ROOT / "config" / "framework.json", DIST_ROOT / "config.json"],
        "templates": [DIST_ROOT / "templates"],
        "prompts": [DIST_ROOT / "prompts"],
        "github": [DIST_ROOT / "integrations" / "github" / "ain-gate.yml"],
    }[name]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise AinError(f"安装包缺少资源 {name}，检查 {DIST_ROOT}")


def target_config(target: Path) -> dict[str, Any]:
    return read_json(config_path(target))


def schema_version(config: dict[str, Any]) -> int:
    try:
        return int(config.get("schema_version", 1))
    except (TypeError, ValueError):
        return 1


def governance_config(config: dict[str, Any]) -> dict[str, Any]:
    value = config.get("governance", {})
    return value if isinstance(value, dict) else {}


def strict_governance(config: dict[str, Any]) -> bool:
    return schema_version(config) >= 2 and bool(governance_config(config).get("require_bound_approvals", False))


def stage_approval_binding(stage: dict[str, Any]) -> str:
    return str(stage.get("approval_binding", "none"))


def is_bound_stage(config: dict[str, Any], stage: dict[str, Any]) -> bool:
    return strict_governance(config) and stage_approval_binding(stage) != "none"


def metadata_is_true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def is_git_sha(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{7,64}", value.strip()) is not None


def csv_metadata(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def normalize_repo_path(value: str) -> str:
    path = value.strip().replace("\\", "/")
    if not path or path.startswith("/") or path.startswith("../") or "/../" in path or path == "..":
        raise AinError(f"不是仓库内相对路径：{value}")
    return path[2:] if path.startswith("./") else path


def matches_path_pattern(path: str, pattern: str) -> bool:
    normalized_path = normalize_repo_path(path)
    normalized_pattern = normalize_repo_path(pattern)
    if fnmatch.fnmatchcase(normalized_path, normalized_pattern):
        return True
    if normalized_pattern.endswith("/**"):
        prefix = normalized_pattern[:-3].rstrip("/")
        return normalized_path == prefix or normalized_path.startswith(prefix + "/")
    return False


def planned_paths(metadata: dict[str, Any]) -> list[str]:
    return csv_metadata(metadata.get("planned_paths", ""))


def artifact_path(target: Path, change_id: str, stage: dict[str, Any]) -> Path:
    return change_dir(target, change_id) / stage["artifact"]


def evidence_dir(target: Path, change_id: str) -> Path:
    return change_dir(target, change_id) / "evidence"


def evidence_manifest_path(target: Path, change_id: str) -> Path:
    return evidence_dir(target, change_id) / "evidence.jsonl"


def record_hash(record: dict[str, Any]) -> str:
    payload = dict(record)
    payload.pop("record_hash", None)
    return sha256_bytes(canonical_json(payload))


def read_evidence(target: Path, change_id: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Read and integrity-check local verification evidence.

    This is an integrity aid, not a substitute for a remotely authenticated
    attestation: someone with repository write access can still rewrite both
    the chain and its local metadata.
    """
    manifest = evidence_manifest_path(target, change_id)
    if not manifest.exists():
        return [f"缺少证据清单：{manifest.relative_to(target)}"], []
    errors: list[str] = []
    records: list[dict[str, Any]] = []
    previous_hash: Optional[str] = None
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return [f"证据清单不是 UTF-8 文本：{manifest.relative_to(target)}"], []
    if not lines:
        return ["证据清单为空"], []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            errors.append(f"证据清单第 {number} 行为空")
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"证据清单第 {number} 行 JSON 错误：{exc.msg}")
            continue
        if not isinstance(record, dict):
            errors.append(f"证据清单第 {number} 行不是对象")
            continue
        expected_hash = record_hash(record)
        if record.get("record_hash") != expected_hash:
            errors.append(f"证据清单第 {number} 行哈希不匹配")
        if record.get("previous_hash") != previous_hash:
            errors.append(f"证据清单第 {number} 行链路不连续")
        log_path_value = record.get("log_path")
        if not isinstance(log_path_value, str):
            errors.append(f"证据清单第 {number} 行缺少 log_path")
        else:
            log_path = evidence_dir(target, change_id) / log_path_value
            if not path_is_within(log_path, evidence_dir(target, change_id)):
                errors.append(f"证据清单第 {number} 行日志路径越界")
            elif not log_path.exists():
                errors.append(f"证据清单第 {number} 行日志不存在：{log_path_value}")
            else:
                if record.get("log_sha256") != sha256_file(log_path):
                    errors.append(f"证据清单第 {number} 行日志哈希不匹配")
                if record.get("log_bytes") != log_path.stat().st_size:
                    errors.append(f"证据清单第 {number} 行日志长度不匹配")
        subject = record.get("subject_commit")
        if not is_git_sha(subject):
            errors.append(f"证据清单第 {number} 行 subject_commit 无效")
        elif not git_commit_exists(target, str(subject)):
            errors.append(f"证据清单第 {number} 行引用的提交不存在：{subject}")
        records.append(record)
        previous_hash = record.get("record_hash") if isinstance(record.get("record_hash"), str) else None
    return errors, records


def successful_evidence(records: list[dict[str, Any]], subject_commit: str) -> bool:
    return any(
        record.get("subject_commit") == subject_commit
        and record.get("exit_code") == record.get("expected_exit")
        and not record.get("timed_out", False)
        for record in records
    )


def change_dir(target: Path, change_id: str) -> Path:
    return target / "ai" / "changes" / change_id


def state_path(target: Path, change_id: str) -> Path:
    return change_dir(target, change_id) / "state.json"


def load_state(target: Path, change_id: str) -> dict[str, Any]:
    return read_json(state_path(target, change_id))


def save_state(target: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = now()
    write_json(state_path(target, state["change_id"]), state)


def audit(target: Path, state: dict[str, Any], event: str, **details: Any) -> None:
    record = {"at": now(), "change_id": state["change_id"], "event": event, **details}
    state.setdefault("events", []).append(record)
    log = target / ".ain" / "audit.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if value in {"[]", "{}"}:
        return [] if value == "[]" else {}
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    if "#" in value:
        value = value.split("#", 1)[0].rstrip()
    return value


def parse_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    result: dict[str, Any] = {}
    for line in text[4:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#") or line.startswith((" ", "\t")):
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = scalar(value)
    return result


def sections(text: str) -> dict[str, str]:
    matches = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", text))
    result: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        result[match.group(1).strip()] = text[match.end():end]
    return result


def substantive(value: str) -> bool:
    value = re.sub(r"<!--.*?-->", "", value, flags=re.S)
    lines = []
    table_header_seen = False
    for raw in value.splitlines():
        line = raw.strip()
        if not line or line.startswith((">", "| ---", "```")):
            continue
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if cells and all(re.fullmatch(r":?-+:?", cell.replace(" ", "")) for cell in cells):
                continue
            if not table_header_seen:
                table_header_seen = True
                continue
            if any(cells):
                lines.append(" ".join(cells))
            continue
        if re.fullmatch(r"[-*]\s*(\[[ xX]\])?\s*", line):
            continue
        if re.fullmatch(r"[-*]\s+[^:：]{0,30}[:：]\s*", line):
            continue
        if line.startswith("|") and set(line.replace("|", "").replace(" ", "")) <= {"-", ":"}:
            continue
        lines.append(line)
    return len(" ".join(lines)) >= 8


def stage_config(config: dict[str, Any], stage_id: str) -> dict[str, Any]:
    for item in config["stages"]:
        if item["id"] == stage_id:
            return item
    raise AinError(f"未知阶段：{stage_id}")


def validate_artifact(target: Path, path: Path, stage: dict[str, Any], config: dict[str, Any], change_id: str) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        return [f"缺少 {stage['artifact']}"]
    text = path.read_text(encoding="utf-8")
    metadata = parse_frontmatter(text)
    if not metadata:
        errors.append("缺少有效的 YAML frontmatter")
    for key in stage.get("required_metadata", []):
        if key not in metadata or metadata[key] in {"", None}:
            errors.append(f"元数据 {key} 未填写")
    if metadata.get("change_id") and metadata["change_id"] != change_id:
        errors.append(f"change_id 应为 {change_id}，实际为 {metadata['change_id']}")
    for token in config.get("validation", {}).get("placeholder_tokens", []):
        if token in text:
            errors.append(f"仍含模板占位符：{token}")
    found_sections = sections(text)
    for aliases in stage.get("required_sections", []):
        match = next((body for heading, body in found_sections.items() if any(alias.lower() in heading.lower() for alias in aliases)), None)
        if match is None:
            errors.append(f"缺少章节：{' / '.join(aliases)}")
        elif not substantive(match):
            errors.append(f"章节内容不足：{' / '.join(aliases)}")
    passing = config.get("validation", {}).get("passing_values", {})
    pass_key = f"{stage['id']}.result" if stage["id"] != "release" else "release.decision"
    if pass_key in passing and str(metadata.get(pass_key.split(".")[-1], "")).lower() not in passing[pass_key]:
        errors.append(f"{pass_key.split('.')[-1]} 尚未达到通过状态")
    if stage["id"] == "plan" and strict_governance(config):
        patterns = planned_paths(metadata)
        if not patterns:
            errors.append("元数据 planned_paths 未填写；必须列出允许修改的仓库路径模式")
        for pattern in patterns:
            try:
                normalized = normalize_repo_path(pattern)
            except AinError as exc:
                errors.append(str(exc))
                continue
            if normalized in {"*", "**", "./**"}:
                errors.append("planned_paths 不能覆盖整个仓库；请列出有意修改的边界")
    if stage["id"] == "verification":
        if not metadata_is_true(metadata.get("ready_for_review")):
            errors.append("缺少 ready_for_review: true 完成声明")
        if strict_governance(config):
            result = str(metadata.get("result", "")).lower()
            commit = str(metadata.get("commit_sha", ""))
            if not is_git_sha(commit):
                errors.append("commit_sha 必须是被验证的 Git 提交 SHA")
            elif not git_commit_exists(target, commit):
                errors.append(f"commit_sha 指向的提交不存在：{commit}")
            if result == "pass":
                if metadata_is_true(metadata.get("release_blocked")):
                    errors.append("result=pass 时 release_blocked 必须为 false")
                manifest_value = metadata.get("evidence_manifest")
                if not isinstance(manifest_value, str) or not manifest_value.strip():
                    errors.append("result=pass 必须填写 evidence_manifest")
                else:
                    try:
                        normalized_manifest = normalize_repo_path(manifest_value)
                    except AinError as exc:
                        errors.append(str(exc))
                        normalized_manifest = ""
                    if normalized_manifest and normalized_manifest != "evidence/evidence.jsonl":
                        errors.append("evidence_manifest 必须引用 evidence/evidence.jsonl")
                evidence_errors, records = read_evidence(target, change_id)
                errors.extend(evidence_errors)
                require_success = verification_controls(config).get("require_successful_evidence_for_pass", True)
                if require_success and is_git_sha(commit) and not evidence_errors and not successful_evidence(records, commit):
                    errors.append("没有与 commit_sha 匹配的成功命令证据")
            elif result == "partial":
                if not metadata_is_true(metadata.get("release_blocked")):
                    errors.append("result=partial 必须声明 release_blocked: true")
                manifest_value = metadata.get("evidence_manifest")
                if isinstance(manifest_value, str) and manifest_value.strip():
                    try:
                        normalized_manifest = normalize_repo_path(manifest_value)
                    except AinError as exc:
                        errors.append(str(exc))
                        normalized_manifest = ""
                    if normalized_manifest == "evidence/evidence.jsonl":
                        evidence_errors, _ = read_evidence(target, change_id)
                        errors.extend(evidence_errors)
    if stage["id"] == "review" and strict_governance(config):
        verification = change_dir(target, change_id) / "verification.md"
        if verification.exists():
            verification_metadata = parse_frontmatter(verification.read_text(encoding="utf-8"))
            if metadata.get("commit_sha") != verification_metadata.get("commit_sha"):
                errors.append("review.commit_sha 必须与 verification.commit_sha 一致")
    if stage["id"] == "release" and strict_governance(config):
        verification = change_dir(target, change_id) / "verification.md"
        if not verification.exists():
            errors.append("发布前缺少 verification.md")
        else:
            verification_metadata = parse_frontmatter(verification.read_text(encoding="utf-8"))
            if str(verification_metadata.get("result", "")).lower() != "pass":
                errors.append("verification.result 不是 pass；partial 只能进入审查，不能发布")
            if metadata_is_true(verification_metadata.get("release_blocked")):
                errors.append("verification.release_blocked=true，不能发布")
            if metadata.get("commit_sha") != verification_metadata.get("commit_sha"):
                errors.append("release.commit_sha 必须与 verification.commit_sha 一致")
    return errors


def infer_risk(target: Path, change_id: str, config: dict[str, Any], paths: Optional[list[str]] = None) -> tuple[str, list[dict[str, Any]]]:
    folder = change_dir(target, change_id)
    combined = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in folder.glob("*.md")).lower()
    candidate_paths = [p.lower() for p in (paths or [])]
    level = "R0"
    matches: list[dict[str, Any]] = []
    for rule in config["risk"]["rules"]:
        keywords = [p for p in rule.get("patterns", []) if p.lower() in combined]
        path_hits = [p for p in rule.get("path_patterns", []) if any(p.lower() in candidate for candidate in candidate_paths)]
        if keywords or path_hits:
            matches.append({"rule": rule["id"], "level": rule["level"], "keywords": keywords, "paths": path_hits})
            if LEVEL_VALUE[rule["level"]] > LEVEL_VALUE[level]:
                level = rule["level"]
    return level, matches


def effective_risk(state: dict[str, Any], inferred: str) -> str:
    declared = state.get("declared_risk", "R0")
    return inferred if LEVEL_VALUE[inferred] > LEVEL_VALUE.get(declared, 0) else declared


def required_roles(config: dict[str, Any], stage_id: str, risk: str) -> list[str]:
    base = list(stage_config(config, stage_id).get("approval_roles", []))
    extra = config["risk"].get("additional_approvals", {}).get(risk, {}).get(stage_id, [])
    return list(dict.fromkeys(base + extra))


def approval_current_reason(
    target: Path,
    config: dict[str, Any],
    stage: dict[str, Any],
    path: Path,
    approval: dict[str, Any],
) -> Optional[str]:
    if approval.get("decision") != "approved":
        return "决策不是 approved"
    if not is_bound_stage(config, stage):
        return None
    if not path.exists():
        return "对应工件不存在"
    if not isinstance(approval.get("artifact_sha256"), str):
        return "旧批准未绑定工件内容；升级严格模式后需要重新批准"
    if approval.get("artifact_sha256") != sha256_file(path):
        return "工件内容已变化"
    if not isinstance(approval.get("policy_sha256"), str):
        return "旧批准未绑定策略配置；升级严格模式后需要重新批准"
    if approval.get("policy_sha256") != sha256_file(config_path(target)):
        return "框架策略配置已变化"
    if stage_approval_binding(stage) == "artifact_and_subject_commit":
        metadata = parse_frontmatter(path.read_text(encoding="utf-8"))
        subject = metadata.get("commit_sha")
        if approval.get("subject_commit") != subject:
            return "被验证/审查的提交已变化"
        if not is_git_sha(subject):
            return "工件中的 commit_sha 无效"
        if not git_commit_exists(target, str(subject)):
            return "工件引用的提交不存在"
    return None


def current_approval_roles(
    target: Path,
    state: dict[str, Any],
    config: dict[str, Any],
    stage: dict[str, Any],
    path: Path,
) -> tuple[set[str], list[dict[str, str]]]:
    current: set[str] = set()
    stale: list[dict[str, str]] = []
    for approval in state.get("approvals", []):
        if approval.get("stage") != stage["id"] or approval.get("decision") != "approved":
            continue
        try:
            reason = approval_current_reason(target, config, stage, path, approval)
        except AinError as exc:
            reason = str(exc)
        if reason is None:
            current.add(str(approval.get("role", "")))
        else:
            stale.append({"role": str(approval.get("role", "")), "reason": reason})
    return current, stale


def stage_evaluation(target: Path, state: dict[str, Any], config: dict[str, Any], item: dict[str, Any], risk: str) -> dict[str, Any]:
    path = artifact_path(target, state["change_id"], item)
    exists = path.exists()
    errors = validate_artifact(target, path, item, config, state["change_id"]) if exists else []
    required = required_roles(config, item["id"], risk)
    approved, stale = current_approval_roles(target, state, config, item, path)
    missing = [role for role in required if role not in approved]
    complete = exists and not errors and not missing
    return {
        "stage": item["id"],
        "artifact": item["artifact"],
        "exists": exists,
        "errors": errors,
        "required_roles": required,
        "approved_roles": sorted(approved),
        "missing_roles": missing,
        "stale_approvals": stale,
        "complete": complete,
    }


def all_evaluations(target: Path, state: dict[str, Any], config: dict[str, Any], paths: Optional[list[str]] = None) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    inferred, matches = infer_risk(target, state["change_id"], config, paths)
    risk = effective_risk(state, inferred)
    evaluations = [stage_evaluation(target, state, config, item, risk) for item in config["stages"]]
    return risk, matches, evaluations


def next_action(evaluations: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    for item in evaluations:
        if not item["exists"]:
            return {"kind": "create", **item}
        if item["errors"]:
            return {"kind": "repair", **item}
        if item["missing_roles"]:
            return {"kind": "approve", **item}
    return None


def derived_status(evaluations: list[dict[str, Any]]) -> str:
    action = next_action(evaluations)
    if action is None:
        return "loop_complete"
    if action["kind"] == "approve":
        return f"awaiting_{action['stage']}_approval"
    if action["kind"] == "repair":
        return f"repairing_{action['stage']}"
    return f"ready_for_{action['stage']}"


def copy_if_absent(source: Path, destination: Path, force: bool = False) -> str:
    if destination.exists() and not force:
        return "kept"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return "written"


def cmd_init(args: argparse.Namespace) -> int:
    target = args.target
    ain = target / ".ain"
    for directory in ["ai/changes", "ai/evals", "ai/incidents", "ai/policies", "ai/metrics", ".ain/runtime", ".ain/templates", ".ain/prompts"]:
        (target / directory).mkdir(parents=True, exist_ok=True)
    copy_if_absent(resource("config"), ain / "config.json", args.force)
    for source in resource("templates").glob("*.md"):
        copy_if_absent(source, ain / "templates" / source.name, args.force)
    for source in resource("prompts").glob("*.md"):
        copy_if_absent(source, ain / "prompts" / source.name, args.force)
    copy_if_absent(SCRIPT_PATH, ain / "runtime" / "ain.py", args.force)
    copy_if_absent(resource("templates") / "AGENTS.md", target / "AGENTS.md", args.force)
    copy_if_absent(resource("templates") / "REVIEW.md", target / "REVIEW.md", args.force)
    wrapper = ain / "ain"
    if not wrapper.exists() or args.force:
        wrapper.write_text('#!/bin/sh\nexec python3 "$(dirname "$0")/runtime/ain.py" --target "${AIN_TARGET:-.}" "$@"\n', encoding="utf-8")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if args.with_github:
        copy_if_absent(resource("github"), target / ".github" / "workflows" / "ain-gate.yml", args.force)
    print(f"已初始化 AIN-Loop：{target}")
    print("下一步：./.ain/ain new --title \"你的第一个变更\" --owner <负责人>")
    return 0


def next_id(target: Path) -> str:
    day = datetime.now().strftime("%Y%m%d")
    prefix = f"CHG-{day}-"
    used = []
    root = target / "ai" / "changes"
    if root.exists():
        for item in root.iterdir():
            if item.name.startswith(prefix) and item.name[len(prefix):].isdigit():
                used.append(int(item.name[len(prefix):]))
    return f"{prefix}{max(used, default=0) + 1:03d}"


def render_template(path: Path, replacements: dict[str, str]) -> str:
    text = path.read_text(encoding="utf-8")
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def cmd_new(args: argparse.Namespace) -> int:
    target = args.target
    if not (target / ".ain" / "config.json").exists():
        raise AinError("目标仓库尚未初始化；先运行 init")
    change_id = args.id or next_id(target)
    folder = change_dir(target, change_id)
    if folder.exists():
        raise AinError(f"变更已存在：{change_id}")
    folder.mkdir(parents=True)
    timestamp = now()
    template = target / ".ain" / "templates" / "intent.md"
    content = render_template(template, {
        "CHG-YYYY-NNN": change_id,
        "<一句话描述期望改变>": args.title,
        '"姓名 / Agent 身份"': f'"{args.owner}"',
        '"产品负责人"': f'"{args.owner}"',
        "YYYY-MM-DDTHH:MM:SSZ": timestamp,
        "source: customer_feedback": f"source: {args.source}",
        "risk_level: R0": f"risk_level: {args.risk}",
    })
    (folder / "intent.md").write_text(content, encoding="utf-8")
    state = {
        "schema_version": 1,
        "change_id": change_id,
        "title": args.title,
        "owner": args.owner,
        "source": args.source,
        "declared_risk": args.risk,
        "status": "draft",
        "created_at": timestamp,
        "updated_at": timestamp,
        "approvals": [],
        "events": [],
    }
    audit(target, state, "change_created", actor=args.owner, declared_risk=args.risk)
    save_state(target, state)
    print(change_id)
    print(f"已创建：{folder / 'intent.md'}")
    print(f"下一步：补全 intent.md，然后运行 ./.ain/ain validate {change_id}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    target = args.target
    config = target_config(target)
    ids = [args.change_id] if args.change_id else sorted(p.name for p in (target / "ai" / "changes").glob("CHG-*") if p.is_dir())
    if not ids:
        raise AinError("没有找到变更")
    failed = False
    for change_id in ids:
        state = load_state(target, change_id)
        _, _, evaluations = all_evaluations(target, state, config)
        existing = [item for item in evaluations if item["exists"]]
        if not existing:
            print(f"✗ {change_id}: 没有工件")
            failed = True
            continue
        for item in existing:
            if item["errors"]:
                failed = True
                print(f"✗ {change_id}/{item['artifact']}")
                for error in item["errors"]:
                    print(f"  - {error}")
            else:
                print(f"✓ {change_id}/{item['artifact']}")
    return 1 if failed else 0


def cmd_status(args: argparse.Namespace) -> int:
    target = args.target
    config = target_config(target)
    state = load_state(target, args.change_id)
    paths = args.paths.split(",") if args.paths else []
    risk, matches, evaluations = all_evaluations(target, state, config, paths)
    action = next_action(evaluations)
    if args.json:
        print(json.dumps({"change": state, "effective_risk": risk, "risk_matches": matches, "stages": evaluations, "next": action}, ensure_ascii=False, indent=2))
        return 0
    print(f"{state['change_id']}  {state['title']}  风险={risk}")
    for item in evaluations:
        if item["complete"]:
            marker, detail = "✓", "完成"
        elif not item["exists"]:
            marker, detail = "·", "待创建"
        elif item["errors"]:
            marker, detail = "!", f"{len(item['errors'])} 个校验问题"
        else:
            stale = item.get("stale_approvals", [])
            if stale:
                marker = "○"
                detail = "待重新批准：" + ", ".join(stale_item["role"] for stale_item in stale)
            else:
                marker, detail = "○", "待审批：" + ", ".join(item["missing_roles"])
        print(f"{marker} {item['stage']:<12} {item['artifact']:<18} {detail}")
    if matches:
        print("风险命中：" + ", ".join(f"{m['rule']}→{m['level']}" for m in matches))
    if action:
        print(f"下一步：{action['kind']} {action['artifact']}")
    else:
        print("下一步：闭环完成，进入发布后观测")
    return 0


def prompt_text(target: Path, action: dict[str, Any], state: dict[str, Any], risk: str) -> str:
    prompt_path = target / ".ain" / "prompts" / f"{action['stage']}.md"
    base = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else "处理 {{ARTIFACT}}。"
    replacements = {
        "{{CHANGE_ID}}": state["change_id"],
        "{{TITLE}}": state["title"],
        "{{RISK_LEVEL}}": risk,
        "{{ARTIFACT}}": action["artifact"],
    }
    for old, new in replacements.items():
        base = base.replace(old, str(new))
    if action["kind"] == "repair":
        base += "\n\n必须修复的校验问题：\n" + "\n".join(f"- {item}" for item in action["errors"])
    return base


def cmd_next(args: argparse.Namespace) -> int:
    target = args.target
    config = target_config(target)
    state = load_state(target, args.change_id)
    risk, _, evaluations = all_evaluations(target, state, config)
    action = next_action(evaluations)
    if action is None:
        print("所有阶段已完成。请持续观测控制带，并把异常写回新 intent。")
    elif action["kind"] == "approve":
        print(f"{action['artifact']} 已通过内容校验，等待角色批准：{', '.join(action['missing_roles'])}")
        print(f"命令：./.ain/ain approve {state['change_id']} --stage {action['stage']} --role <角色> --by <姓名>")
    else:
        if args.prepare:
            if action["kind"] != "create":
                raise AinError(f"{action['artifact']} 已存在，--prepare 不会覆盖；请按提示修复")
            source = target / ".ain" / "templates" / action["artifact"]
            destination = change_dir(target, state["change_id"]) / action["artifact"]
            content = render_template(source, {
                "CHG-YYYY-NNN": state["change_id"],
                "<变更名称>": state["title"],
                "YYYY-MM-DDTHH:MM:SSZ": now(),
                "risk_level: R1": f"risk_level: {risk}",
                'owner: "产品负责人"': f'owner: "{state["owner"]}"',
                'engineer_owner: "姓名"': 'engineer_owner: "待填写"',
            })
            destination.write_text(content, encoding="utf-8")
            audit(target, state, "artifact_prepared", actor="ain-orchestrator", stage=action["stage"], artifact=action["artifact"])
            state["status"] = f"draft_{action['stage']}"
            save_state(target, state)
            print(f"已创建：{destination}\n")
        print(prompt_text(target, action, state, risk))
    return 0


def cmd_risk(args: argparse.Namespace) -> int:
    target = args.target
    config = target_config(target)
    state = load_state(target, args.change_id)
    paths = args.paths.split(",") if args.paths else []
    inferred, matches = infer_risk(target, args.change_id, config, paths)
    risk = effective_risk(state, inferred)
    print(f"declared={state.get('declared_risk', 'R0')} inferred={inferred} effective={risk}")
    for item in matches:
        evidence = item["keywords"] + item["paths"]
        print(f"- {item['rule']} -> {item['level']}: {', '.join(evidence)}")
    if args.write:
        state["inferred_risk"] = inferred
        state["effective_risk"] = risk
        audit(target, state, "risk_evaluated", actor=args.by, inferred=inferred, effective=risk, matches=matches)
        save_state(target, state)
    return 0


def allowed_actors(config: dict[str, Any], role: str) -> list[str]:
    bindings = governance_config(config).get("role_bindings", {})
    if not isinstance(bindings, dict):
        return []
    value = bindings.get(role, [])
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def bound_approval_fields(
    target: Path,
    config: dict[str, Any],
    stage: dict[str, Any],
    path: Path,
    decision: str,
) -> dict[str, Any]:
    if decision != "approved" or not is_bound_stage(config, stage):
        return {}
    controls = governance_config(config)
    require_git = bool(controls.get("require_git_for_bound_approvals", True))
    require_clean = bool(controls.get("require_clean_worktree_for_approval", True))
    head = git_head(target, required=require_git)
    if require_clean and not git_is_clean(target):
        raise AinError("绑定审批前工作树必须干净；请先提交工件、配置和已有审计记录")
    fields: dict[str, Any] = {
        "binding": stage_approval_binding(stage),
        "artifact_sha256": sha256_file(path),
        "policy_sha256": sha256_file(config_path(target)),
        "git_head": head,
    }
    if stage_approval_binding(stage) == "artifact_and_subject_commit":
        metadata = parse_frontmatter(path.read_text(encoding="utf-8"))
        subject = metadata.get("commit_sha")
        if not is_git_sha(subject):
            raise AinError("此阶段必须在工件中填写有效的 commit_sha")
        if not git_commit_exists(target, str(subject)):
            raise AinError(f"此阶段引用的提交不存在：{subject}")
        fields["subject_commit"] = subject
    return fields


def git_changed_paths(target: Path, base: Optional[str], head: Optional[str], staged: bool) -> list[str]:
    if (base is None) != (head is None):
        raise AinError("--base 与 --head 必须同时提供")
    if base is not None and head is not None:
        if not git_commit_exists(target, base) or not git_commit_exists(target, head):
            raise AinError("--base 和 --head 必须是当前仓库可访问的提交 SHA")
        changed = git_output(target, "diff", "--name-only", base, head).splitlines()
    elif staged:
        changed = git_output(target, "diff", "--cached", "--name-only").splitlines()
    else:
        git_head(target, required=True)
        changed = git_output(target, "diff", "--name-only", "HEAD").splitlines()
        changed.extend(git_output(target, "ls-files", "--others", "--exclude-standard").splitlines())
    normalized = {normalize_repo_path(path) for path in changed if path.strip()}
    return sorted(normalized)


def product_changed_paths(config: dict[str, Any], paths: list[str]) -> list[str]:
    controls = governance_config(config).get("guard", {})
    ignored = controls.get("non_product_path_patterns", []) if isinstance(controls, dict) else []
    if not isinstance(ignored, list):
        raise AinError("governance.guard.non_product_path_patterns 必须是列表")
    product: list[str] = []
    for path in paths:
        if not any(matches_path_pattern(path, str(pattern)) for pattern in ignored):
            product.append(path)
    return product


def cmd_guard(args: argparse.Namespace) -> int:
    target = args.target
    config = target_config(target)
    controls = governance_config(config).get("guard", {})
    if not isinstance(controls, dict) or not controls.get("enabled", False):
        print("✓ guard 未启用")
        return 0
    changed = git_changed_paths(target, args.base, args.head, args.staged)
    product = product_changed_paths(config, changed)
    if not product:
        print("✓ 仅检测到治理/文档变更，无需关联产品变更")
        return 0
    if controls.get("require_change_for_product_changes", True) and not args.change_id:
        print("✗ 检测到产品变更，但没有变更 ID；请在 PR 标题中包含 CHG-YYYYMMDD-NNN，或传入 --change")
        for path in product:
            print(f"  - {path}")
        return 1
    if not args.change_id:
        return 0
    if not CHANGE_ID_RE.fullmatch(args.change_id):
        raise AinError("change ID 格式应为 CHG-YYYYMMDD-NNN")
    state = load_state(target, args.change_id)
    risk, _, evaluations = all_evaluations(target, state, config, product)
    plan_evaluation = next(item for item in evaluations if item["stage"] == "plan")
    if not plan_evaluation["complete"]:
        print(f"✗ {args.change_id}: 计划门禁未通过（risk={risk}）")
        for error in plan_evaluation["errors"]:
            print(f"  - {error}")
        if plan_evaluation["missing_roles"]:
            print("  - 缺少批准 " + ", ".join(plan_evaluation["missing_roles"]))
        for stale in plan_evaluation.get("stale_approvals", []):
            print(f"  - 批准已失效 {stale['role']}：{stale['reason']}")
        return 1
    plan_path = artifact_path(target, args.change_id, stage_config(config, "plan"))
    patterns = planned_paths(parse_frontmatter(plan_path.read_text(encoding="utf-8")))
    if controls.get("require_planned_paths", True):
        outside = [path for path in product if not any(matches_path_pattern(path, pattern) for pattern in patterns)]
        if outside:
            print(f"✗ {args.change_id}: 检测到未纳入 plan.md planned_paths 的产品变更")
            for path in outside:
                print(f"  - {path}")
            return 1
    print(f"✓ {args.change_id}: {len(product)} 个产品路径受已批准计划约束（risk={risk}）")
    return 0


def verification_controls(config: dict[str, Any]) -> dict[str, Any]:
    value = governance_config(config).get("verification", {})
    return value if isinstance(value, dict) else {}


def bounded_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def cmd_verify(args: argparse.Namespace) -> int:
    target = args.target
    config = target_config(target)
    if args.check:
        errors, records = read_evidence(target, args.change_id)
        if errors:
            print(f"✗ {args.change_id}: 证据链校验失败")
            for error in errors:
                print(f"  - {error}")
            return 1
        print(f"✓ {args.change_id}: {len(records)} 条证据记录的哈希链和日志均有效")
        return 0
    if not args.kind or not args.command:
        raise AinError("verify 需要 --kind 和 --command；只校验证据时使用 --check")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", args.kind):
        raise AinError("--kind 只能包含字母、数字、点、下划线和连字符")
    state = load_state(target, args.change_id)
    _, _, evaluations = all_evaluations(target, state, config)
    plan_evaluation = next(item for item in evaluations if item["stage"] == "plan")
    if not plan_evaluation["complete"]:
        raise AinError("执行验证前必须先通过 plan 门禁")
    if strict_governance(config) and not git_is_clean(target):
        raise AinError("执行验证前工作树必须干净；这样证据才能绑定到明确的提交")
    subject = git_head(target, required=True)
    try:
        argv = shlex.split(args.command)
    except ValueError as exc:
        raise AinError(f"--command 解析失败：{exc}") from exc
    if not argv:
        raise AinError("--command 不能为空")
    cwd = (target / args.cwd).resolve()
    if not path_is_within(cwd, target) or not cwd.is_dir():
        raise AinError("--cwd 必须是目标仓库内已存在的目录")
    controls = verification_controls(config)
    timeout = args.timeout if args.timeout is not None else bounded_positive_int(
        controls.get("default_timeout_seconds"), DEFAULT_COMMAND_TIMEOUT_SECONDS
    )
    if timeout <= 0:
        raise AinError("--timeout 必须大于 0")
    max_log_bytes = bounded_positive_int(controls.get("max_log_bytes"), DEFAULT_MAX_LOG_BYTES)
    started_at = now()
    started = time.monotonic()
    exit_code: Optional[int]
    timed_out = False
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=timeout,
        )
        output = completed.stdout or b""
        exit_code = completed.returncode
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or b""
        if isinstance(output, str):
            output = output.encode("utf-8", errors="replace")
        output += f"\n[AIN] command timed out after {timeout} seconds\n".encode("utf-8")
        exit_code = None
        timed_out = True
    except OSError as exc:
        output = f"[AIN] command could not start: {exc}\n".encode("utf-8", errors="replace")
        exit_code = 127
    duration_ms = round((time.monotonic() - started) * 1000)
    if isinstance(output, str):
        output = output.encode("utf-8", errors="replace")
    truncated = len(output) > max_log_bytes
    persisted_output = output[:max_log_bytes]
    folder = evidence_dir(target, args.change_id)
    folder.mkdir(parents=True, exist_ok=True)
    log_name = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{time.time_ns()}-{args.kind}.log"
    log_path = folder / log_name
    log_path.write_bytes(persisted_output)
    manifest = evidence_manifest_path(target, args.change_id)
    if manifest.exists():
        existing_errors, records = read_evidence(target, args.change_id)
        if existing_errors:
            raise AinError("已有证据链不完整，拒绝追加新记录：" + "；".join(existing_errors))
        previous_hash = records[-1].get("record_hash") if records else None
    else:
        previous_hash = None
    log_relative = str(log_path.relative_to(folder))
    record: dict[str, Any] = {
        "schema_version": 1,
        "kind": args.kind,
        "command": argv,
        "cwd": str(cwd.relative_to(target)) or ".",
        "subject_commit": subject,
        "started_at": started_at,
        "finished_at": now(),
        "duration_ms": duration_ms,
        "expected_exit": args.expected_exit,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "log_path": log_relative,
        "log_sha256": sha256_file(log_path),
        "log_bytes": log_path.stat().st_size,
        "truncated": truncated,
        "original_log_bytes": len(output),
        "previous_hash": previous_hash,
    }
    record["record_hash"] = record_hash(record)
    with manifest.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    audit(
        target,
        state,
        "verification_command_executed",
        actor="ain-verifier",
        kind=args.kind,
        subject_commit=subject,
        exit_code=exit_code,
        expected_exit=args.expected_exit,
        record_hash=record["record_hash"],
    )
    save_state(target, state)
    outcome = "✓" if exit_code == args.expected_exit and not timed_out else "✗"
    print(f"{outcome} {args.change_id}: {args.kind} exit={exit_code} expected={args.expected_exit} evidence={log_path.relative_to(target)}")
    return 0 if outcome == "✓" else 1


def cmd_approve(args: argparse.Namespace) -> int:
    target = args.target
    config = target_config(target)
    state = load_state(target, args.change_id)
    risk, _, evaluations = all_evaluations(target, state, config)
    evaluation = next(item for item in evaluations if item["stage"] == args.stage)
    if not evaluation["exists"]:
        raise AinError(f"不能审批：缺少 {evaluation['artifact']}")
    if evaluation["errors"]:
        raise AinError("不能审批，工件校验未通过：" + "；".join(evaluation["errors"]))
    allowed = evaluation["required_roles"]
    if args.role not in allowed:
        raise AinError(f"角色 {args.role} 不是此阶段所需角色；需要：{', '.join(allowed) or '无'}")
    actor_allowlist = allowed_actors(config, args.role)
    if actor_allowlist and args.by not in actor_allowlist:
        raise AinError(f"{args.by} 不在角色 {args.role} 的本地身份白名单中")
    stage = stage_config(config, args.stage)
    path = artifact_path(target, args.change_id, stage)
    approval = {
        "stage": args.stage,
        "role": args.role,
        "by": args.by,
        "decision": args.decision,
        "at": now(),
        "note": args.note,
        **bound_approval_fields(target, config, stage, path, args.decision),
    }
    state["approvals"] = [item for item in state.get("approvals", []) if not (item.get("stage") == args.stage and item.get("role") == args.role)]
    state["approvals"].append(approval)
    if args.decision == "rejected":
        state["status"] = "blocked"
    else:
        _, _, refreshed = all_evaluations(target, state, config)
        state["status"] = derived_status(refreshed)
    audit(
        target,
        state,
        "stage_decision",
        actor=args.by,
        stage=args.stage,
        role=args.role,
        decision=args.decision,
        note=args.note,
        artifact_sha256=approval.get("artifact_sha256"),
        subject_commit=approval.get("subject_commit"),
    )
    save_state(target, state)
    print(f"已记录：{args.stage} / {args.role} / {args.decision} / {args.by}")
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    target = args.target
    config = target_config(target)
    ids = [args.change_id] if args.change_id else sorted(p.name for p in (target / "ai" / "changes").glob("CHG-*") if p.is_dir())
    if not ids:
        raise AinError("没有找到变更")
    through_index = next(index for index, item in enumerate(config["stages"]) if item["id"] == args.through)
    failed = False
    for change_id in ids:
        state = load_state(target, change_id)
        risk, _, evaluations = all_evaluations(target, state, config)
        blockers = [item for item in evaluations[:through_index + 1] if not item["complete"]]
        if not blockers:
            print(f"✓ {change_id}: through={args.through} risk={risk}")
            continue
        failed = True
        print(f"✗ {change_id}: 未通过 {args.through} 门禁（risk={risk}）")
        for item in blockers:
            if not item["exists"]:
                print(f"  - {item['stage']}: 缺少 {item['artifact']}")
            for error in item["errors"]:
                print(f"  - {item['stage']}: {error}")
            if item["missing_roles"]:
                print(f"  - {item['stage']}: 缺少批准 {', '.join(item['missing_roles'])}")
            for stale in item.get("stale_approvals", []):
                print(f"  - {item['stage']}: 批准已失效 {stale['role']}：{stale['reason']}")
    return 1 if failed else 0


def cmd_doctor(args: argparse.Namespace) -> int:
    target = args.target
    problems: list[str] = []
    if sys.version_info < (3, 9):
        problems.append("需要 Python 3.9+")
    try:
        config = target_config(target)
        for key in ["schema_version", "stages", "risk"]:
            if key not in config:
                problems.append(f"配置缺少 {key}")
    except AinError as exc:
        problems.append(str(exc))
    installed = target / ".ain" / "config.json"
    if not installed.exists():
        print("提示：当前目标尚未 init；正在检查框架分发包。")
    for name in ["config", "templates", "prompts"]:
        try:
            resource(name)
        except AinError as exc:
            problems.append(str(exc))
    if problems:
        for problem in problems:
            print(f"✗ {problem}")
        return 1
    print(f"✓ Python {sys.version.split()[0]}")
    print("✓ 配置、模板和阶段提示完整")
    print(f"✓ target={target}")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="ain", description="AIN-Loop AI-native SDLC orchestrator")
    root.add_argument("--target", type=Path, default=Path(os.environ.get("AIN_TARGET", ".")), help="目标业务仓库")
    sub = root.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="在业务仓库安装 AIN-Loop")
    init.add_argument("--force", action="store_true", help="覆盖 AIN-Loop 自有文件")
    init.add_argument("--with-github", action="store_true", help="安装 GitHub Actions 门禁")
    init.set_defaults(func=cmd_init)

    new = sub.add_parser("new", help="创建变更和 intent")
    new.add_argument("--id")
    new.add_argument("--title", required=True)
    new.add_argument("--owner", required=True)
    new.add_argument("--source", default="idea")
    new.add_argument("--risk", choices=list(LEVEL_VALUE), default="R0")
    new.set_defaults(func=cmd_new)

    validate = sub.add_parser("validate", help="校验一个或全部变更工件")
    validate.add_argument("change_id", nargs="?")
    validate.set_defaults(func=cmd_validate)

    status = sub.add_parser("status", help="显示状态、风险和下一步")
    status.add_argument("change_id")
    status.add_argument("--paths", help="逗号分隔的拟修改路径")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    next_cmd = sub.add_parser("next", help="生成下一阶段 Agent 提示或审批命令")
    next_cmd.add_argument("change_id")
    next_cmd.add_argument("--prepare", action="store_true", help="下一工件缺失时，从模板创建后输出提示")
    next_cmd.set_defaults(func=cmd_next)

    risk = sub.add_parser("risk", help="推断风险下限")
    risk.add_argument("change_id")
    risk.add_argument("--paths", help="逗号分隔的拟修改路径")
    risk.add_argument("--write", action="store_true", help="写入状态与审计日志")
    risk.add_argument("--by", default="ain-risk-engine")
    risk.set_defaults(func=cmd_risk)

    approve = sub.add_parser("approve", help="记录带身份和角色的阶段决策")
    approve.add_argument("change_id")
    approve.add_argument("--stage", required=True, choices=["intent", "spec", "plan", "verification", "review", "release"])
    approve.add_argument("--role", required=True)
    approve.add_argument("--by", required=True)
    approve.add_argument("--decision", choices=["approved", "rejected"], default="approved")
    approve.add_argument("--note", default="")
    approve.set_defaults(func=cmd_approve)

    guard = sub.add_parser("guard", help="用 Git diff 检查产品变更是否受已批准计划约束")
    guard.add_argument("--change", dest="change_id", help="关联的 CHG-YYYYMMDD-NNN")
    guard.add_argument("--base", help="比较起点提交（通常是 PR base SHA）")
    guard.add_argument("--head", help="比较终点提交（通常是 PR head SHA）")
    guard.add_argument("--staged", action="store_true", help="检查暂存区，而不是工作树")
    guard.set_defaults(func=cmd_guard)

    verify = sub.add_parser("verify", help="实际执行命令并保存可校验的原始证据")
    verify.add_argument("change_id")
    verify.add_argument("--kind", help="证据类型，例如 unit、lint、integration")
    verify.add_argument("--command", help="要直接执行的命令；不会经过 shell")
    verify.add_argument("--cwd", default=".", help="命令的仓库内工作目录")
    verify.add_argument("--timeout", type=int, help="命令超时秒数")
    verify.add_argument("--expected-exit", type=int, default=0, help="期望退出码")
    verify.add_argument("--check", action="store_true", help="仅校验已保存的证据哈希链和日志")
    verify.set_defaults(func=cmd_verify)

    gate = sub.add_parser("gate", help="验证指定阶段及其所有前置工件和批准")
    gate.add_argument("change_id", nargs="?")
    gate.add_argument("--through", required=True, choices=["intent", "spec", "plan", "verification", "review", "release"])
    gate.set_defaults(func=cmd_gate)

    doctor = sub.add_parser("doctor", help="检查运行环境和安装包")
    doctor.set_defaults(func=cmd_doctor)
    return root


def main() -> int:
    args = parser().parse_args()
    args.target = args.target.resolve()
    try:
        return args.func(args)
    except AinError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
