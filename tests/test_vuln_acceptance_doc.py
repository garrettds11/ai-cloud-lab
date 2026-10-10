"""Keeps docs/vuln-mcp-acceptance-tests.md honest, offline.

The acceptance document tells a person what to ask Open WebUI and what answer to expect. Those
expectations must come from the repository's test fixture (ITEMS in test_vuln_mcp.py), not from
memory. Each question in the document carries a machine-readable block:

    <!-- acceptance: {"id": "Q1", "tool": "...", "arguments": {...}, "expect": [["path", value], ...]} -->

This test runs that tool call against the fixture, using the real VulnTools code, and checks every
expected value. It also checks that the committed DynamoDB load files match the fixture, so the
table a person loads is the table the expectations were computed from.

Regenerate the committed files after changing the fixture:

    python tests/test_vuln_acceptance_doc.py --write

This is an offline test of the document and the fixture. The questions themselves are run by a
person against a deployment; nothing here talks to Open WebUI, a model or AWS.
"""

import json
import pathlib
import re
import sys
import unittest
from decimal import Decimal

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lambda" / "vuln_mcp"))
sys.path.insert(0, str(ROOT / "tests"))

import vuln_tools  # noqa: E402
from test_vuln_mcp import ITEMS, FakeRepo  # noqa: E402
from vuln_tools import ToolError, VulnTools  # noqa: E402

DOC = ROOT / "docs" / "vuln-mcp-acceptance-tests.md"
FIXTURE = ROOT / "docs" / "vuln-acceptance-fixture.json"
TABLE = ROOT / "docs" / "vuln-acceptance-table.json"
TABLE_NAME = "aiwebdemo-vuln-acceptance"


def to_dynamodb(value):
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, (int, float, Decimal)):
        return {"N": str(value)}
    if isinstance(value, str):
        return {"S": value}
    if isinstance(value, list):
        return {"L": [to_dynamodb(v) for v in value]}
    raise TypeError(type(value))


def build_fixture():
    # Sorted so the committed file is stable.
    items = sorted(ITEMS, key=lambda i: (i["host_name"], i["finding_id"]))
    return {TABLE_NAME: [{"PutRequest": {"Item": {k: to_dynamodb(v) for k, v in i.items()}}} for i in items]}


def build_table():
    return {
        "TableName": TABLE_NAME,
        "BillingMode": "PAY_PER_REQUEST",
        "AttributeDefinitions": [
            {"AttributeName": name, "AttributeType": "S"}
            for name in ("host_name", "finding_id", "vulnerability_severity", "last_seen", "vulnerability_id")
        ],
        "KeySchema": [
            {"AttributeName": "host_name", "KeyType": "HASH"},
            {"AttributeName": "finding_id", "KeyType": "RANGE"},
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": vuln_tools.INDEX_SEVERITY,
                "KeySchema": [
                    {"AttributeName": "vulnerability_severity", "KeyType": "HASH"},
                    {"AttributeName": "last_seen", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": vuln_tools.INDEX_VULNERABILITY,
                "KeySchema": [
                    {"AttributeName": "vulnerability_id", "KeyType": "HASH"},
                    {"AttributeName": "host_name", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
    }


def render(data):
    return json.dumps(data, indent=2) + "\n"


def lookup(result, path):
    """'groups.0.count' -> result['groups'][0]['count']"""
    for part in path.split("."):
        result = result[int(part)] if isinstance(result, list) else result[part]
    return result


def doc_blocks():
    text = DOC.read_text(encoding="utf-8")
    return [json.loads(m) for m in re.findall(r"<!-- acceptance: (\{.*?\}) -->", text)]


class AcceptanceDocTest(unittest.TestCase):
    def test_committed_load_files_match_the_fixture(self):
        self.assertEqual(FIXTURE.read_text(encoding="utf-8"), render(build_fixture()),
                         "run: python tests/test_vuln_acceptance_doc.py --write")
        self.assertEqual(TABLE.read_text(encoding="utf-8"), render(build_table()),
                         "run: python tests/test_vuln_acceptance_doc.py --write")

    def test_fixture_fits_one_batch_write(self):
        self.assertLessEqual(len(build_fixture()[TABLE_NAME]), 25)

    def test_every_question_has_a_block_and_the_ids_are_unique(self):
        blocks = doc_blocks()
        ids = [b["id"] for b in blocks]
        self.assertGreaterEqual(len(blocks), 8)
        self.assertEqual(len(ids), len(set(ids)))
        headings = re.findall(r"^### (Q\d+)\b", DOC.read_text(encoding="utf-8"), re.M)
        self.assertEqual(headings, ids)

    def test_expected_values_are_what_the_real_tools_return_for_the_fixture(self):
        tools = VulnTools(FakeRepo())
        for block in doc_blocks():
            with self.subTest(question=block["id"]):
                self.assertIn(block["tool"], vuln_tools.TOOL_NAMES)
                if "error_contains" in block:
                    with self.assertRaises(ToolError) as caught:
                        getattr(tools, block["tool"])(**block["arguments"])
                    self.assertIn(block["error_contains"], str(caught.exception))
                    continue
                result = json.loads(json.dumps(getattr(tools, block["tool"])(**block["arguments"]), default=str))
                self.assertTrue(block["expect"], "a question needs at least one expected value")
                for path, expected in block["expect"]:
                    self.assertEqual(lookup(result, path), expected, f"{block['id']} {path}")

    def test_document_marks_every_live_test_as_pending_and_forbids_answer_only_passes(self):
        text = DOC.read_text(encoding="utf-8")
        sections = [x for x in re.split(r"^### ", text, flags=re.M)[1:] if re.match(r"[SQBA]\d+\.", x)]
        self.assertEqual(len(sections), 15)
        for section in sections:
            title = section.splitlines()[0]
            self.assertIn("PENDING MANUAL EXECUTION", section, f"{title} is not marked pending")
        self.assertIn("plausible answer alone", text)
        self.assertRegex(text, r"(?m)^\| Q1 \| Pending ")  # no result has been recorded yet
        self.assertNotRegex(text, r"(?m)^\|[^|]+\|\s*(Pass|Passed|Fail|Failed)\s*\|")

    def test_questions_do_not_leak_into_the_application_or_server(self):
        questions = re.findall(r'^\*\*Ask:\*\* "(.+)"$', DOC.read_text(encoding="utf-8"), re.M)
        self.assertTrue(questions)
        for path in [*ROOT.glob("*.tf"), ROOT / "cloud-init.sh.tpl", ROOT / "scripts" / "ai-lab-register-vuln-mcp.sh",
                     *(ROOT / "lambda" / "vuln_mcp").glob("*.py")]:
            code = path.read_text(encoding="utf-8")
            for question in questions:
                self.assertNotIn(question, code, f"{question!r} appears in {path.name}")


if __name__ == "__main__":
    if "--write" in sys.argv:
        FIXTURE.write_text(render(build_fixture()), encoding="utf-8")
        TABLE.write_text(render(build_table()), encoding="utf-8")
        print(f"wrote {FIXTURE.relative_to(ROOT)} and {TABLE.relative_to(ROOT)}")
    else:
        unittest.main()
