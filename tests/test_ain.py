import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "ain.py"


class AinCliTest(unittest.TestCase):
    change_id = "CHG-20260828-001"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.target = Path(self.temp.name) / "service"
        self.target.mkdir()
        self.git("init")
        self.git("config", "user.name", "AIN Test")
        self.git("config", "user.email", "ain-test@example.invalid")
        self.run_cli("init", "--with-github")
        self.commit("bootstrap AIN")

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args, ok=True):
        result = subprocess.run(
            [sys.executable, str(CLI), "--target", str(self.target), *args],
            text=True,
            capture_output=True,
        )
        if ok and result.returncode != 0:
            self.fail(f"command failed ({result.returncode}): {result.stdout}\n{result.stderr}")
        return result

    def git(self, *args, ok=True):
        result = subprocess.run(
            ["git", "-C", str(self.target), *args],
            text=True,
            capture_output=True,
        )
        if ok and result.returncode != 0:
            self.fail(f"git failed ({result.returncode}): {result.stdout}\n{result.stderr}")
        return result

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "--no-gpg-sign", "-m", message)

    def head(self):
        return self.git("rev-parse", "HEAD").stdout.strip()

    def folder(self):
        return self.target / "ai" / "changes" / self.change_id

    def yaml_value(self, value):
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False)
        return json.dumps(str(value), ensure_ascii=False)

    def write_document(self, name, metadata, title, sections):
        frontmatter = "\n".join(f"{key}: {self.yaml_value(value)}" for key, value in metadata.items())
        body = "\n\n".join(f"## {heading}\n\n{content}" for heading, content in sections)
        (self.folder() / name).write_text(
            f"---\n{frontmatter}\n---\n\n# {title}\n\n{body}\n",
            encoding="utf-8",
        )

    def create_change(self):
        self.run_cli(
            "new",
            "--id",
            self.change_id,
            "--title",
            "Improve change traceability",
            "--owner",
            "Lin",
            "--source",
            "test",
        )
        self.write_intent()
        self.commit("create valid intent")

    def write_intent(self):
        self.write_document(
            "intent.md",
            {
                "change_id": self.change_id,
                "status": "accepted",
                "risk_level": "R0",
                "source": "test",
                "owner": "Lin",
            },
            "Intent: improve traceability",
            [
                ("问题与证据", "交付记录分散，维护者无法快速确认一次改动的责任范围。"),
                ("期望结果", "每次改动都有可追溯的计划与验证记录，交接时间明显缩短。"),
                ("约束与不可做项", "保持零第三方运行时依赖，不读取外部凭据，也不改变现有数据。"),
            ],
        )

    def write_spec(self):
        self.write_document(
            "spec.md",
            {
                "change_id": self.change_id,
                "status": "accepted",
                "input_intent": "intent.md",
                "owner": "Lin",
                "technical_owner": "Rae",
                "risk_level": "R0",
            },
            "Specification: traceability",
            [
                ("成功标准", "维护者能从一次变更定位计划、执行结果和责任说明。"),
                ("行为需求", "系统在变更目录中保存可读工件，并能按阶段报告缺失项。"),
                ("设计与技术边界", "只使用标准库和本地 Git 元数据；不调用远程服务。"),
                ("策略与风险", "不收集个人数据，失败时保留工件并要求人工处理。"),
            ],
        )

    def write_plan(self, paths="src/**"):
        self.write_document(
            "plan.md",
            {
                "change_id": self.change_id,
                "status": "accepted",
                "input_spec": "spec.md",
                "engineer_owner": "Rae",
                "risk_level": "R0",
                "planned_paths": paths,
            },
            "Implementation Plan: traceability",
            [
                ("变更范围", "只修改已声明的路径，并为关键规则添加回归测试。"),
                ("实施顺序", "先写失败用例，再实现检查逻辑，最后运行完整验证。"),
                ("风险、依赖与回退", "错误阻断时可回退单次提交；运行时只依赖本机 Git。"),
                ("验证计划", "执行标准库单元测试，并记录命令退出码与原始日志。"),
            ],
        )

    def write_verification(self, result, commit_sha, evidence_manifest=""):
        self.write_document(
            "verification.md",
            {
                "change_id": self.change_id,
                "commit_sha": commit_sha,
                "agent_identity": "Verifier",
                "completed_at": "2026-08-28T00:00:00Z",
                "result": result,
                "evidence_manifest": evidence_manifest,
                "ready_for_review": True,
                "release_blocked": result == "partial",
            },
            "Verification Evidence: traceability",
            [
                ("计划符合性", "实现路径与已批准计划一致，没有扩大修改范围。"),
                ("实际执行证据", "命令由 AIN runtime 直接执行，输出保存在本变更的证据目录。"),
                ("独立验证", "验证者检查了退出码、日志哈希和被验证提交。"),
            ],
        )

    def write_review(self, commit_sha):
        self.write_document(
            "review.md",
            {
                "change_id": self.change_id,
                "commit_sha": commit_sha,
                "result": "pass",
                "reviewer_identity": "Casey",
            },
            "Review: traceability",
            [
                ("计划一致性", "审查的改动在已批准范围内，验证状态被如实保留。"),
                ("审查发现", "无阻断发现；后续仍需要补齐发布前验证。"),
                ("残余风险", "部分验证尚未完成时由 release_blocked 防止发布。"),
            ],
        )

    def write_release(self, commit_sha):
        self.write_document(
            "release.md",
            {
                "change_id": self.change_id,
                "commit_sha": commit_sha,
                "environment": "staging",
                "decision": "approved",
            },
            "Release: traceability",
            [
                ("发布条件", "计划、验证和审查工件均已被检查。"),
                ("回滚", "出现异常时回退到前一个已知提交并重新验证。"),
                ("发布后观测", "观察错误率、请求耗时和使用反馈。"),
            ],
        )

    def approve(self, stage, role, actor):
        self.run_cli("approve", self.change_id, "--stage", stage, "--role", role, "--by", actor)
        self.commit(f"approve {stage}")

    def prepare_approved_plan(self, paths="src/**"):
        self.create_change()
        self.run_cli("validate", self.change_id)
        self.approve("intent", "product_owner", "Lin")
        self.write_spec()
        self.commit("write spec")
        self.approve("spec", "product_owner", "Lin")
        self.write_plan(paths)
        self.commit("write plan")
        self.approve("plan", "engineer", "Rae")

    def test_init_installs_universal_github_gate(self):
        workflow = (self.target / ".github" / "workflows" / "ain-gate.yml").read_text(encoding="utf-8")
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("guard --base", workflow)
        self.assertNotIn("paths:", workflow)
        self.assertTrue((self.target / ".ain" / "ain").exists())
        installed = subprocess.run(
            [str(self.target / ".ain" / "ain"), "doctor"],
            cwd=self.target,
            text=True,
            capture_output=True,
        )
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        self.assertIn("配置、模板和阶段提示完整", installed.stdout)

    def test_bound_approval_is_invalidated_when_artifact_changes(self):
        self.create_change()
        self.approve("intent", "product_owner", "Lin")
        self.run_cli("gate", self.change_id, "--through", "intent")

        intent = self.folder() / "intent.md"
        intent.write_text(intent.read_text(encoding="utf-8") + "\n补充了新的、仍然有效的业务背景。\n", encoding="utf-8")
        blocked = self.run_cli("gate", self.change_id, "--through", "intent", ok=False)
        self.assertEqual(blocked.returncode, 1)
        self.assertIn("批准已失效", blocked.stdout)

        state = json.loads((self.folder() / "state.json").read_text(encoding="utf-8"))
        self.assertIn("artifact_sha256", state["approvals"][0])
        self.assertIn("policy_sha256", state["approvals"][0])

    def test_role_binding_rejects_unlisted_actor(self):
        config_path = self.target / ".ain" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["governance"]["role_bindings"] = {"product_owner": ["Lin"]}
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.commit("bind product owner")
        self.create_change()

        result = self.run_cli(
            "approve",
            self.change_id,
            "--stage",
            "intent",
            "--role",
            "product_owner",
            "--by",
            "Eve",
            ok=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("身份白名单", result.stderr)

    def test_guard_requires_change_and_planned_path_coverage(self):
        self.prepare_approved_plan("src/**")
        base = self.head()
        (self.target / "src").mkdir()
        (self.target / "src" / "allowed.py").write_text("value = 1\n", encoding="utf-8")

        missing_change = self.run_cli("guard", ok=False)
        self.assertEqual(missing_change.returncode, 1)
        self.assertIn("没有变更 ID", missing_change.stdout)
        self.run_cli("guard", "--change", self.change_id)
        self.commit("change an allowed path")
        self.run_cli("guard", "--change", self.change_id, "--base", base, "--head", self.head())

        (self.target / "outside.txt").write_text("outside scope\n", encoding="utf-8")
        outside_scope = self.run_cli("guard", "--change", self.change_id, ok=False)
        self.assertEqual(outside_scope.returncode, 1)
        self.assertIn("未纳入", outside_scope.stdout)

    def test_verify_writes_and_detects_tampered_evidence(self):
        self.prepare_approved_plan()
        (self.target / "src").mkdir()
        (self.target / "src" / "change.py").write_text("answer = 42\n", encoding="utf-8")
        self.commit("implement change")

        command = f"{sys.executable} -c \"print('evidence ok')\""
        self.run_cli("verify", self.change_id, "--kind", "unit", "--command", command)
        self.run_cli("verify", self.change_id, "--check")

        log = next((self.folder() / "evidence").glob("*-unit.log"))
        log.write_bytes(log.read_bytes() + b"tampered\n")
        invalid = self.run_cli("verify", self.change_id, "--check", ok=False)
        self.assertEqual(invalid.returncode, 1)
        self.assertIn("日志哈希不匹配", invalid.stdout)

    def test_pass_requires_matching_command_evidence(self):
        self.prepare_approved_plan()
        (self.target / "src").mkdir()
        (self.target / "src" / "change.py").write_text("answer = 42\n", encoding="utf-8")
        self.commit("implement change")
        self.write_verification("pass", self.head(), "evidence/evidence.jsonl")
        self.commit("claim pass without evidence")

        invalid = self.run_cli("validate", self.change_id, ok=False)
        self.assertEqual(invalid.returncode, 1)
        self.assertIn("缺少证据清单", invalid.stdout)

    def test_partial_can_be_reviewed_but_never_released(self):
        self.prepare_approved_plan()
        (self.target / "src").mkdir()
        (self.target / "src" / "partial.py").write_text("answer = 7\n", encoding="utf-8")
        self.commit("implement partial change")
        subject = self.head()

        self.write_verification("partial", subject)
        self.commit("record partial verification")
        self.run_cli("validate", self.change_id)
        self.approve("verification", "verifier", "Verifier")

        self.write_review(subject)
        self.commit("write review")
        self.approve("review", "code_owner", "Casey")
        self.run_cli("gate", self.change_id, "--through", "review")

        self.write_release(subject)
        self.commit("prepare release")
        blocked = self.run_cli("gate", self.change_id, "--through", "release", ok=False)
        self.assertEqual(blocked.returncode, 1)
        self.assertIn("partial", blocked.stdout)

    def test_v1_config_keeps_legacy_unbound_approval_behavior(self):
        config_path = self.target / ".ain" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["schema_version"] = 1
        config.pop("governance", None)
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.commit("simulate v1 config")
        self.create_change()

        intent = self.folder() / "intent.md"
        intent.write_text(intent.read_text(encoding="utf-8") + "\n未提交但有效的补充背景。\n", encoding="utf-8")
        self.run_cli("approve", self.change_id, "--stage", "intent", "--role", "product_owner", "--by", "Lin")
        self.run_cli("gate", self.change_id, "--through", "intent")


if __name__ == "__main__":
    unittest.main()
