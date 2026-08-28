import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "ain.py"


class AinCliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.target = Path(self.temp.name) / "service"
        self.target.mkdir()

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

    def test_init_new_risk_and_gate(self):
        self.run_cli("init", "--with-github")
        self.assertTrue((self.target / ".ain" / "ain").exists())
        self.assertTrue((self.target / ".github" / "workflows" / "ain-gate.yml").exists())

        change_id = "CHG-20260828-001"
        self.run_cli(
            "new", "--id", change_id, "--title", "支付 API 增加退款查询",
            "--owner", "Lin", "--source", "customer_feedback",
        )
        folder = self.target / "ai" / "changes" / change_id
        self.assertTrue((folder / "intent.md").exists())

        risk = self.run_cli("risk", change_id, "--paths", "services/payments/refund.py")
        self.assertIn("effective=R2", risk.stdout)

        rejected = self.run_cli(
            "approve", change_id, "--stage", "intent", "--role", "product_owner", "--by", "Lin",
            ok=False,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("校验未通过", rejected.stderr)

        intent = folder / "intent.md"
        text = intent.read_text(encoding="utf-8")
        text = text.replace(
            "<!-- 当前谁在什么场景下遇到什么问题？附数据、工单、反馈或事故链接，不要只写解决方案。 -->",
            "客服无法查询退款状态，最近一个月产生 312 个重复咨询工单。",
        )
        text = text.replace(
            "<!-- 写出可观察、可测量的成功标准和预期收益。 -->",
            "用户可在订单页看到退款状态，使相关咨询量在四周内下降 30%。",
        )
        text = text.replace(
            "- 安全、隐私、法规、性能、成本或兼容性约束：",
            "- 安全、隐私、法规、性能、成本或兼容性约束：沿用订单鉴权，不记录支付信息。",
        )
        intent.write_text(text, encoding="utf-8")

        self.run_cli("validate", change_id)
        self.run_cli("approve", change_id, "--stage", "intent", "--role", "product_owner", "--by", "Lin")
        self.run_cli("gate", change_id, "--through", "intent")
        blocked_gate = self.run_cli("gate", change_id, "--through", "spec", ok=False)
        self.assertEqual(blocked_gate.returncode, 1)
        next_result = self.run_cli("next", change_id, "--prepare")
        self.assertIn("spec.md", next_result.stdout)
        self.assertTrue((folder / "spec.md").exists())

        state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["approvals"][0]["decision"], "approved")
        self.assertTrue((self.target / ".ain" / "audit.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
