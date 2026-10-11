#!/usr/bin/env python3
"""Builds the Grafana dashboards (grafana/dashboards/*.json) and alert rules (grafana/alerts/alert-rules.json).

The JSON files are what you import (scripts/import-grafana.ps1); this file is how they are made, so a
panel is one line here instead of forty in JSON. After changing it, run:

    python grafana/generate.py

and commit the JSON it writes. tests/test_grafana_dashboards.py fails if the committed JSON no longer
matches what this script produces, and checks every ai_lab_ metric and CloudWatch metric the panels use.

Offline only: none of this has been loaded into a real Grafana yet. The metric names come from
scripts/ai-lab-metrics and the Alloy config; the log labels and Open WebUI's own metric names are the
parts most likely to need a small fix at the first import (see docs/monitoring-spec.md).

Data sources are chosen with dashboard variables (Metrics, Logs, CloudWatch), so the files work in any
Grafana stack. Alert rules carry placeholders (__DS_PROM__, __DS_CW__, __INSTANCE_ID__) that the import
script fills in.
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
PROJECT = "__PROJECT__"  # replaced by scripts/import-grafana.ps1 with project_name, so the dashboards fit any project name

PROM = {"type": "prometheus", "uid": "${ds_prom}"}
LOKI = {"type": "loki", "uid": "${ds_loki}"}
CW = {"type": "cloudwatch", "uid": "${ds_cw}"}

GREEN, AMBER, RED, GREY, BLUE = "green", "orange", "red", "text", "blue"


# ---- building blocks --------------------------------------------------------------------------


def ds_var(name, label, kind):
    return {"type": "datasource", "name": name, "label": label, "query": kind, "current": {}, "hide": 0,
            "refresh": 1, "regex": "", "options": [], "multi": False, "includeAll": False}


def text_var(name, label, default):
    return {"type": "textbox", "name": name, "label": label, "query": default, "current": {"text": default, "value": default},
            "options": [{"selected": True, "text": default, "value": default}], "hide": 0}


def thresholds(*steps):
    """steps: (value or None, colour). The first is the base."""
    return {"mode": "absolute", "steps": [{"value": v, "color": c} for v, c in steps]}


def prom(expr, legend="", ref="A", instant=False):
    return {"datasource": PROM, "refId": ref, "expr": expr, "legendFormat": legend, "instant": instant, "range": not instant}


def cw_sql(sql, label="", ref="A", period=""):
    """CloudWatch Metrics Insights query."""
    return {"datasource": CW, "refId": ref, "queryMode": "Metrics", "metricQueryType": 1, "metricEditorMode": 1,
            "sqlExpression": sql, "region": "default", "id": "", "label": label, "period": period, "expression": "",
            "namespace": "", "metricName": "", "dimensions": {}, "statistic": "Average", "matchExact": True}


def loki(expr, ref="A", kind="range"):
    return {"datasource": LOKI, "refId": ref, "expr": expr, "queryType": kind}


class Dashboard:
    def __init__(self, uid, title, description, variables):
        self.d = {
            "uid": uid, "title": title, "description": description, "tags": ["ai-lab"], "schemaVersion": 39, "version": 1,
            "editable": True, "graphTooltip": 1, "timezone": "browser", "refresh": "1m", "time": {"from": "now-6h", "to": "now"},
            "templating": {"list": variables}, "annotations": {"list": []}, "links": [], "panels": [],
        }
        self.x, self.y, self.row_h, self.next_id = 0, 0, 0, 1

    def add(self, panel, w, h):
        if self.x + w > 24:
            self.x, self.y, self.row_h = 0, self.y + self.row_h, 0
        panel["id"] = self.next_id
        panel["gridPos"] = {"x": self.x, "y": self.y, "w": w, "h": h}
        self.next_id += 1
        self.x += w
        self.row_h = max(self.row_h, h)
        self.d["panels"].append(panel)

    def row(self, title):
        self.x, self.y, self.row_h = 0, self.y + self.row_h, 0
        self.d["panels"].append({"id": self.next_id, "type": "row", "title": title, "collapsed": False,
                                 "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1}, "panels": []})
        self.next_id += 1
        self.y += 1
        self.row_h = 0


def stat(title, targets, unit="short", steps=None, mappings=None, decimals=None, description="", text_mode="auto", color_mode="value"):
    return {"type": "stat", "title": title, "description": description, "targets": targets, "datasource": targets[0]["datasource"],
            "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals, "mappings": mappings or [],
                                         "thresholds": steps or thresholds((None, BLUE)), "color": {"mode": "thresholds"}}, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "colorMode": color_mode,
                        "graphMode": "none", "textMode": text_mode, "justifyMode": "auto"}}


def series(title, targets, unit="short", description="", stack=False, minimum=None, maximum=None):
    custom = {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 12, "showPoints": "never", "spanNulls": False}
    if stack:
        custom["stacking"] = {"mode": "normal", "group": "A"}
        custom["fillOpacity"] = 40
    defaults = {"unit": unit, "custom": custom}
    if minimum is not None:
        defaults["min"] = minimum
    if maximum is not None:
        defaults["max"] = maximum
    return {"type": "timeseries", "title": title, "description": description, "targets": targets, "datasource": targets[0]["datasource"],
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True}, "tooltip": {"mode": "multi", "sort": "desc"}}}


def table(title, targets, description="", transformations=None, overrides=None):
    return {"type": "table", "title": title, "description": description, "targets": targets, "datasource": targets[0]["datasource"],
            "fieldConfig": {"defaults": {"custom": {"align": "auto"}}, "overrides": overrides or []},
            "options": {"showHeader": True, "cellHeight": "sm"}, "transformations": transformations or []}


def logs(title, expr, description=""):
    return {"type": "logs", "title": title, "description": description, "targets": [loki(expr)], "datasource": LOKI,
            "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "enableLogDetails": True, "dedupStrategy": "none"}}


def bar(title, targets, unit="short", description=""):
    return {"type": "bargauge", "title": title, "description": description, "targets": targets, "datasource": targets[0]["datasource"],
            "fieldConfig": {"defaults": {"unit": unit, "thresholds": thresholds((None, BLUE)), "color": {"mode": "thresholds"}}, "overrides": []},
            "options": {"orientation": "horizontal", "displayMode": "gradient", "reduceOptions": {"calcs": ["sum"], "fields": "", "values": False},
                        "showUnfilled": True}}


UP_DOWN = [{"type": "value", "options": {"1": {"text": "Up", "color": GREEN}, "0": {"text": "Down", "color": RED}}},
           {"type": "special", "options": {"match": "null+nan", "result": {"text": "No data", "color": GREY}}}]
OFF_AT_MINUS_ONE = [{"type": "value", "options": {"-1": {"text": "Off", "color": GREY}}}]
OK_BAD = thresholds((None, RED), (1, GREEN))

STANDARD_VARS = [ds_var("ds_prom", "Metrics", "prometheus"), ds_var("ds_loki", "Logs", "loki"), ds_var("ds_cw", "CloudWatch", "cloudwatch")]

# CloudWatch Metrics Insights over the tool metrics that vuln_mcp.tf's metric filters publish.
TOOLS_NS = "AILab/Tools"
BY_TOOL = f'SCHEMA("{TOOLS_NS}", Tool)'


# ---- dashboards -------------------------------------------------------------------------------


def lab_overview():
    d = Dashboard("ai-lab-overview", "AI Lab: Lab overview",
                  "Is the lab up, is the AI working, and what is it costing. Start here for a demo.", STANDARD_VARS)
    d.row("Is it up")
    d.add(stat("Telemetry", [prom("(time() - max(ai_lab_exporter_last_success_timestamp_seconds)) < bool 180", instant=True)], mappings=UP_DOWN,
               steps=OK_BAD, description="Up when the metrics exporter on the instance reported in the last 3 minutes."), 4, 4)
    d.add(stat("Ollama", [prom('max(node_systemd_unit_state{name="ollama.service",state="active"})', instant=True)], mappings=UP_DOWN, steps=OK_BAD), 4, 4)
    d.add(stat("Docker", [prom('max(node_systemd_unit_state{name="docker.service",state="active"})', instant=True)], mappings=UP_DOWN, steps=OK_BAD), 4, 4)
    d.add(stat("Alloy", [prom('max(node_systemd_unit_state{name="alloy.service",state="active"})', instant=True)], mappings=UP_DOWN, steps=OK_BAD), 4, 4)
    d.add(stat("Open WebUI API", [prom('max(ai_lab_exporter_source_up{source="open_webui"})', instant=True)], mappings=UP_DOWN, steps=OK_BAD,
               description="Up when the exporter could sign in to Open WebUI and read its analytics."), 4, 4)
    d.add(stat("GPU", [prom('max(ai_lab_exporter_source_up{source="gpu"})', instant=True)], mappings=UP_DOWN, steps=OK_BAD), 4, 4)
    d.row("Is the AI working")
    d.add(stat("Model loaded", [prom('ai_lab_ollama_model_loaded == 1', "{{model}}", instant=True)], text_mode="name", color_mode="none",
               description="The models Ollama holds in memory right now."), 6, 4)
    d.add(stat("Model in GPU memory", [prom("min(ai_lab_ollama_model_vram_ratio)", instant=True)], unit="percentunit",
               steps=thresholds((None, RED), (0.99, GREEN)), description="Below 100% means part of the model runs on the CPU and replies slow down."), 4, 4)
    d.add(stat("Messages (24 h)", [prom("sum(increase(ai_lab_messages_total[24h]))", instant=True)], decimals=0), 4, 4)
    d.add(stat("Tool calls (range)", [cw_sql(f'SELECT SUM(ToolCalls) FROM {BY_TOOL}', period="86400")], decimals=0,
               description="Calls to the vulnerability findings tools, counted from the Lambda's log lines."), 5, 4)
    d.add(stat("Tool errors (range)", [cw_sql(f'SELECT SUM(ToolErrors) FROM {BY_TOOL}', period="86400")], decimals=0,
               steps=thresholds((None, GREEN), (1, AMBER))), 5, 4)
    d.row("GPU and time left")
    d.add(stat("GPU temperature", [prom("max(ai_lab_gpu_temperature_celsius)", instant=True)], unit="celsius",
               steps=thresholds((None, GREEN), (75, AMBER), (85, RED))), 4, 4)
    d.add(stat("GPU utilization", [prom("avg(ai_lab_gpu_utilization_ratio)", instant=True)], unit="percentunit"), 4, 4)
    d.add(stat("Idle stop in", [prom("max(ai_lab_idle_seconds_remaining)", instant=True)], unit="s", mappings=OFF_AT_MINUS_ONE), 4, 4)
    d.add(stat("Session limit in", [prom("max(ai_lab_hard_limit_seconds_remaining)", instant=True)], unit="s", mappings=OFF_AT_MINUS_ONE,
               steps=thresholds((None, RED), (600, AMBER), (1800, GREEN))), 4, 4)
    d.add(stat("Estimated cost (24 h)",
               [prom("max(ai_lab_instance_hourly_cost_usd) * count_over_time(max(ai_lab_exporter_last_success_timestamp_seconds)[24h:1m]) / 60", instant=True)],
               unit="currencyUSD", decimals=2, description="Hourly price times the minutes the instance reported in. Not the AWS bill."), 4, 4)
    d.add(stat("Tokens (1 h)", [prom("sum(increase(ai_lab_tokens_total[1h]))", instant=True)], decimals=0), 4, 4)
    d.row("Trends")
    d.add(series("Tokens per 5 minutes", [prom("sum by (direction) (increase(ai_lab_tokens_total[5m]))", "{{direction}}")], stack=True), 12, 8)
    d.add(series("GPU", [prom("max(ai_lab_gpu_utilization_ratio) * 100", "utilization %"), prom("max(ai_lab_gpu_temperature_celsius)", "temperature °C", ref="B")]), 12, 8)
    return d.d


def tool_calls():
    d = Dashboard("ai-lab-tool-calls", "AI Lab: Tool calls",
                  "Proof the tools are used and healthy: calls, errors and speed per tool, and refused requests.", STANDARD_VARS)
    d.row("Now")
    d.add(stat("Calls (range)", [cw_sql(f"SELECT SUM(ToolCalls) FROM {BY_TOOL}", period="300")], decimals=0), 6, 4)
    d.add(stat("Errors (range)", [cw_sql(f"SELECT SUM(ToolErrors) FROM {BY_TOOL}", period="300")], decimals=0, steps=thresholds((None, GREEN), (1, AMBER))), 6, 4)
    d.add(stat("Refused requests (range)", [cw_sql(f'SELECT SUM(Unauthorized) FROM "{TOOLS_NS}"', period="300")], decimals=0, steps=thresholds((None, GREEN), (1, AMBER)),
               description="Requests the tool server answered with 401: a wrong or missing token."), 6, 4)
    d.add(stat("Slowest call (range)", [cw_sql(f"SELECT MAX(ToolLatencyMs) FROM {BY_TOOL}", period="300")], unit="ms"), 6, 4)
    d.row("Per tool")
    d.add(series("Calls per tool", [cw_sql(f"SELECT SUM(ToolCalls) FROM {BY_TOOL} GROUP BY Tool", "{{Tool}}", period="300")], description="Per 5 minutes.", stack=True), 12, 8)
    d.add(series("Errors per tool", [cw_sql(f"SELECT SUM(ToolErrors) FROM {BY_TOOL} GROUP BY Tool", "{{Tool}}", period="300")], description="Per 5 minutes."), 12, 8)
    d.add(series("Average time per tool", [cw_sql(f"SELECT AVG(ToolLatencyMs) FROM {BY_TOOL} GROUP BY Tool", "{{Tool}}", period="300")], unit="ms"), 12, 8)
    d.add(series("Slowest call per tool", [cw_sql(f"SELECT MAX(ToolLatencyMs) FROM {BY_TOOL} GROUP BY Tool", "{{Tool}}", period="300")], unit="ms"), 12, 8)
    d.row("Lambda and refusals")
    d.add(series("Refused requests", [cw_sql(f'SELECT SUM(Unauthorized) FROM "{TOOLS_NS}"', "401s", period="300")]), 8, 7)
    d.add(series("Lambda invocations and errors", [
        cw_sql(f'SELECT SUM(Invocations) FROM SCHEMA("AWS/Lambda", FunctionName) WHERE FunctionName = \'{PROJECT}-vuln-mcp\'', "invocations", period="300"),
        cw_sql(f'SELECT SUM(Errors) FROM SCHEMA("AWS/Lambda", FunctionName) WHERE FunctionName = \'{PROJECT}-vuln-mcp\'', "errors", ref="B", period="300")]), 8, 7)
    d.add(series("Lambda duration", [
        cw_sql(f'SELECT AVG(Duration) FROM SCHEMA("AWS/Lambda", FunctionName) WHERE FunctionName = \'{PROJECT}-vuln-mcp\'', "average", period="300"),
        cw_sql(f'SELECT MAX(Duration) FROM SCHEMA("AWS/Lambda", FunctionName) WHERE FunctionName = \'{PROJECT}-vuln-mcp\'', "slowest", ref="B", period="300")], unit="ms"), 8, 7)
    return d.d


def tokenomics():
    variables = STANDARD_VARS + [text_var("price_in", "Hosted API price per 1M input tokens (USD)", "3"),
                                 text_var("price_out", "Hosted API price per 1M output tokens (USD)", "15")]
    d = Dashboard("ai-lab-tokenomics", "AI Lab: Tokenomics",
                  "Tokens, what they cost on this lab, and what the same tokens would cost on a hosted API. Per-person figures are on the control panel.", variables)
    running_min = "count_over_time(max(ai_lab_exporter_last_success_timestamp_seconds)[$__range:1m])"
    cost = f"max(ai_lab_instance_hourly_cost_usd) * {running_min} / 60"
    tokens = "sum(increase(ai_lab_tokens_total[$__range]))"
    d.row("Over the selected time range")
    d.add(stat("Tokens", [prom(tokens, instant=True)], decimals=0), 4, 4)
    d.add(stat("Input tokens", [prom('sum(increase(ai_lab_tokens_total{direction="input"}[$__range]))', instant=True)], decimals=0), 4, 4)
    d.add(stat("Output tokens", [prom('sum(increase(ai_lab_tokens_total{direction="output"}[$__range]))', instant=True)], decimals=0), 4, 4)
    d.add(stat("Lab cost", [prom(cost, instant=True)], unit="currencyUSD", decimals=2, description="Hourly price times the minutes the instance reported in."), 4, 4)
    d.add(stat("Cost per 1M tokens", [prom(f"({cost}) / ({tokens} / 1000000)", instant=True)], unit="currencyUSD", decimals=2,
               description="Lab cost divided by tokens. Falls as the lab is used more."), 4, 4)
    d.add(stat("Idle share of running time", [prom(
        f"clamp_min(1 - (count_over_time((sum(increase(ai_lab_tokens_total[5m])) > 0)[$__range:5m]) * 5) / ({running_min}), 0)", instant=True)],
        unit="percentunit", decimals=0, description="Minutes with no tokens processed, as a share of minutes the instance was running."), 4, 4)
    d.row("Compare with a hosted API")
    d.add(stat("Same tokens on a hosted API", [prom(
        'sum(increase(ai_lab_tokens_total{direction="input"}[$__range])) / 1000000 * $price_in + '
        'sum(increase(ai_lab_tokens_total{direction="output"}[$__range])) / 1000000 * $price_out', instant=True)],
        unit="currencyUSD", decimals=2, description="Uses the two price variables at the top. Change them to match the API you compare against."), 6, 4)
    d.add(stat("Lab minus hosted", [prom(
        f"({cost}) - (sum(increase(ai_lab_tokens_total{{direction=\"input\"}}[$__range])) / 1000000 * $price_in + "
        f"sum(increase(ai_lab_tokens_total{{direction=\"output\"}}[$__range])) / 1000000 * $price_out)", instant=True)],
        unit="currencyUSD", decimals=2, steps=thresholds((None, GREEN), (0, AMBER)), description="Negative means the lab was cheaper for this usage."), 6, 4)
    d.row("Over time")
    d.add(series("Tokens per 5 minutes, by model", [prom("sum by (model) (increase(ai_lab_tokens_total[5m]))", "{{model}}")], stack=True), 12, 8)
    d.add(series("Tokens per 5 minutes, input and output", [prom("sum by (direction) (increase(ai_lab_tokens_total[5m]))", "{{direction}}")], stack=True), 12, 8)
    d.add(series("Messages per 5 minutes, by model", [prom("sum by (model) (increase(ai_lab_messages_total[5m]))", "{{model}}")], stack=True), 12, 8)
    d.add(series("Tokens per message", [prom("sum(increase(ai_lab_tokens_total[15m])) / clamp_min(sum(increase(ai_lab_messages_total[15m])), 1)", "tokens per message")]), 12, 8)
    return d.d


def model_serving():
    d = Dashboard("ai-lab-model-gpu", "AI Lab: Model serving and GPU",
                  "Why is it slow? Which model is loaded, how much of it is on the GPU, and how the GPU is coping.", STANDARD_VARS)
    d.row("Loaded models")
    d.add(table("Loaded models", [prom("ai_lab_ollama_model_vram_ratio", "", ref="A", instant=True), prom("ai_lab_ollama_model_size_bytes", "", ref="B", instant=True)],
               description="Size in memory and the share that fits in GPU memory (1 = all of it).",
               transformations=[{"id": "merge", "options": {}},
                                {"id": "organize", "options": {"excludeByName": {"Time": True, "instance": True, "job": True, "instance_id": True},
                                                               "renameByName": {"model": "Model", "Value #A": "In GPU memory", "Value #B": "Size in memory"}}}],
               overrides=[{"matcher": {"id": "byName", "options": "In GPU memory"}, "properties": [{"id": "unit", "value": "percentunit"}]},
                          {"matcher": {"id": "byName", "options": "Size in memory"}, "properties": [{"id": "unit", "value": "bytes"}]}]), 12, 7)
    d.add(table("Configured model settings", [prom("ai_lab_model_param", "", instant=True)],
               description="Temperature, top_p, top_k and context size as set in Open WebUI.",
               transformations=[{"id": "organize", "options": {"excludeByName": {"Time": True, "instance": True, "job": True, "instance_id": True},
                                                               "renameByName": {"model": "Model", "param": "Setting", "Value": "Value"}}}]), 12, 7)
    d.row("GPU")
    d.add(series("Temperature", [prom("ai_lab_gpu_temperature_celsius", "GPU {{gpu}}")], unit="celsius"), 8, 7)
    d.add(series("Utilization", [prom("ai_lab_gpu_utilization_ratio", "GPU {{gpu}}")], unit="percentunit", minimum=0, maximum=1), 8, 7)
    d.add(series("Power", [prom("ai_lab_gpu_power_watts", "draw", ), prom("ai_lab_gpu_power_limit_watts", "limit", ref="B")], unit="watt"), 8, 7)
    d.add(series("GPU memory", [prom("ai_lab_gpu_memory_used_bytes", "used"), prom("ai_lab_gpu_memory_total_bytes", "total", ref="B")], unit="bytes"), 8, 7)
    d.add(series("Throttling", [prom('ai_lab_gpu_throttled == 1', "{{reason}}")], description="Appears only while the GPU reports a throttle reason.", minimum=0, maximum=1), 8, 7)
    d.add(series("Model in GPU memory", [prom("ai_lab_ollama_model_vram_ratio", "{{model}}")], unit="percentunit", minimum=0, maximum=1,
                 description="Anything under 100% means part of the model runs on the CPU."), 8, 7)
    d.row("Telemetry sources")
    d.add(series("Exporter sources up", [prom("ai_lab_exporter_source_up", "{{source}}")], minimum=0, maximum=1,
                 description="0 means the exporter could not read that source this run."), 12, 6)
    d.add(series("Seconds since the exporter last succeeded", [prom("time() - max(ai_lab_exporter_last_success_timestamp_seconds)", "age")], unit="s"), 12, 6)
    return d.d


def logs_dashboard():
    variables = STANDARD_VARS + [text_var("stream", "Log stream selector", 'service_name=~".+"')]
    d = Dashboard("ai-lab-logs", "AI Lab: Logs",
                  "Saved log searches for troubleshooting. Alloy sends the logs over OTLP, so its labels (job, unit, container) arrive as log "
                  "attributes; change the stream selector at the top if your Loki indexes them differently.", variables)
    sel = "{$stream}"
    d.row("Volume")
    d.add(series("Log lines per minute, by source", [{"datasource": LOKI, "refId": "A", "queryType": "range",
                                                      "expr": f'sum by (job) (count_over_time({sel} | job=~".+" [1m]))', "legendFormat": "{{job}}"}], stack=True), 24, 6)
    d.row("Searches")
    d.add(logs("Bootstrap warnings and errors", f'{sel} | job="ai-lab-bootstrap" |~ "(?i)warn|error|fail"'), 12, 9)
    d.add(logs("Security Analyst and MCP registration", f'{sel} | job="ai-lab-bootstrap" |~ "(?i)vuln|mcp|security analyst|registration"'), 12, 9)
    d.add(logs("Ollama errors", f'{sel} | job="ai-lab-journal" | unit=~"ollama.*" |~ "(?i)error|fail|panic|oom|out of memory"'), 12, 9)
    d.add(logs("Open WebUI errors", f'{sel} | job="ai-lab-containers" | container=~".*open-webui.*" |~ "(?i)error|exception|traceback| 5[0-9][0-9] "'), 12, 9)
    d.add(logs("Sign-ins", f'{sel} | job="ai-lab-containers" | container=~".*open-webui.*" |~ "(?i)signin|login|oauth"'), 12, 9)
    d.add(logs("Services stopping or restarting", f'{sel} | job="ai-lab-journal" |~ "(?i)stopped|stopping|failed|restart"'), 12, 9)
    return d.d


DASHBOARDS = {
    "lab-overview.json": lab_overview,
    "tool-calls.json": tool_calls,
    "tokenomics.json": tokenomics,
    "model-serving-and-gpu.json": model_serving,
    "logs.json": logs_dashboard,
}


# ---- alert rules ------------------------------------------------------------------------------

FOLDER_UID = "ai-lab"
RULE_GROUP = "ai-lab"


def a_prom(ref, expr, seconds=600):
    return {"refId": ref, "relativeTimeRange": {"from": seconds, "to": 0}, "datasourceUid": "__DS_PROM__",
            "model": {"refId": ref, "datasource": {"type": "prometheus", "uid": "__DS_PROM__"}, "expr": expr, "instant": True, "range": False,
                      "intervalMs": 1000, "maxDataPoints": 43200}}


def a_cw(ref, sql, seconds=900, period="300"):
    return {"refId": ref, "relativeTimeRange": {"from": seconds, "to": 0}, "datasourceUid": "__DS_CW__",
            "model": {"refId": ref, "datasource": {"type": "cloudwatch", "uid": "__DS_CW__"}, "queryMode": "Metrics", "metricQueryType": 1,
                      "metricEditorMode": 1, "sqlExpression": sql, "region": "default", "id": "", "label": "", "period": period,
                      "expression": "", "namespace": "", "metricName": "", "dimensions": {}, "statistic": "Sum", "matchExact": True,
                      "intervalMs": 1000, "maxDataPoints": 43200}}


def a_reduce(ref, source, reducer="last"):
    return {"refId": ref, "relativeTimeRange": {"from": 0, "to": 0}, "datasourceUid": "__expr__",
            "model": {"refId": ref, "type": "reduce", "expression": source, "reducer": reducer, "datasource": {"type": "__expr__", "uid": "__expr__"},
                      "settings": {"mode": "dropNN"}}}


def a_math(ref, expression):
    return {"refId": ref, "relativeTimeRange": {"from": 0, "to": 0}, "datasourceUid": "__expr__",
            "model": {"refId": ref, "type": "math", "expression": expression, "datasource": {"type": "__expr__", "uid": "__expr__"}}}


def a_threshold(ref, source, kind, value):
    return {"refId": ref, "relativeTimeRange": {"from": 0, "to": 0}, "datasourceUid": "__expr__",
            "model": {"refId": ref, "type": "threshold", "expression": source, "datasource": {"type": "__expr__", "uid": "__expr__"},
                      "conditions": [{"type": "query", "evaluator": {"type": kind, "params": [value]}, "operator": {"type": "and"},
                                      "query": {"params": [ref]}, "reducer": {"type": "last", "params": []}}]}}


def rule(uid, title, summary, data, condition, wait, severity="warning", no_data="OK"):
    return {"uid": uid, "title": title, "ruleGroup": RULE_GROUP, "folderUID": FOLDER_UID, "orgID": 1, "condition": condition, "data": data,
            "for": wait, "noDataState": no_data, "execErrState": "Error", "isPaused": False,
            "annotations": {"summary": summary}, "labels": {"severity": severity, "app": "ai-lab"}}


def alert_rules():
    return [
        rule("ai-lab-gpu-hot", "GPU hot", "GPU temperature has been above 85 °C, or a thermal throttle is active, for 5 minutes.",
             [a_prom("A", '(max(ai_lab_gpu_temperature_celsius > bool 85) or vector(0)) + (max(ai_lab_gpu_throttled{reason=~".*thermal.*"} > bool 0) or vector(0))'),
              a_reduce("B", "A"), a_threshold("C", "B", "gt", 0)], "C", "5m"),
        rule("ai-lab-model-on-cpu", "Model running partly on the CPU", "A loaded model is not fully in GPU memory, so replies will be slow.",
             [a_prom("A", "count((ai_lab_ollama_model_vram_ratio < 0.99) and on(model) (ai_lab_ollama_model_loaded == 1)) or vector(0)"),
              a_reduce("B", "A"), a_threshold("C", "B", "gt", 0)], "C", "5m"),
        rule("ai-lab-tool-errors", "Tool error rate above 10%", "More than 10% of tool calls failed over the last 15 minutes.",
             [a_cw("A", f"SELECT SUM(ToolErrors) FROM {BY_TOOL}"), a_cw("B", f"SELECT SUM(ToolCalls) FROM {BY_TOOL}"),
              a_reduce("C", "A", "sum"), a_reduce("D", "B", "sum"), a_math("E", "$D > 0 ? $C / $D * 100 : 0"), a_threshold("F", "E", "gt", 10)], "F", "0s"),
        rule("ai-lab-mcp-refusals", "MCP server refusing requests", "The tool server answered more than 5 requests with 401 in 10 minutes: a wrong or missing token.",
             [a_cw("A", f'SELECT SUM(Unauthorized) FROM "{TOOLS_NS}"', seconds=600, period="60"), a_reduce("B", "A", "sum"), a_threshold("C", "B", "gt", 5)], "C", "0s"),
        rule("ai-lab-disk-filling", "Root disk above 85%", "The instance's root disk is more than 85% full.",
             [a_prom("A", 'max(1 - node_filesystem_avail_bytes{mountpoint="/",fstype!~"tmpfs|overlay"} / node_filesystem_size_bytes{mountpoint="/",fstype!~"tmpfs|overlay"}) * 100 or vector(0)'),
              a_reduce("B", "A"), a_threshold("C", "B", "gt", 85)], "C", "10m"),
        rule("ai-lab-webui-errors", "Open WebUI server errors above 5%", "More than 5% of Open WebUI requests failed with a 5xx over 5 minutes. Check the metric name at first import.",
             [a_prom("A", 'sum(rate(http_server_duration_milliseconds_count{http_status_code=~"5.."}[5m])) / sum(rate(http_server_duration_milliseconds_count[5m])) * 100 or vector(0)'),
              a_reduce("B", "A"), a_threshold("C", "B", "gt", 5)], "C", "5m"),
        rule("ai-lab-telemetry-silent", "Telemetry silent while the instance runs", "The instance is running but no lab metrics reached Grafana for 10 minutes.",
             [a_prom("A", "count(ai_lab_exporter_last_success_timestamp_seconds) or vector(0)"),
              a_cw("B", 'SELECT COUNT(CPUUtilization) FROM SCHEMA("AWS/EC2", InstanceId) WHERE InstanceId = \'__INSTANCE_ID__\'', seconds=600, period="60"),
              a_reduce("C", "A"), a_reduce("D", "B", "sum"), a_math("E", "$D > 0 && $C == 0"), a_threshold("F", "E", "gt", 0)], "F", "0s"),
    ]


# ---- output -----------------------------------------------------------------------------------


def render():
    """Returns {relative path: text} for every generated file."""
    files = {f"dashboards/{name}": json.dumps(build(), indent=2) + "\n" for name, build in DASHBOARDS.items()}
    files["alerts/alert-rules.json"] = json.dumps({"folderUid": FOLDER_UID, "folderTitle": "AI Lab", "group": RULE_GROUP, "interval": "1m", "rules": alert_rules()}, indent=2) + "\n"
    return files


if __name__ == "__main__":
    for rel, text in render().items():
        path = ROOT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, newline="\n")
        print("wrote", path.relative_to(ROOT.parent))
    sys.exit(0)
