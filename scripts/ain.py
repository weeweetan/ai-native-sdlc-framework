#!/usr/bin/env python3
"""AIN-Loop: deterministic orchestration for an AI-native delivery loop.

The runtime deliberately has no third-party dependencies. Models generate and
reason about artifacts; this program validates state, permissions and gates.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_PATH = Path(__file__).resolve()
if SCRIPT_PATH.parent.name == "scripts":
    DIST_ROOT = SCRIPT_PATH.parent.parent
elif SCRIPT_PATH.parent.name == "runtime":
    DIST_ROOT = SCRIPT_PATH.parent.parent
else:
    DIST_ROOT = SCRIPT_PATH.parent

LEVEL_VALUE = {"R0": 0, "R1": 1, "R2": 2, "R3": 3}


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
    installed = target / ".ain" / "config.json"
    return read_json(installed if installed.exists() else resource("config"))


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


def validate_artifact(path: Path, stage: dict[str, Any], config: dict[str, Any], change_id: str) -> list[str]:
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
    if stage["id"] == "verification" and "ready_for_review: true" not in text.lower():
        errors.append("缺少 ready_for_review: true 完成声明")
    return errors


def infer_risk(target: Path, change_id: str, config: dict[str, Any], paths: list[str] | None = None) -> tuple[str, list[dict[str, Any]]]:
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


def approved_roles(state: dict[str, Any], stage_id: str) -> set[str]:
    return {
        item["role"] for item in state.get("approvals", [])
        if item.get("stage") == stage_id and item.get("decision") == "approved"
    }


def stage_evaluation(target: Path, state: dict[str, Any], config: dict[str, Any], item: dict[str, Any], risk: str) -> dict[str, Any]:
    path = change_dir(target, state["change_id"]) / item["artifact"]
    exists = path.exists()
    errors = validate_artifact(path, item, config, state["change_id"]) if exists else []
    required = required_roles(config, item["id"], risk)
    approved = approved_roles(state, item["id"])
    missing = [role for role in required if role not in approved]
    complete = exists and not errors and not missing
    return {"stage": item["id"], "artifact": item["artifact"], "exists": exists, "errors": errors, "required_roles": required, "missing_roles": missing, "complete": complete}


def all_evaluations(target: Path, state: dict[str, Any], config: dict[str, Any], paths: list[str] | None = None) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    inferred, matches = infer_risk(target, state["change_id"], config, paths)
    risk = effective_risk(state, inferred)
    evaluations = [stage_evaluation(target, state, config, item, risk) for item in config["stages"]]
    return risk, matches, evaluations


def next_action(evaluations: list[dict[str, Any]]) -> dict[str, Any] | None:
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
    approval = {"stage": args.stage, "role": args.role, "by": args.by, "decision": args.decision, "at": now(), "note": args.note}
    state["approvals"] = [item for item in state.get("approvals", []) if not (item.get("stage") == args.stage and item.get("role") == args.role)]
    state["approvals"].append(approval)
    if args.decision == "rejected":
        state["status"] = "blocked"
    else:
        _, _, refreshed = all_evaluations(target, state, config)
        state["status"] = derived_status(refreshed)
    audit(target, state, "stage_decision", actor=args.by, stage=args.stage, role=args.role, decision=args.decision, note=args.note)
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
