"""Offline checks of the Grafana dashboards and alert rules under grafana/.

What this proves: the committed JSON is exactly what grafana/generate.py produces, every panel sits on the
grid without overlapping, every ai_lab_ metric a query uses is one scripts/ai-lab-metrics writes, the
CloudWatch metrics match the metric filters in vuln_mcp.tf, and every alert rule's condition points at a
query or expression that exists. What it cannot prove: that a real Grafana accepts the files or that the
queries return data. That is the first import (docs/runbook/smoke-tests/lab-features.md).

Run: python -m unittest discover -s tests
"""

import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "grafana"))
import generate  # noqa: E402

EXPORTER = (ROOT / "scripts" / "ai-lab-metrics").read_text()
VULN_TF = (ROOT / "vuln_mcp.tf").read_text()
DASHBOARDS = {name: json.loads((ROOT / "grafana" / "dashboards" / name).read_text()) for name in generate.DASHBOARDS}
ALERTS = json.loads((ROOT / "grafana" / "alerts" / "alert-rules.json").read_text())


def queries(dashboard):
    for panel in dashboard["panels"]:
        for target in panel.get("targets", []):
            yield panel, target


class GeneratedFilesTest(unittest.TestCase):
    def test_the_committed_json_is_what_the_generator_writes(self):
        for rel, text in generate.render().items():
            self.assertEqual((ROOT / "grafana" / rel).read_text(), text, f"{rel} is stale: run python grafana/generate.py")

    def test_five_dashboards_with_unique_ids_and_titles(self):
        self.assertEqual(len(DASHBOARDS), 5)
        uids = [d["uid"] for d in DASHBOARDS.values()]
        self.assertEqual(len(set(uids)), 5)
        self.assertIn("ai-lab-overview", uids)
        self.assertEqual(len({d["title"] for d in DASHBOARDS.values()}), 5)


class LayoutTest(unittest.TestCase):
    def test_panels_have_unique_ids_stay_on_the_grid_and_do_not_overlap(self):
        for name, dash in DASHBOARDS.items():
            ids = [p["id"] for p in dash["panels"]]
            self.assertEqual(len(ids), len(set(ids)), name)
            boxes = []
            for p in dash["panels"]:
                g = p["gridPos"]
                self.assertTrue(0 <= g["x"] and g["x"] + g["w"] <= 24 and g["h"] >= 1, f"{name}: {p['title']}")
                boxes.append((g["x"], g["y"], g["x"] + g["w"], g["y"] + g["h"], p["title"]))
            for i, a in enumerate(boxes):
                for b in boxes[i + 1:]:
                    overlap = a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]
                    self.assertFalse(overlap, f"{name}: {a[4]} overlaps {b[4]}")


class QueryTest(unittest.TestCase):
    def test_data_sources_are_always_dashboard_variables(self):
        for name, dash in DASHBOARDS.items():
            declared = {v["name"] for v in dash["templating"]["list"]}
            for panel, target in queries(dash):
                uid = target["datasource"]["uid"]
                self.assertRegex(uid, r"^\$\{ds_(prom|loki|cw)\}$", f"{name}: {panel['title']}")
                self.assertIn(uid[2:-1], declared, f"{name}: {panel['title']}")

    def test_every_ai_lab_metric_used_is_written_by_the_exporter(self):
        used = set()
        for dash in DASHBOARDS.values():
            for _, target in queries(dash):
                used |= set(re.findall(r"\bai_lab_[a-z_]+", target.get("expr", "")))
        for rule in ALERTS["rules"]:
            for step in rule["data"]:
                used |= set(re.findall(r"\bai_lab_[a-z_]+", step["model"].get("expr", "")))
        self.assertTrue(used)
        for metric in used:
            self.assertIn(metric, EXPORTER, f"{metric} is not written by scripts/ai-lab-metrics")

    def test_cloudwatch_tool_metrics_match_the_metric_filters(self):
        filters = set(re.findall(r'name\s*=\s*"(Tool[A-Za-z]+|Unauthorized)"', VULN_TF))
        self.assertEqual(filters, {"ToolCalls", "ToolErrors", "ToolLatencyMs", "Unauthorized"})
        self.assertIn('namespace  = "AILab/Tools"', VULN_TF)
        sqls = [t["sqlExpression"] for d in DASHBOARDS.values() for _, t in queries(d) if "sqlExpression" in t]
        sqls += [s["model"]["sqlExpression"] for r in ALERTS["rules"] for s in r["data"] if "sqlExpression" in s["model"]]
        for sql in sqls:
            for metric in re.findall(r"(?:SUM|AVG|MAX|MIN|COUNT)\((\w+)\)", sql):
                if sql.count("AILab/Tools"):
                    self.assertIn(metric, filters, sql)

    def test_the_lambda_name_in_queries_matches_the_function_name(self):
        self.assertIn('function_name    = "${var.project_name}-vuln-mcp"', VULN_TF)
        for _, t in queries(DASHBOARDS["tool-calls.json"]):
            for name in re.findall(r"FunctionName = '([^']+)'", t.get("sqlExpression", "")):
                self.assertEqual(name, "__PROJECT__-vuln-mcp")

    def test_log_queries_never_ask_for_message_text_fields_beyond_streams(self):
        for _, t in queries(DASHBOARDS["logs.json"]):
            self.assertTrue(t["expr"].startswith(("{$stream}", "sum by (job) (count_over_time({$stream}")), t["expr"])


class AlertRuleTest(unittest.TestCase):
    def test_seven_rules_with_unique_ids_in_one_group(self):
        rules = ALERTS["rules"]
        self.assertEqual(len(rules), 7)
        self.assertEqual(len({r["uid"] for r in rules}), 7)
        self.assertEqual({r["ruleGroup"] for r in rules}, {generate.RULE_GROUP})
        self.assertEqual({r["folderUID"] for r in rules}, {ALERTS["folderUid"]})

    def test_each_condition_and_expression_points_at_something_that_exists(self):
        for rule in ALERTS["rules"]:
            refs = {s["refId"] for s in rule["data"]}
            self.assertIn(rule["condition"], refs, rule["title"])
            self.assertEqual(rule["data"][-1]["refId"], rule["condition"], rule["title"])
            for step in rule["data"]:
                model = step["model"]
                if model.get("type") in ("reduce", "threshold"):
                    self.assertIn(model["expression"], refs, rule["title"])
                if model.get("type") == "math":
                    for ref in re.findall(r"\$([A-Z])", model["expression"]):
                        self.assertIn(ref, refs, rule["title"])

    def test_placeholders_are_only_the_ones_the_import_script_fills_in(self):
        text = json.dumps(ALERTS)
        self.assertEqual(set(re.findall(r"__[A-Z_]+__", text)) - {"__expr__"}, {"__DS_PROM__", "__DS_CW__", "__INSTANCE_ID__"})

    def test_no_data_never_pages_while_the_lab_is_stopped(self):
        for rule in ALERTS["rules"]:
            self.assertEqual(rule["noDataState"], "OK", rule["title"])

    def test_the_spec_thresholds_are_in_the_rules(self):
        text = json.dumps(ALERTS["rules"])
        by_uid = {r["uid"]: r for r in ALERTS["rules"]}
        self.assertEqual(by_uid["ai-lab-tool-errors"]["data"][-1]["model"]["conditions"][0]["evaluator"]["params"], [10])
        self.assertEqual(by_uid["ai-lab-mcp-refusals"]["data"][-1]["model"]["conditions"][0]["evaluator"]["params"], [5])
        self.assertEqual(by_uid["ai-lab-disk-filling"]["data"][-1]["model"]["conditions"][0]["evaluator"]["params"], [85])
        self.assertEqual(by_uid["ai-lab-webui-errors"]["data"][-1]["model"]["conditions"][0]["evaluator"]["params"], [5])
        self.assertIn("> bool 85", text)


if __name__ == "__main__":
    unittest.main()
