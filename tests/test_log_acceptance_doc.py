"""Keeps docs/log-tools-acceptance-tests.md honest, offline.

The document tells a person what to put in the lab's logs, what to ask the Security Analyst and what
answer to expect. The expectations live in docs/log-acceptance-cases.json. Each case names a tool, its
arguments, the log lines the real Loki would hold at that moment ("rows", as [seconds ago, line]) and the
values the tool must return. This test runs the real log tool code (lambda/vuln_mcp/log_tools.py) against
a fake log store holding those lines and checks every expected value, so a document question cannot drift
from what the code does.

Expected values are [path, "=", value], [path, "contains", text] or [path, "excludes", text]; a path like
"sources.0.source" walks the tool's JSON result.

Nothing here talks to Loki, Open WebUI, a model or AWS. The questions themselves are run by a person.
"""

import json
import os
import pathlib
import re
import sys
import unittest

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lambda" / "vuln_mcp"))

import log_tools  # noqa: E402
from log_tools import LogTools, ToolError  # noqa: E402

DOC = ROOT / "docs" / "log-tools-acceptance-tests.md"
CASES = json.loads((ROOT / "docs" / "log-acceptance-cases.json").read_text(encoding="utf-8"))
NOW = 1_800_000_000.0


class FakeBackend:
    def __init__(self, rows, points):
        self.rows, self.points = rows, points

    def streams(self, logql, start_s, end_s, limit):
        return [(int((NOW - ago) * 1e9), line) for ago, line in self.rows][:limit]

    def matrix(self, logql, start_s, end_s, step_s):
        return [(NOW - ago, value) for ago, value in self.points]


def lookup(result, path):
    for part in path.split("."):
        result = result[int(part)] if isinstance(result, list) else result[part]
    return result


class LogAcceptanceDocTest(unittest.TestCase):
    def test_every_case_has_a_matching_heading_in_the_document(self):
        headings = re.findall(r"^### (L\d+)\b", DOC.read_text(encoding="utf-8"), re.M)
        self.assertEqual(headings, [case["id"] for case in CASES])
        self.assertEqual(len(headings), len(set(headings)))

    def test_expected_values_are_what_the_real_tools_return(self):
        for case in CASES:
            with self.subTest(case=case["id"]):
                self.assertIn(case["tool"], log_tools.LOG_TOOL_NAMES)
                tools = LogTools(FakeBackend(case.get("rows", []), case.get("points", [])), clock=lambda: NOW)
                if "error_contains" in case:
                    with self.assertRaises(ToolError) as caught:
                        getattr(tools, case["tool"])(**case["arguments"])
                    self.assertIn(case["error_contains"], str(caught.exception))
                    continue
                result = json.loads(json.dumps(getattr(tools, case["tool"])(**case["arguments"]), default=str))
                self.assertTrue(case["expect"], "a case needs at least one expected value")
                for path, op, expected in case["expect"]:
                    actual = lookup(result, path)
                    if op == "=":
                        self.assertEqual(actual, expected, f"{case['id']} {path}")
                    elif op == "contains":
                        self.assertIn(expected, str(actual), f"{case['id']} {path}")
                    elif op == "excludes":
                        self.assertNotIn(expected, str(actual), f"{case['id']} {path}")
                    else:
                        self.fail(f"unknown operator {op}")

    def test_every_log_tool_is_covered_by_a_case(self):
        self.assertEqual({case["tool"] for case in CASES}, log_tools.LOG_TOOL_NAMES)

    def test_document_marks_every_live_test_as_pending_and_forbids_answer_only_passes(self):
        text = DOC.read_text(encoding="utf-8")
        sections = [x for x in re.split(r"^### ", text, flags=re.M)[1:] if re.match(r"L\d+\.", x)]
        self.assertEqual(len(sections), len(CASES))
        for section in sections:
            self.assertIn("PENDING MANUAL EXECUTION", section, f"{section.splitlines()[0]} is not marked pending")
        self.assertIn("plausible answer alone", text)
        self.assertNotRegex(text, r"(?m)^\|[^|]+\|\s*(Pass|Passed|Fail|Failed)\s*\|")

    def test_questions_do_not_leak_into_the_application_or_server(self):
        questions = re.findall(r'^\*\*Ask:\*\* "(.+)"$', DOC.read_text(encoding="utf-8"), re.M)
        self.assertTrue(questions)
        for path in [*ROOT.glob("*.tf"), ROOT / "scripts" / "ai-lab-register-vuln-mcp.sh", *(ROOT / "lambda" / "vuln_mcp").glob("*.py")]:
            code = path.read_text(encoding="utf-8")
            for question in questions:
                self.assertNotIn(question, code, f"{question!r} appears in {path.name}")


if __name__ == "__main__":
    unittest.main()
