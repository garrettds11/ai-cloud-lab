"""Read-only tools over the vulnerability findings DynamoDB table.

This module knows nothing about MCP or HTTP. It holds the tool definitions (name,
description, input schema) and their implementations, so the protocol layer
(mcp_protocol.py) stays generic and the tools can be tested without AWS.

Table design (created by the sec-data repo's create_vuln_table.py):
  key                        host_name (full name) + finding_id ("<vulnerability_id>#<proto>/<port>")
  vuln-host-index            vulnerability_id + host_name
  severity-lastseen-index    vulnerability_severity + last_seen

Each tool maps to one access pattern. summarize_findings and the host/vulnerability
lists read the whole table with a projected Scan, which is right at lab scale (tens or
hundreds of findings). A much larger table would need precomputed rollups instead.
"""

import difflib
import os
import re
import time
from decimal import Decimal
from functools import reduce

TABLE_NAME = os.environ.get("TABLE_NAME", "aiwebdemo-vuln-findings")
INDEX_SEVERITY = "severity-lastseen-index"
INDEX_VULNERABILITY = "vuln-host-index"

DEFAULT_LIMIT = 20
MAX_LIMIT = 50
DEFAULT_GROUP_LIMIT = 25
CATALOG_CACHE_SECONDS = 300
MAX_QUERY_PAGES = 20  # one host, vulnerability or severity is far below this
MAX_SCAN_PAGES = 50  # about 50 MB; beyond that the question must be narrowed

SEVERITIES = ["Info", "Low", "Medium", "High", "Critical"]  # lowest to highest
SEVERITY_RANK = {name: rank for rank, name in enumerate(SEVERITIES)}
STATUSES = ["open", "fixed", "reopened", "unknown"]
STATUS_CHOICES = ["active", "any"] + STATUSES  # "active" = open or reopened
STATUS_ORDER = {"open": 0, "reopened": 1, "unknown": 2, "fixed": 3}
ENVIRONMENTS = ["prod", "stage", "dev"]
GROUP_FIELDS = {
    "severity": "vulnerability_severity",
    "status": "vulnerability_status",
    "environment": "environment",
    "host": "host_name",
    "vulnerability": "vulnerability_id",
    "exploit_available": "exploit_available",
}
SINCE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?Z?)?$")


class ToolError(Exception):
    """A problem the model can act on (bad argument, unknown host). Shown to the model."""


def _plain(value):
    """DynamoDB returns Decimal; JSON wants int or float. Works through lists and maps."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def _score(value):
    """CVSS scores are always floats, so 10.0 is not shown as 10 beside 9.8."""
    return None if value is None else float(value)


def _short(host_name):
    return (host_name or "").split(".")[0]


def _collect(call, kwargs, max_pages):
    """Read every page of a Query or Scan, or fail loudly instead of returning a partial answer."""
    items = []
    for _ in range(max_pages):
        page = call(**kwargs)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            return items
        kwargs = {**kwargs, "ExclusiveStartKey": page["LastEvaluatedKey"]}
    raise ToolError("Too many records to read in one call. Narrow the question with filters.")


# --------------------------------------------------------------------------
# DynamoDB access (the only code that talks to AWS)
# --------------------------------------------------------------------------
class DynamoRepo:
    def __init__(self, table_name=None, resource=None):
        import boto3  # imported here so the rest of the module works without boto3

        self._table = (resource or boto3.resource("dynamodb")).Table(table_name or TABLE_NAME)

    @staticmethod
    def _filter(statuses=None, severities=None, environment=None, exploit=None):
        from boto3.dynamodb.conditions import Attr

        conditions = []
        if statuses:
            conditions.append(Attr("vulnerability_status").is_in(list(statuses)))
        if severities:
            conditions.append(Attr("vulnerability_severity").is_in(list(severities)))
        if environment:
            conditions.append(Attr("environment").eq(environment))
        if exploit is not None:
            conditions.append(Attr("exploit_available").eq(exploit))
        return reduce(lambda a, b: a & b, conditions) if conditions else None

    def scan_catalog(self):
        """Host, vulnerability id and title of every finding (used to learn the hosts and vulnerabilities)."""
        return _collect(self._table.scan, {
            "ProjectionExpression": "#h, #v, #t",
            "ExpressionAttributeNames": {"#h": "host_name", "#v": "vulnerability_id", "#t": "title"},
        }, MAX_SCAN_PAGES)

    def scan_findings(self, statuses=None, severities=None, environment=None, exploit=None):
        """The few fields needed for counting, for every finding that matches."""
        kwargs = {
            "ProjectionExpression": "#a, #b, #c, #d, #e, #f",
            "ExpressionAttributeNames": {
                "#a": "host_name", "#b": "vulnerability_id", "#c": "vulnerability_severity",
                "#d": "vulnerability_status", "#e": "environment", "#f": "exploit_available",
            },
        }
        condition = self._filter(statuses, severities, environment, exploit)
        if condition is not None:
            kwargs["FilterExpression"] = condition
        return _collect(self._table.scan, kwargs, MAX_SCAN_PAGES)

    def query_host(self, host_name, statuses=None, severities=None):
        """All findings for one host."""
        from boto3.dynamodb.conditions import Key

        kwargs = {"KeyConditionExpression": Key("host_name").eq(host_name)}
        condition = self._filter(statuses, severities)
        if condition is not None:
            kwargs["FilterExpression"] = condition
        return _collect(self._table.query, kwargs, MAX_QUERY_PAGES)

    def query_finding(self, host_name, finding_id_prefix):
        """Full records on one host whose finding_id starts with the prefix."""
        from boto3.dynamodb.conditions import Key

        key = Key("host_name").eq(host_name) & Key("finding_id").begins_with(finding_id_prefix)
        return _collect(self._table.query, {"KeyConditionExpression": key}, MAX_QUERY_PAGES)

    def query_vulnerability(self, vulnerability_id, statuses=None):
        """Every finding of one vulnerability, across hosts."""
        from boto3.dynamodb.conditions import Key

        kwargs = {
            "IndexName": INDEX_VULNERABILITY,
            "KeyConditionExpression": Key("vulnerability_id").eq(vulnerability_id),
        }
        condition = self._filter(statuses)
        if condition is not None:
            kwargs["FilterExpression"] = condition
        return _collect(self._table.query, kwargs, MAX_QUERY_PAGES)

    def query_severity(self, severity, since=None, statuses=None, exploit=None, environment=None):
        """Findings of one severity, newest first, optionally only those seen since a date."""
        from boto3.dynamodb.conditions import Key

        key = Key("vulnerability_severity").eq(severity)
        if since:
            key = key & Key("last_seen").gte(since)
        kwargs = {"IndexName": INDEX_SEVERITY, "KeyConditionExpression": key, "ScanIndexForward": False}
        condition = self._filter(statuses, None, environment, exploit)
        if condition is not None:
            kwargs["FilterExpression"] = condition
        return _collect(self._table.query, kwargs, MAX_QUERY_PAGES)


# --------------------------------------------------------------------------
# Output shaping
# --------------------------------------------------------------------------
def _row(item, keys):
    """A short, flat view of a finding with only the listed keys, in that order."""
    full = {
        "finding_id": item.get("finding_id"),
        "host": _short(item.get("host_name")),
        "environment": item.get("environment"),
        "vulnerability_id": item.get("vulnerability_id"),
        "title": item.get("title") or item.get("vulnerability_description"),
        "severity": item.get("vulnerability_severity"),
        "cvss_score": _score(item.get("vulnerability_score_base")),
        "status": item.get("vulnerability_status"),
        "exploit_available": item.get("exploit_available"),
        "port": _plain(item.get("network_port")),
        "last_seen": item.get("last_seen"),
    }
    return {key: full[key] for key in keys}


HOST_FINDING_KEYS = ["finding_id", "vulnerability_id", "title", "severity", "cvss_score", "status", "exploit_available", "port", "last_seen"]
VULNERABILITY_HOST_KEYS = ["host", "environment", "status", "severity", "cvss_score", "exploit_available", "port", "last_seen"]
LIST_KEYS = ["host", "environment", "vulnerability_id", "title", "severity", "cvss_score", "status", "exploit_available", "last_seen"]

DATA_DICTIONARY = {
    "about": (
        "Synthetic vulnerability-scan findings. One record is one vulnerability found on one host. "
        "Severity comes from the CVSS base score: Critical 9.0-10.0, High 7.0-8.9, Medium 4.0-6.9, "
        "Low 0.1-3.9, Info 0."
    ),
    "severity_order_lowest_to_highest": SEVERITIES,
    "status_values": {
        "open": "currently present",
        "fixed": "was found before, no longer present",
        "reopened": "had been fixed, found again",
        "unknown": "state not known",
        "active": "search shortcut meaning open or reopened (current problems)",
        "any": "search shortcut meaning every status",
    },
    "environments": ENVIRONMENTS,
    "host_names": "Hosts look like prod-app-01, dc-01, bastion-01. Use the short name.",
    "fields": {
        "host_name": "full host name (the short name is accepted everywhere)",
        "environment": "prod, stage or dev",
        "host_ip / host_mac / host_os_full": "network address, hardware address, operating system",
        "network_port / network_protocol / service_name": "where the flaw was found (port 0 = local, not network)",
        "vulnerability_id": "CVE id such as CVE-2021-44228, or PLUGIN-<n> for scanner-only findings",
        "title": "short name of the vulnerability",
        "vulnerability_severity": "Info, Low, Medium, High or Critical",
        "vulnerability_score_base": "CVSS base score 0.0-10.0 (cvss_vector holds the vector string)",
        "cwe_id": "weakness category, such as CWE-502",
        "epss_score": "estimated probability of exploitation in the wild, 0-1 (illustrative)",
        "exploit_available": "true if public exploit code exists",
        "vulnerability_status": "open, fixed or reopened",
        "first_seen / last_seen": "UTC timestamps of first and most recent detection",
        "vulnerability_scanner_vendor / vulnerability_report_id / scan_type / scan_network": "which scan reported it",
    },
}


class VulnTools:
    def __init__(self, repo=None, clock=time.monotonic):
        self._repo = repo
        self._clock = clock
        self._catalog_cache = None
        self._catalog_cached_at = 0.0

    @property
    def repo(self):
        if self._repo is None:
            self._repo = DynamoRepo()
        return self._repo

    # ---- hosts and vulnerabilities that exist ----
    def _catalog(self):
        """(sorted host names, {vulnerability_id: title}), cached for a few minutes."""
        now = self._clock()
        if self._catalog_cache is None or now - self._catalog_cached_at > CATALOG_CACHE_SECONDS:
            hosts, vulnerabilities = set(), {}
            for item in self.repo.scan_catalog():
                hosts.add(item["host_name"])
                if item.get("vulnerability_id"):
                    vulnerabilities[item["vulnerability_id"]] = item.get("title") or ""
            self._catalog_cache = (sorted(hosts), vulnerabilities)
            self._catalog_cached_at = now
        return self._catalog_cache

    def resolve_host(self, name):
        """Accept the short name (prod-app-01) or the full name; return the full key."""
        if not isinstance(name, str) or not name.strip():
            raise ToolError("host is required. Example: prod-app-01")
        wanted = name.strip().lower()
        hosts = self._catalog()[0]

        for host in hosts:
            if host.lower() == wanted:
                return host
        by_short = [h for h in hosts if h.lower().split(".")[0] == wanted]
        if len(by_short) == 1:
            return by_short[0]
        if len(by_short) > 1:
            raise ToolError(f"'{name}' matches several hosts: {', '.join(by_short)}. Use the full name.")
        by_prefix = [h for h in hosts if h.lower().startswith(wanted)]
        if len(by_prefix) == 1:
            return by_prefix[0]

        shorts = sorted({_short(h) for h in hosts})
        close = difflib.get_close_matches(wanted, [s.lower() for s in shorts], n=3, cutoff=0.6)
        close = [s for s in shorts if s.lower() in close]
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        raise ToolError(f"Unknown host '{name}'.{hint} Call get_data_dictionary to see the host list.")

    def resolve_vulnerability(self, text):
        """Accept an id (CVE-2021-44228) or part of a name (Log4Shell); return the id."""
        if not isinstance(text, str) or not text.strip():
            raise ToolError("vulnerability_id is required. Example: CVE-2021-44228 or Log4Shell")
        known = self._catalog()[1]
        wanted = text.strip()
        if wanted.upper() in known:
            return wanted.upper()
        needle = wanted.lower()
        matches = [vid for vid, title in known.items() if needle in vid.lower() or needle in title.lower()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            listing = "; ".join(f"{vid} ({known[vid]})" for vid in sorted(matches)[:6])
            raise ToolError(f"'{text}' matches several vulnerabilities: {listing}. Use the exact id.")
        raise ToolError(f"No vulnerability matching '{text}' is in the data. Call get_data_dictionary to see the list.")

    # ---- argument checks ----
    @staticmethod
    def _statuses(status):
        value = "active" if status in (None, "") else str(status).strip().lower()
        if value == "active":
            return value, ["open", "reopened"]
        if value == "any":
            return value, None
        if value in STATUSES:
            return value, [value]
        raise ToolError(f"Invalid status '{status}'. Use one of: {', '.join(STATUS_CHOICES)}.")

    @staticmethod
    def _severity(value, argument):
        if value in (None, ""):
            return None
        wanted = str(value).strip().lower()
        for name in SEVERITIES:
            if name.lower() == wanted:
                return name
        raise ToolError(f"Invalid {argument} '{value}'. Use one of: {', '.join(SEVERITIES)}.")

    @staticmethod
    def _limit(limit, default=DEFAULT_LIMIT):
        if limit in (None, ""):
            return default
        try:
            value = int(limit)
        except (TypeError, ValueError):
            raise ToolError(f"limit must be a whole number from 1 to {MAX_LIMIT}.") from None
        return max(1, min(value, MAX_LIMIT))

    @staticmethod
    def _environment(value):
        if value in (None, ""):
            return None
        wanted = str(value).strip().lower()
        if wanted in ENVIRONMENTS:
            return wanted
        raise ToolError(f"Invalid environment '{value}'. Use one of: {', '.join(ENVIRONMENTS)}.")

    @staticmethod
    def _boolean(value, argument):
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("true", "yes", "1"):
            return True
        if text in ("false", "no", "0"):
            return False
        raise ToolError(f"{argument} must be true or false.")

    @staticmethod
    def _since(value):
        if value in (None, ""):
            return None
        text = str(value).strip()
        if not SINCE_PATTERN.match(text):
            raise ToolError("since must be a date such as 2026-09-20 (or 2026-09-20T12:00:00Z).")
        return text

    def _severity_range(self, severity, min_severity):
        exact = self._severity(severity, "severity")
        minimum = self._severity(min_severity, "min_severity")
        if exact and minimum:
            raise ToolError("Use either severity or min_severity, not both.")
        if exact:
            return exact, None, [exact]
        if minimum:
            return None, minimum, SEVERITIES[SEVERITY_RANK[minimum]:]
        return None, None, list(SEVERITIES)

    # ---- tools ----
    def get_data_dictionary(self):
        result = dict(DATA_DICTIONARY)
        result["tools"] = [tool["name"] for tool in TOOLS]
        try:
            hosts, vulnerabilities = self._catalog()
            result["hosts"] = sorted({_short(h) for h in hosts})
            result["vulnerabilities"] = [f"{vid}: {title}" for vid, title in sorted(vulnerabilities.items())][:60]
        except Exception as error:  # the static part is still useful without the table
            result["hosts"] = f"unavailable ({type(error).__name__})"
        return result

    def get_host_findings(self, host, status=None, min_severity=None, limit=None):
        fqdn = self.resolve_host(host)
        status_label, statuses = self._statuses(status)
        minimum = self._severity(min_severity, "min_severity")
        severities = SEVERITIES[SEVERITY_RANK[minimum]:] if minimum else None
        limit = self._limit(limit)

        rows = [_row(item, HOST_FINDING_KEYS) for item in self.repo.query_host(fqdn, statuses, severities)]
        rows.sort(key=lambda r: (-SEVERITY_RANK.get(r["severity"], -1), -(r["cvss_score"] or 0), r["vulnerability_id"] or ""))
        page = rows[:limit]
        return {
            "host": fqdn,
            "filters": {"status": status_label, "min_severity": minimum},
            "total_matching": len(rows),
            "returned": len(page),
            "truncated": len(rows) > len(page),
            "findings": page,
        }

    def find_hosts_by_vulnerability(self, vulnerability_id, status=None, limit=None):
        vulnerability = self.resolve_vulnerability(vulnerability_id)
        status_label, statuses = self._statuses(status)
        limit = self._limit(limit)

        items = self.repo.query_vulnerability(vulnerability, statuses)
        rows = [_row(item, VULNERABILITY_HOST_KEYS) for item in items]
        rows.sort(key=lambda r: (STATUS_ORDER.get(r["status"], 9), r["host"]))
        page = rows[:limit]
        return {
            "vulnerability_id": vulnerability,
            "title": self._catalog()[1].get(vulnerability, ""),
            "filters": {"status": status_label},
            "total_matching": len(rows),
            "returned": len(page),
            "truncated": len(rows) > len(page),
            "hosts": page,
        }

    def list_findings(self, severity=None, min_severity=None, status=None, exploit_available=None,
                      since=None, environment=None, limit=None):
        exact, minimum, severities = self._severity_range(severity, min_severity)
        status_label, statuses = self._statuses(status)
        exploit = self._boolean(exploit_available, "exploit_available")
        since = self._since(since)
        environment = self._environment(environment)
        limit = self._limit(limit)

        items = []
        for level in severities:
            items.extend(self.repo.query_severity(level, since, statuses, exploit, environment))
        rows = [_row(item, LIST_KEYS) for item in items]
        rows.sort(key=lambda r: r["host"])
        rows.sort(key=lambda r: r["last_seen"] or "", reverse=True)
        rows.sort(key=lambda r: SEVERITY_RANK.get(r["severity"], -1), reverse=True)
        page = rows[:limit]
        return {
            "filters": {
                "severity": exact, "min_severity": minimum, "status": status_label,
                "exploit_available": exploit, "since": since, "environment": environment,
            },
            "total_matching": len(rows),
            "returned": len(page),
            "truncated": len(rows) > len(page),
            "findings": page,
        }

    def get_finding(self, host, vulnerability_id):
        fqdn = self.resolve_host(host)
        text = str(vulnerability_id or "").strip()
        if "#" in text:  # a finding_id such as CVE-2021-44228#tcp/8080
            prefix, label = text, text
        else:
            label = self.resolve_vulnerability(text)
            prefix = label + "#"
        items = self.repo.query_finding(fqdn, prefix)
        if not items:
            raise ToolError(
                f"No finding {label} on {_short(fqdn)}. Use find_hosts_by_vulnerability to see which hosts have it."
            )
        findings = [dict(sorted(_plain(item).items())) for item in items]
        return {"host": fqdn, "count": len(findings), "findings": findings}

    def summarize_findings(self, group_by, status=None, min_severity=None, environment=None,
                           exploit_available=None, limit=None):
        group = str(group_by or "").strip().lower()
        if group not in GROUP_FIELDS:
            raise ToolError(f"Invalid group_by '{group_by}'. Use one of: {', '.join(GROUP_FIELDS)}.")
        status_label, statuses = self._statuses(status)
        minimum = self._severity(min_severity, "min_severity")
        severities = SEVERITIES[SEVERITY_RANK[minimum]:] if minimum else None
        environment = self._environment(environment)
        exploit = self._boolean(exploit_available, "exploit_available")
        limit = self._limit(limit, DEFAULT_GROUP_LIMIT)

        items = self.repo.scan_findings(statuses, severities, environment, exploit)
        field = GROUP_FIELDS[group]
        counts = {}
        for item in items:
            value = item.get(field)
            value = _short(value) if group == "host" else str(value).lower() if isinstance(value, bool) else value
            counts[value] = counts.get(value, 0) + 1

        groups = [{"value": value, "count": count} for value, count in counts.items()]
        groups.sort(key=lambda g: str(g["value"]))
        if group == "severity":
            groups.sort(key=lambda g: SEVERITY_RANK.get(g["value"], -1), reverse=True)
        elif group == "status":
            groups.sort(key=lambda g: STATUS_ORDER.get(g["value"], 9))
        else:
            groups.sort(key=lambda g: g["count"], reverse=True)
        page = groups[:limit]
        if group == "vulnerability":
            titles = self._catalog()[1]
            for entry in page:
                entry["title"] = titles.get(entry["value"], "")
        return {
            "group_by": group,
            "filters": {
                "status": status_label, "min_severity": minimum,
                "environment": environment, "exploit_available": exploit,
            },
            "total_findings": len(items),
            "group_count": len(groups),
            "returned": len(page),
            "truncated": len(groups) > len(page),
            "groups": page,
        }


def _tool(name, description, properties, required=()):
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": list(required),
            "additionalProperties": False,
        },
    }


def _limit_property(default):
    return {
        "type": "integer",
        "minimum": 1,
        "maximum": MAX_LIMIT,
        "description": f"Maximum rows to return (default {default})",
    }


_STATUS = {
    "type": "string",
    "enum": STATUS_CHOICES,
    "description": "active (default) = open or reopened; any = all statuses",
}
_MIN_SEVERITY = {"type": "string", "enum": SEVERITIES, "description": "Only this severity or higher"}
_ENVIRONMENT = {"type": "string", "enum": ENVIRONMENTS, "description": "Only hosts in this environment"}
_EXPLOIT = {"type": "boolean", "description": "true = only findings with a public exploit"}

TOOLS = [
    _tool(
        "get_data_dictionary",
        "Explains the vulnerability findings data: fields, allowed values, the host list and the vulnerability list. "
        "Call this first when unsure what to ask.",
        {},
    ),
    _tool(
        "get_host_findings",
        "List vulnerability findings for one host, worst first. host is a short name such as prod-app-01. "
        "By default only open and reopened findings are returned.",
        {
            "host": {"type": "string", "description": "Host name, for example prod-app-01"},
            "status": _STATUS,
            "min_severity": _MIN_SEVERITY,
            "limit": _limit_property(DEFAULT_LIMIT),
        },
        required=["host"],
    ),
    _tool(
        "find_hosts_by_vulnerability",
        "Which hosts have a given vulnerability. Accepts a CVE id or a name such as Log4Shell. "
        "By default only open and reopened findings are returned.",
        {
            "vulnerability_id": {"type": "string", "description": "CVE id such as CVE-2021-44228, or a name such as Log4Shell"},
            "status": _STATUS,
            "limit": _limit_property(DEFAULT_LIMIT),
        },
        required=["vulnerability_id"],
    ),
    _tool(
        "list_findings",
        "List findings across all hosts, worst severity first and newest first within a severity. "
        "Use severity for one level, or min_severity for that level and higher. Not for counting: use summarize_findings.",
        {
            "severity": {"type": "string", "enum": SEVERITIES, "description": "Only this exact severity"},
            "min_severity": _MIN_SEVERITY,
            "status": _STATUS,
            "exploit_available": _EXPLOIT,
            "since": {"type": "string", "description": "Only findings last seen on or after this date, such as 2026-09-20"},
            "environment": _ENVIRONMENT,
            "limit": _limit_property(DEFAULT_LIMIT),
        },
    ),
    _tool(
        "get_finding",
        "Full details of one vulnerability on one host: description, CVSS vector, CWE, EPSS, references, scan information.",
        {
            "host": {"type": "string", "description": "Host name, for example prod-app-01"},
            "vulnerability_id": {"type": "string", "description": "CVE id such as CVE-2021-44228, or a name such as Log4Shell"},
        },
        required=["host", "vulnerability_id"],
    ),
    _tool(
        "summarize_findings",
        "Count findings grouped by one field. Use this for how-many questions. group_count is the number of distinct "
        "groups, so group_by host gives the number of hosts affected. By default only open and reopened findings are counted.",
        {
            "group_by": {"type": "string", "enum": list(GROUP_FIELDS), "description": "What to count by"},
            "status": _STATUS,
            "min_severity": _MIN_SEVERITY,
            "environment": _ENVIRONMENT,
            "exploit_available": _EXPLOIT,
            "limit": {
                "type": "integer", "minimum": 1, "maximum": MAX_LIMIT,
                "description": f"Maximum groups to return (default {DEFAULT_GROUP_LIMIT})",
            },
        },
        required=["group_by"],
    ),
]
TOOL_NAMES = {tool["name"] for tool in TOOLS}
