"""Offline tests for the vulnerability MCP server in lambda/vuln_mcp.

Run: python -m unittest discover -s tests

The tool, protocol and handler tests use an in-memory fake table and need only the
standard library. DynamoRepoTest checks the real DynamoDB queries against a mock table
and is skipped unless moto is installed (pip install "moto[dynamodb]").
"""

import base64
import json
import os
import pathlib
import sys
import unittest
from decimal import Decimal
from unittest import mock

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "lambda" / "vuln_mcp"))

import mcp_handler  # noqa: E402
import mcp_protocol  # noqa: E402
import vuln_tools  # noqa: E402
from vuln_tools import ToolError, VulnTools  # noqa: E402

DOMAIN = ".corp.example.local"
TOOL_NAMES = [
    "get_data_dictionary", "get_host_findings", "find_hosts_by_vulnerability",
    "list_findings", "get_finding", "summarize_findings",
]
TITLES = {
    "CVE-2021-44228": "Apache Log4j2 JNDI Remote Code Execution (Log4Shell)",
    "CVE-2022-22965": "Spring Framework Data Binding Remote Code Execution (Spring4Shell)",
    "CVE-2023-48795": "OpenSSH Terrapin Attack Vulnerability",
    "CVE-2016-2183": "TLS 3DES Birthday Attack (Sweet32)",
    "CVE-2014-0160": "OpenSSL Heartbleed Information Disclosure",
    "CVE-2020-1472": "Netlogon Elevation of Privilege (Zerologon)",
    "PLUGIN-104743": "TLS Version 1.0 Protocol Detection",
}


def make_item(host, vuln, severity="High", score=7.5, status="open", exploit=False,
              last_seen="2026-10-01T02:00:00Z", port=443, environment=None):
    prefix = host.split("-")[0]
    return {
        "host_name": host + DOMAIN,
        "finding_id": f"{vuln}#tcp/{port}",
        "environment": environment or (prefix if prefix in ("prod", "stage", "dev") else "prod"),
        "vulnerability_id": vuln,
        "title": TITLES.get(vuln, f"Title of {vuln}"),
        "vulnerability_description": f"Description of {vuln}",
        "vulnerability_severity": severity,
        "vulnerability_score_base": Decimal(str(score)),
        "cvss_vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
        "vulnerability_reference": [f"https://nvd.nist.gov/vuln/detail/{vuln}"],
        "vulnerability_status": status,
        "exploit_available": exploit,
        "epss_score": Decimal("0.5"),
        "network_port": Decimal(port),
        "last_seen": last_seen,
    }


# Counts used below. Active (open + reopened): Critical 5, High 2, Medium 2, Info 1.
# Open only: Critical 5, Medium 2, Info 1. Fixed: 2. All statuses: 12.
ITEMS = [
    make_item("prod-app-01", "CVE-2021-44228", "Critical", 10.0, "open", True, "2026-09-17T02:48:00Z", 8080),
    make_item("prod-app-01", "CVE-2022-22965", "Critical", 9.8, "open", True, "2026-10-02T02:37:00Z", 8080),
    make_item("prod-app-01", "CVE-2023-48795", "Medium", 5.9, "open", False, "2026-10-01T02:00:00Z", 22),
    make_item("prod-app-01", "CVE-2016-2183", "High", 7.5, "fixed", False, "2026-08-20T02:00:00Z"),
    make_item("prod-app-01", "CVE-2014-0160", "High", 7.5, "reopened", True, "2026-10-02T02:00:00Z"),
    make_item("prod-app-01", "PLUGIN-104743", "Info", 0.0, "open", False),
    make_item("prod-app-02", "CVE-2021-44228", "Critical", 10.0, "open", True, "2026-10-01T02:10:00Z", 8080),
    make_item("prod-app-03", "CVE-2021-44228", "Critical", 10.0, "fixed", True, "2026-08-20T02:00:00Z", 8080),
    make_item("prod-web-02", "CVE-2014-0160", "High", 7.5, "reopened", True, "2026-10-02T02:30:00Z"),
    make_item("dc-01", "CVE-2020-1472", "Critical", 10.0, "open", True, "2026-10-01T02:20:00Z", 445),
    make_item("dev-app-01", "CVE-2022-22965", "Critical", 9.8, "open", True, "2026-09-30T02:00:00Z", 8080),
    make_item("stage-web-01", "CVE-2023-48795", "Medium", 5.9, "open", False, "2026-10-01T03:00:00Z", 22),
]


class FakeRepo:
    """The same interface as DynamoRepo, answered from a list."""

    def __init__(self, items=None):
        self.items = ITEMS if items is None else items
        self.catalog_calls = 0

    @staticmethod
    def _match(item, statuses=None, severities=None, environment=None, exploit=None):
        return (
            (not statuses or item["vulnerability_status"] in statuses)
            and (not severities or item["vulnerability_severity"] in severities)
            and (not environment or item["environment"] == environment)
            and (exploit is None or item["exploit_available"] == exploit)
        )

    def scan_catalog(self):
        self.catalog_calls += 1
        return [{"host_name": i["host_name"], "vulnerability_id": i["vulnerability_id"], "title": i["title"]} for i in self.items]

    def scan_findings(self, statuses=None, severities=None, environment=None, exploit=None):
        return [i for i in self.items if self._match(i, statuses, severities, environment, exploit)]

    def query_host(self, host_name, statuses=None, severities=None):
        return [i for i in self.items if i["host_name"] == host_name and self._match(i, statuses, severities)]

    def query_finding(self, host_name, prefix):
        return [i for i in self.items if i["host_name"] == host_name and i["finding_id"].startswith(prefix)]

    def query_vulnerability(self, vulnerability_id, statuses=None):
        return [i for i in self.items if i["vulnerability_id"] == vulnerability_id and self._match(i, statuses)]

    def query_severity(self, severity, since=None, statuses=None, exploit=None, environment=None):
        found = [
            i for i in self.items
            if i["vulnerability_severity"] == severity
            and (not since or i["last_seen"] >= since)
            and self._match(i, statuses, None, environment, exploit)
        ]
        return sorted(found, key=lambda i: i["last_seen"], reverse=True)


def pairs(result):
    return [(f["host"], f["vulnerability_id"]) for f in result["findings"]]


class HostNameTest(unittest.TestCase):
    def setUp(self):
        self.repo = FakeRepo()
        self.tools = VulnTools(self.repo)

    def test_short_full_and_mixed_case_names_resolve(self):
        for name in ["prod-app-01", "prod-app-01" + DOMAIN, "  PROD-APP-01  "]:
            self.assertEqual(self.tools.resolve_host(name), "prod-app-01" + DOMAIN)

    def test_unknown_host_suggests_close_names(self):
        with self.assertRaises(ToolError) as caught:
            self.tools.resolve_host("prod-app-9")
        self.assertIn("Unknown host", str(caught.exception))
        self.assertIn("prod-app-01", str(caught.exception))

    def test_unknown_host_without_close_match_points_to_dictionary(self):
        with self.assertRaises(ToolError) as caught:
            self.tools.resolve_host("zzz")
        self.assertIn("get_data_dictionary", str(caught.exception))

    def test_empty_host_is_an_error(self):
        for bad in [None, "", "   ", 5]:
            with self.assertRaises(ToolError):
                self.tools.resolve_host(bad)

    def test_same_short_name_in_two_domains_is_ambiguous(self):
        tools = VulnTools(FakeRepo([
            {**make_item("x-01", "CVE-1"), "host_name": "x-01.a.local"},
            {**make_item("x-01", "CVE-1"), "host_name": "x-01.b.local"},
        ]))
        with self.assertRaises(ToolError) as caught:
            tools.resolve_host("x-01")
        self.assertIn("several hosts", str(caught.exception))

    def test_catalog_is_cached_until_it_expires(self):
        now = [0.0]
        tools = VulnTools(self.repo, clock=lambda: now[0])
        tools.resolve_host("prod-app-01")
        tools.resolve_vulnerability("CVE-2021-44228")
        tools.resolve_host("dc-01")
        self.assertEqual(self.repo.catalog_calls, 1)
        now[0] = vuln_tools.CATALOG_CACHE_SECONDS + 1
        tools.resolve_host("dc-01")
        self.assertEqual(self.repo.catalog_calls, 2)


class VulnerabilityNameTest(unittest.TestCase):
    def setUp(self):
        self.tools = VulnTools(FakeRepo())

    def test_id_name_and_fragment_resolve(self):
        for text in ["CVE-2021-44228", "cve-2021-44228", "Log4Shell", "log4j", "  LOG4SHELL "]:
            self.assertEqual(self.tools.resolve_vulnerability(text), "CVE-2021-44228")

    def test_ambiguous_name_lists_the_candidates(self):
        with self.assertRaises(ToolError) as caught:
            self.tools.resolve_vulnerability("remote code execution")
        message = str(caught.exception)
        self.assertIn("several vulnerabilities", message)
        self.assertIn("CVE-2021-44228", message)
        self.assertIn("CVE-2022-22965", message)

    def test_unknown_and_empty(self):
        with self.assertRaises(ToolError) as unknown:
            self.tools.resolve_vulnerability("CVE-1999-0001")
        self.assertIn("get_data_dictionary", str(unknown.exception))
        for bad in [None, "", "  "]:
            with self.assertRaises(ToolError):
                self.tools.resolve_vulnerability(bad)


class HostFindingsTest(unittest.TestCase):
    def setUp(self):
        self.tools = VulnTools(FakeRepo())

    def ids(self, result):
        return [f["vulnerability_id"] for f in result["findings"]]

    def test_default_shows_only_open_and_reopened_worst_first(self):
        result = self.tools.get_host_findings("prod-app-01")
        self.assertEqual(result["filters"], {"status": "active", "min_severity": None})
        self.assertEqual(
            self.ids(result),
            ["CVE-2021-44228", "CVE-2022-22965", "CVE-2014-0160", "CVE-2023-48795", "PLUGIN-104743"],
        )
        self.assertEqual(result["total_matching"], 5)
        self.assertFalse(result["truncated"])

    def test_status_any_and_fixed(self):
        self.assertEqual(self.tools.get_host_findings("prod-app-01", status="any")["total_matching"], 6)
        self.assertEqual(self.ids(self.tools.get_host_findings("prod-app-01", status="FIXED")), ["CVE-2016-2183"])

    def test_min_severity_keeps_that_level_and_above(self):
        result = self.tools.get_host_findings("prod-app-01", min_severity="high")
        self.assertEqual(self.ids(result), ["CVE-2021-44228", "CVE-2022-22965", "CVE-2014-0160"])
        self.assertEqual(result["filters"]["min_severity"], "High")

    def test_limit_truncates_and_reports_totals(self):
        many = [make_item("big-01", f"CVE-2020-{n:04d}", "Medium", 5.0) for n in range(60)]
        tools = VulnTools(FakeRepo(many))
        result = tools.get_host_findings("big-01", limit=10)
        self.assertEqual((result["returned"], result["total_matching"], result["truncated"]), (10, 60, True))
        self.assertEqual(tools.get_host_findings("big-01", limit=999)["returned"], vuln_tools.MAX_LIMIT)
        self.assertEqual(tools.get_host_findings("big-01", limit=0)["returned"], 1)
        self.assertEqual(tools.get_host_findings("big-01")["returned"], vuln_tools.DEFAULT_LIMIT)

    def test_bad_arguments_give_actionable_errors(self):
        with self.assertRaises(ToolError) as status:
            self.tools.get_host_findings("prod-app-01", status="broken")
        self.assertIn("active", str(status.exception))
        with self.assertRaises(ToolError) as severity:
            self.tools.get_host_findings("prod-app-01", min_severity="urgent")
        self.assertIn("Critical", str(severity.exception))
        with self.assertRaises(ToolError):
            self.tools.get_host_findings("prod-app-01", limit="many")

    def test_numbers_are_plain_json_types(self):
        rows = self.tools.get_host_findings("prod-app-01")["findings"]
        self.assertTrue(all(type(r["cvss_score"]) is float for r in rows))
        self.assertIs(type(rows[0]["port"]), int)
        json.dumps(rows)

    def test_host_with_no_matching_findings_returns_an_empty_list(self):
        result = self.tools.get_host_findings("prod-web-02", status="fixed")
        self.assertEqual((result["total_matching"], result["findings"]), (0, []))


class FindHostsByVulnerabilityTest(unittest.TestCase):
    def setUp(self):
        self.tools = VulnTools(FakeRepo())

    def hosts(self, result):
        return [(h["host"], h["status"]) for h in result["hosts"]]

    def test_by_id_defaults_to_active(self):
        result = self.tools.find_hosts_by_vulnerability("CVE-2021-44228")
        self.assertEqual(self.hosts(result), [("prod-app-01", "open"), ("prod-app-02", "open")])
        self.assertEqual(result["title"], TITLES["CVE-2021-44228"])
        self.assertEqual(result["filters"], {"status": "active"})

    def test_by_name_and_any_status_lists_open_before_fixed(self):
        result = self.tools.find_hosts_by_vulnerability("log4shell", status="any")
        self.assertEqual(
            self.hosts(result),
            [("prod-app-01", "open"), ("prod-app-02", "open"), ("prod-app-03", "fixed")],
        )

    def test_fixed_only_and_reopened(self):
        self.assertEqual(self.hosts(self.tools.find_hosts_by_vulnerability("Log4Shell", status="fixed")), [("prod-app-03", "fixed")])
        reopened = self.tools.find_hosts_by_vulnerability("Heartbleed", status="reopened")
        self.assertEqual(self.hosts(reopened), [("prod-app-01", "reopened"), ("prod-web-02", "reopened")])

    def test_rows_are_short_and_carry_environment(self):
        row = self.tools.find_hosts_by_vulnerability("Zerologon")["hosts"][0]
        self.assertEqual(row["host"], "dc-01")
        self.assertEqual(row["environment"], "prod")
        self.assertNotIn("title", row)

    def test_limit_and_errors(self):
        result = self.tools.find_hosts_by_vulnerability("Log4Shell", status="any", limit=1)
        self.assertEqual((result["returned"], result["total_matching"], result["truncated"]), (1, 3, True))
        with self.assertRaises(ToolError):
            self.tools.find_hosts_by_vulnerability("CVE-1999-0001")
        with self.assertRaises(ToolError):
            self.tools.find_hosts_by_vulnerability("Log4Shell", status="broken")


class ListFindingsTest(unittest.TestCase):
    def setUp(self):
        self.tools = VulnTools(FakeRepo())

    def test_one_severity_newest_first(self):
        result = self.tools.list_findings(severity="Critical")
        self.assertEqual(result["total_matching"], 5)
        self.assertEqual(pairs(result), [
            ("prod-app-01", "CVE-2022-22965"), ("dc-01", "CVE-2020-1472"), ("prod-app-02", "CVE-2021-44228"),
            ("dev-app-01", "CVE-2022-22965"), ("prod-app-01", "CVE-2021-44228"),
        ])
        self.assertEqual(result["filters"]["status"], "active")

    def test_min_severity_orders_worst_first_then_newest(self):
        result = self.tools.list_findings(min_severity="High", exploit_available=True)
        self.assertEqual(result["total_matching"], 7)
        severities = [f["severity"] for f in result["findings"]]
        self.assertEqual(severities, ["Critical"] * 5 + ["High"] * 2)
        self.assertEqual(pairs(result)[5:], [("prod-web-02", "CVE-2014-0160"), ("prod-app-01", "CVE-2014-0160")])

    def test_since_environment_and_status_filters(self):
        self.assertEqual(self.tools.list_findings(severity="Critical", since="2026-10-01")["total_matching"], 3)
        self.assertEqual(pairs(self.tools.list_findings(severity="Critical", environment="dev")), [("dev-app-01", "CVE-2022-22965")])
        self.assertEqual(self.tools.list_findings(severity="Critical", status="any")["total_matching"], 6)
        self.assertEqual(self.tools.list_findings(status="fixed")["total_matching"], 2)

    def test_no_filters_returns_everything_active_worst_first(self):
        result = self.tools.list_findings()
        self.assertEqual(result["total_matching"], 10)
        self.assertEqual(result["findings"][0]["severity"], "Critical")
        self.assertEqual(result["findings"][-1]["severity"], "Info")

    def test_exploit_flag_accepts_text_from_small_models(self):
        self.assertEqual(self.tools.list_findings(severity="Medium", exploit_available="false")["total_matching"], 2)
        self.assertEqual(self.tools.list_findings(severity="Medium", exploit_available="true")["total_matching"], 0)

    def test_limit_truncates(self):
        result = self.tools.list_findings(limit=3)
        self.assertEqual((result["returned"], result["total_matching"], result["truncated"]), (3, 10, True))

    def test_bad_arguments(self):
        for kwargs in [
            {"severity": "Critical", "min_severity": "High"},
            {"severity": "urgent"},
            {"environment": "moon"},
            {"since": "last tuesday"},
            {"exploit_available": "maybe"},
            {"status": "broken"},
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ToolError):
                self.tools.list_findings(**kwargs)


class GetFindingTest(unittest.TestCase):
    def setUp(self):
        self.tools = VulnTools(FakeRepo())

    def test_by_name_returns_the_full_record(self):
        result = self.tools.get_finding("prod-app-01", "log4shell")
        self.assertEqual((result["host"], result["count"]), ("prod-app-01" + DOMAIN, 1))
        record = result["findings"][0]
        self.assertEqual(record["finding_id"], "CVE-2021-44228#tcp/8080")
        for field in ("vulnerability_description", "cvss_vector", "vulnerability_reference", "epss_score"):
            self.assertIn(field, record)
        self.assertEqual(record["vulnerability_score_base"], 10)
        json.dumps(result)

    def test_by_finding_id(self):
        result = self.tools.get_finding("prod-app-01", "CVE-2021-44228#tcp/8080")
        self.assertEqual(result["count"], 1)

    def test_missing_combination_points_to_the_search_tool(self):
        with self.assertRaises(ToolError) as caught:
            self.tools.get_finding("prod-app-02", "CVE-2022-22965")
        self.assertIn("find_hosts_by_vulnerability", str(caught.exception))
        with self.assertRaises(ToolError):
            self.tools.get_finding("nope-99", "CVE-2022-22965")
        with self.assertRaises(ToolError):
            self.tools.get_finding("prod-app-01", "")


class SummarizeFindingsTest(unittest.TestCase):
    def setUp(self):
        self.tools = VulnTools(FakeRepo())

    def groups(self, result):
        return [(g["value"], g["count"]) for g in result["groups"]]

    def test_by_severity_is_ordered_worst_first(self):
        result = self.tools.summarize_findings("severity")
        self.assertEqual(self.groups(result), [("Critical", 5), ("High", 2), ("Medium", 2), ("Info", 1)])
        self.assertEqual((result["total_findings"], result["group_count"]), (10, 4))

    def test_open_only_excludes_reopened(self):
        result = self.tools.summarize_findings("severity", status="open")
        self.assertEqual(self.groups(result), [("Critical", 5), ("Medium", 2), ("Info", 1)])

    def test_any_status_and_by_status(self):
        self.assertEqual(self.tools.summarize_findings("severity", status="any")["total_findings"], 12)
        by_status = self.tools.summarize_findings("status", status="any")
        self.assertEqual(self.groups(by_status), [("open", 8), ("reopened", 2), ("fixed", 2)])

    def test_group_count_gives_distinct_hosts(self):
        open_hosts = self.tools.summarize_findings("host", status="open", min_severity="High", exploit_available=True)
        self.assertEqual((open_hosts["group_count"], open_hosts["total_findings"]), (4, 5))
        active_hosts = self.tools.summarize_findings("host", min_severity="High", exploit_available=True)
        self.assertEqual((active_hosts["group_count"], active_hosts["total_findings"]), (5, 7))
        self.assertEqual(active_hosts["groups"][0], {"value": "prod-app-01", "count": 3})

    def test_by_environment_and_exploit_flag(self):
        result = self.tools.summarize_findings("environment", min_severity="Critical")
        self.assertEqual(self.groups(result), [("prod", 4), ("dev", 1)])
        flags = self.tools.summarize_findings("exploit_available")
        self.assertEqual(self.groups(flags), [("true", 7), ("false", 3)])

    def test_by_vulnerability_includes_titles(self):
        result = self.tools.summarize_findings("vulnerability", min_severity="Critical")
        top = result["groups"][0]
        self.assertEqual((top["value"], top["count"], top["title"]), ("CVE-2021-44228", 2, TITLES["CVE-2021-44228"]))

    def test_group_limit_reports_truncation(self):
        result = self.tools.summarize_findings("host", limit=2)
        self.assertEqual((result["returned"], result["truncated"]), (2, True))
        self.assertGreater(result["group_count"], 2)

    def test_bad_arguments(self):
        for kwargs in [{"group_by": "color"}, {"group_by": None}, {"group_by": "host", "status": "broken"}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ToolError):
                self.tools.summarize_findings(**kwargs)


class DataDictionaryTest(unittest.TestCase):
    def test_lists_hosts_vulnerabilities_and_tools(self):
        result = VulnTools(FakeRepo()).get_data_dictionary()
        self.assertEqual(result["hosts"], ["dc-01", "dev-app-01", "prod-app-01", "prod-app-02", "prod-app-03", "prod-web-02", "stage-web-01"])
        self.assertIn("CVE-2021-44228: " + TITLES["CVE-2021-44228"], result["vulnerabilities"])
        self.assertEqual(result["tools"], TOOL_NAMES)
        self.assertEqual(result["severity_order_lowest_to_highest"][-1], "Critical")

    def test_still_works_when_the_table_is_unreachable(self):
        repo = mock.Mock()
        repo.scan_catalog.side_effect = RuntimeError("no table")
        result = VulnTools(repo).get_data_dictionary()
        self.assertTrue(result["hosts"].startswith("unavailable"))
        self.assertIn("about", result)


class PaginationSafetyTest(unittest.TestCase):
    def test_collect_reads_every_page(self):
        pages = [{"Items": [1], "LastEvaluatedKey": "a"}, {"Items": [2], "LastEvaluatedKey": "b"}, {"Items": [3]}]
        call = mock.Mock(side_effect=pages)
        self.assertEqual(vuln_tools._collect(call, {}, 5), [1, 2, 3])
        self.assertEqual(call.call_args_list[1].kwargs["ExclusiveStartKey"], "a")

    def test_collect_refuses_to_return_a_partial_answer(self):
        call = mock.Mock(return_value={"Items": [1], "LastEvaluatedKey": "more"})
        with self.assertRaises(ToolError) as caught:
            vuln_tools._collect(call, {}, 3)
        self.assertIn("Narrow", str(caught.exception))


def rpc(method, params=None, request_id=1):
    message = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        message["id"] = request_id
    if params is not None:
        message["params"] = params
    return message


class ProtocolTest(unittest.TestCase):
    def setUp(self):
        self.tools = VulnTools(FakeRepo())

    def call(self, name, arguments=None):
        return mcp_protocol.handle_message(rpc("tools/call", {"name": name, "arguments": arguments or {}}), self.tools)

    def test_initialize_echoes_a_supported_version_and_falls_back_otherwise(self):
        ok = mcp_protocol.handle_message(rpc("initialize", {"protocolVersion": "2025-06-18"}), self.tools)
        self.assertEqual(ok["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("tools", ok["result"]["capabilities"])
        odd = mcp_protocol.handle_message(rpc("initialize", {"protocolVersion": "1999-01-01"}), self.tools)
        self.assertEqual(odd["result"]["protocolVersion"], mcp_protocol.SUPPORTED_VERSIONS[0])

    def test_notifications_get_no_response(self):
        self.assertIsNone(mcp_protocol.handle_message(rpc("notifications/initialized", request_id=None), self.tools))
        self.assertEqual(mcp_protocol.handle_body(json.dumps(rpc("notifications/initialized", request_id=None)), self.tools), (202, ""))

    def test_ping_and_unknown_method(self):
        self.assertEqual(mcp_protocol.handle_message(rpc("ping"), self.tools)["result"], {})
        self.assertEqual(mcp_protocol.handle_message(rpc("nope"), self.tools)["error"]["code"], mcp_protocol.METHOD_NOT_FOUND)

    def test_tools_list_schemas_are_well_formed(self):
        tools = mcp_protocol.handle_message(rpc("tools/list"), self.tools)["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], TOOL_NAMES)
        for tool in tools:
            self.assertTrue(tool["description"])
            schema = tool["inputSchema"]
            self.assertEqual(schema["type"], "object")
            self.assertTrue(set(schema["required"]) <= set(schema["properties"]))
            for prop in schema["properties"].values():
                self.assertIn("description", prop)
        for tool in tools:  # every tool function accepts exactly the arguments its schema lists
            import inspect

            accepted = set(inspect.signature(getattr(VulnTools, tool["name"])).parameters) - {"self"}
            self.assertEqual(accepted, set(tool["inputSchema"]["properties"]), tool["name"])

    def test_every_tool_is_callable_over_the_protocol(self):
        calls = {
            "get_data_dictionary": {},
            "get_host_findings": {"host": "prod-app-01"},
            "find_hosts_by_vulnerability": {"vulnerability_id": "Log4Shell"},
            "list_findings": {"min_severity": "Critical"},
            "get_finding": {"host": "prod-app-01", "vulnerability_id": "CVE-2021-44228"},
            "summarize_findings": {"group_by": "severity"},
        }
        self.assertEqual(list(calls), TOOL_NAMES)
        for name, arguments in calls.items():
            result = self.call(name, arguments)["result"]
            self.assertNotIn("isError", result, name)
            json.loads(result["content"][0]["text"])

    def test_tool_call_returns_compact_json_text(self):
        result = self.call("get_host_findings", {"host": "prod-app-01", "min_severity": "Critical"})["result"]
        self.assertNotIn(" ", result["content"][0]["text"].split('"findings"')[0])
        self.assertEqual(json.loads(result["content"][0]["text"])["total_matching"], 2)

    def test_tool_errors_are_results_the_model_can_read(self):
        result = self.call("get_host_findings", {"host": "nope-99"})["result"]
        self.assertTrue(result["isError"])
        self.assertIn("Unknown host", result["content"][0]["text"])

    def test_wrong_argument_names_are_a_readable_error(self):
        result = self.call("get_host_findings", {"hostname": "prod-app-01"})["result"]
        self.assertTrue(result["isError"])
        self.assertIn("Invalid arguments", result["content"][0]["text"])

    def test_unexpected_failures_do_not_leak_details(self):
        repo = mock.Mock()
        repo.scan_catalog.return_value = [{"host_name": "prod-app-01" + DOMAIN, "vulnerability_id": "CVE-1", "title": "t"}]
        repo.query_host.side_effect = RuntimeError("secret internal detail")
        result = mcp_protocol.handle_message(
            rpc("tools/call", {"name": "get_host_findings", "arguments": {"host": "prod-app-01"}}), VulnTools(repo)
        )["result"]
        self.assertTrue(result["isError"])
        self.assertNotIn("secret internal detail", result["content"][0]["text"])

    def test_unknown_tool_is_a_protocol_error(self):
        self.assertEqual(self.call("drop_table")["error"]["code"], mcp_protocol.INVALID_PARAMS)

    def test_bad_json_and_bad_requests(self):
        self.assertEqual(mcp_protocol.handle_body("{not json", self.tools)[0], 400)
        self.assertEqual(mcp_protocol.handle_body("[]", self.tools)[0], 400)
        status, text = mcp_protocol.handle_body(json.dumps({"hello": "world"}), self.tools)
        self.assertEqual(json.loads(text)["error"]["code"], mcp_protocol.INVALID_REQUEST)

    def test_batches_return_a_list(self):
        status, text = mcp_protocol.handle_body(json.dumps([rpc("ping", request_id=1), rpc("ping", request_id=2)]), self.tools)
        self.assertEqual((status, [r["id"] for r in json.loads(text)]), (200, [1, 2]))


def event(body=None, method="POST", path="/mcp", token=None, base64_body=False):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    text = json.dumps(body) if body is not None else ""
    if base64_body:
        text = base64.b64encode(text.encode()).decode()
    return {
        "requestContext": {"http": {"method": method, "path": path}},
        "headers": headers,
        "body": text,
        "isBase64Encoded": base64_body,
    }


class HandlerTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("AUTH_TOKEN", "AUTH_TOKEN_SECRET_ARN", "AWS_LAMBDA_FUNCTION_NAME", "REQUIRE_AUTH"):
            os.environ.pop(name, None)
        mcp_handler._tools = VulnTools(FakeRepo())
        mcp_handler._token_cache = None
        self.addCleanup(setattr, mcp_handler, "_tools", None)

    def test_local_mode_needs_no_token(self):
        result = mcp_handler.lambda_handler(event(rpc("tools/list")))
        self.assertEqual(result["statusCode"], 200)
        self.assertEqual(len(json.loads(result["body"])["result"]["tools"]), len(TOOL_NAMES))

    def test_token_is_enforced_when_set(self):
        os.environ["AUTH_TOKEN"] = "s3cret-token"
        self.assertEqual(mcp_handler.lambda_handler(event(rpc("ping")))["statusCode"], 401)
        self.assertEqual(mcp_handler.lambda_handler(event(rpc("ping"), token="wrong"))["statusCode"], 401)
        self.assertEqual(mcp_handler.lambda_handler(event(rpc("ping"), token="s3cret-token"))["statusCode"], 200)

    def test_missing_token_in_lambda_fails_closed(self):
        os.environ["AWS_LAMBDA_FUNCTION_NAME"] = "vuln-mcp"
        self.assertEqual(mcp_handler.lambda_handler(event(rpc("ping")))["statusCode"], 500)

    def test_wrong_method_and_path(self):
        self.assertEqual(mcp_handler.lambda_handler(event(method="GET"))["statusCode"], 405)
        self.assertEqual(mcp_handler.lambda_handler(event(method="DELETE"))["statusCode"], 405)
        self.assertEqual(mcp_handler.lambda_handler(event(rpc("ping"), path="/admin"))["statusCode"], 404)
        self.assertEqual(mcp_handler.lambda_handler(event(rpc("ping"), path="/mcp/"))["statusCode"], 200)

    def test_base64_body_and_notification(self):
        self.assertEqual(mcp_handler.lambda_handler(event(rpc("ping"), base64_body=True))["statusCode"], 200)
        note = mcp_handler.lambda_handler(event(rpc("notifications/initialized", request_id=None)))
        self.assertEqual((note["statusCode"], note["body"]), (202, ""))

    def test_oversized_body_is_rejected(self):
        big = {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"pad": "x" * mcp_handler.MAX_BODY_BYTES}}
        self.assertEqual(mcp_handler.lambda_handler(event(big))["statusCode"], 413)

    def test_secret_text_accepts_plain_and_single_value_json(self):
        self.assertEqual(mcp_handler._secret_text("plain"), "plain")
        self.assertEqual(mcp_handler._secret_text('{"token": "abc"}'), "abc")
        self.assertEqual(mcp_handler._secret_text('"quoted"'), "quoted")


try:
    import boto3
    from moto import mock_aws
except ImportError:  # moto is optional
    mock_aws = None


@unittest.skipIf(mock_aws is None, "moto is not installed")
class DynamoRepoTest(unittest.TestCase):
    """Runs the real boto3 queries, filters and indexes against a mock DynamoDB table."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {
            "AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test", "AWS_DEFAULT_REGION": "us-east-1",
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        mocked = mock_aws()
        mocked.start()
        self.addCleanup(mocked.stop)

        resource = boto3.resource("dynamodb")
        resource.create_table(
            TableName="test-vuln",
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "host_name", "AttributeType": "S"},
                {"AttributeName": "finding_id", "AttributeType": "S"},
                {"AttributeName": "vulnerability_severity", "AttributeType": "S"},
                {"AttributeName": "last_seen", "AttributeType": "S"},
                {"AttributeName": "vulnerability_id", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "host_name", "KeyType": "HASH"},
                {"AttributeName": "finding_id", "KeyType": "RANGE"},
            ],
            GlobalSecondaryIndexes=[
                {"IndexName": vuln_tools.INDEX_SEVERITY,
                 "KeySchema": [{"AttributeName": "vulnerability_severity", "KeyType": "HASH"},
                               {"AttributeName": "last_seen", "KeyType": "RANGE"}],
                 "Projection": {"ProjectionType": "ALL"}},
                {"IndexName": vuln_tools.INDEX_VULNERABILITY,
                 "KeySchema": [{"AttributeName": "vulnerability_id", "KeyType": "HASH"},
                               {"AttributeName": "host_name", "KeyType": "RANGE"}],
                 "Projection": {"ProjectionType": "ALL"}},
            ],
        )
        table = resource.Table("test-vuln")
        for item in ITEMS:
            table.put_item(Item=item)
        self.repo = vuln_tools.DynamoRepo("test-vuln", resource)
        self.tools = VulnTools(self.repo)

    def test_catalog_scan_returns_hosts_and_titles(self):
        rows = self.repo.scan_catalog()
        self.assertEqual(len(rows), len(ITEMS))
        self.assertEqual({r["host_name"] for r in rows} >= {"dc-01" + DOMAIN, "prod-app-01" + DOMAIN}, True)
        self.assertEqual(self.tools.resolve_vulnerability("Zerologon"), "CVE-2020-1472")

    def test_query_host_filters_on_status_and_severity(self):
        host = "prod-app-01" + DOMAIN
        self.assertEqual(len(self.repo.query_host(host)), 6)
        self.assertEqual(len(self.repo.query_host(host, statuses=["open", "reopened"])), 5)
        both = self.repo.query_host(host, statuses=["open"], severities=["Critical"])
        self.assertEqual(sorted(i["vulnerability_id"] for i in both), ["CVE-2021-44228", "CVE-2022-22965"])

    def test_query_finding_uses_the_sort_key_prefix(self):
        host = "prod-app-01" + DOMAIN
        self.assertEqual(len(self.repo.query_finding(host, "CVE-2021-44228#")), 1)
        self.assertEqual(len(self.repo.query_finding(host, "CVE-2021-44228#tcp/8080")), 1)
        self.assertEqual(self.repo.query_finding(host, "CVE-2021-4422#tcp"), [])

    def test_query_vulnerability_uses_the_index(self):
        hosts = [i["host_name"] for i in self.repo.query_vulnerability("CVE-2021-44228")]
        self.assertEqual(hosts, ["prod-app-01" + DOMAIN, "prod-app-02" + DOMAIN, "prod-app-03" + DOMAIN])
        open_hosts = self.repo.query_vulnerability("CVE-2021-44228", statuses=["open"])
        self.assertEqual(len(open_hosts), 2)

    def test_query_severity_is_newest_first_with_since_and_filters(self):
        found = self.repo.query_severity("Critical", since="2026-10-01", statuses=["open"])
        self.assertEqual([(i["host_name"].split(".")[0], i["last_seen"][:16]) for i in found], [
            ("prod-app-01", "2026-10-02T02:37"), ("dc-01", "2026-10-01T02:20"), ("prod-app-02", "2026-10-01T02:10"),
        ])
        self.assertEqual(len(self.repo.query_severity("Critical", environment="dev")), 1)
        self.assertEqual(len(self.repo.query_severity("Critical", exploit=False)), 0)

    def test_scan_findings_filters_and_projects(self):
        found = self.repo.scan_findings(statuses=["open"], severities=["Critical"], exploit=True)
        self.assertEqual(len(found), 5)
        self.assertEqual(set(found[0]), {
            "host_name", "vulnerability_id", "vulnerability_severity", "vulnerability_status", "environment", "exploit_available",
        })

    def test_every_tool_gives_the_same_answer_as_the_fake_table(self):
        fake = VulnTools(FakeRepo())
        for name, arguments in [
            ("get_host_findings", {"host": "prod-app-01", "status": "any"}),
            ("find_hosts_by_vulnerability", {"vulnerability_id": "Log4Shell", "status": "any"}),
            ("list_findings", {"min_severity": "High", "exploit_available": True}),
            ("list_findings", {"severity": "Critical", "since": "2026-10-01", "environment": "prod"}),
            ("get_finding", {"host": "dc-01", "vulnerability_id": "Zerologon"}),
            ("summarize_findings", {"group_by": "host", "status": "open", "min_severity": "High", "exploit_available": True}),
            ("summarize_findings", {"group_by": "severity", "status": "any"}),
        ]:
            with self.subTest(tool=name, arguments=arguments):
                self.assertEqual(getattr(self.tools, name)(**arguments), getattr(fake, name)(**arguments))
        json.dumps(self.tools.get_finding("dc-01", "Zerologon"))


if __name__ == "__main__":
    unittest.main()
