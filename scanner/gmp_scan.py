"""Run from Greenbone's gvm-tools container with gvm-script (GMP over its Unix socket)."""

import json
import pathlib
import time
from base64 import b64decode
from lxml import etree as ET


FULL_AND_FAST = "daba56c8-73ec-11df-a475-002264764cea"
OPENVAS = "08b69003-5fc2-4037-a479-93b440211c73"
XML_FORMAT = "a994b278-1f62-11e1-96ac-406186ea4fc5"
PDF_FORMAT = "c402cc3e-b531-11e1-9163-406186ea4fc5"
CSV_FORMAT = "c1645568-627a-11e3-a660-406186ea4fc5"


def _ok(response):
    status = response.get("status", "")
    if not status.startswith("2"):
        raise RuntimeError(f"Greenbone rejected {response.tag}: {status} {response.get('status_text', '')}")
    return response


def _feed_ready(gmp, deadline):
    while time.time() < deadline:
        configs = _ok(gmp.get_scan_configs())
        scanners = _ok(gmp.get_scanners())
        if (configs.find(f".//config[@id='{FULL_AND_FAST}']") is not None
                and scanners.find(f".//scanner[@id='{OPENVAS}']") is not None):
            return
        time.sleep(60)
    raise TimeoutError("Greenbone feed and safe Full and Fast configuration did not become ready")


def _scan(gmp, name, host, credential_id, deadline, output):
    kwargs = {"name": f"{name} {int(time.time())}", "hosts": [host], "alive_test": "Consider Alive"}
    if credential_id:
        kwargs.update(ssh_credential_id=credential_id, ssh_credential_port=22)
    target_id = _ok(gmp.create_target(**kwargs)).get("id")
    task_id = _ok(gmp.create_task(
        name=f"{name} {int(time.time())}", config_id=FULL_AND_FAST,
        target_id=target_id, scanner_id=OPENVAS,
    )).get("id")
    started = _ok(gmp.start_task(task_id))
    report_id = started.findtext("report_id")
    if not report_id:
        raise RuntimeError("Greenbone did not return a report ID")

    while time.time() < deadline:
        task = _ok(gmp.get_task(task_id))
        status = task.findtext("./task/status")
        if status == "Done":
            break
        if status in {"Stopped", "Interrupted", "Delete Requested"}:
            raise RuntimeError(f"{name} scan ended with status {status}")
        time.sleep(60)
    else:
        raise TimeoutError(f"{name} scan exceeded its time limit")

    # Request every result; the default GMP page would silently omit older rows.
    report = _ok(gmp.get_report(
        report_id, report_format_id=XML_FORMAT,
        filter_string="rows=-1", ignore_pagination=True,
    ))
    (output / f"{name}.xml").write_bytes(ET.tostring(report, encoding="utf-8", xml_declaration=True))
    exports = []
    for extension, format_id in (("pdf", PDF_FORMAT), ("csv", CSV_FORMAT)):
        try:
            formatted = _ok(gmp.get_report(report_id, report_format_id=format_id,
                                           filter_string="rows=-1", ignore_pagination=True))
            # Greenbone returns non-XML report formats as base64 in the report
            # element. The official gvm-tools example uses itertext here.
            payload = b64decode("".join(formatted[0].itertext()).encode("ascii"), validate=True)
            if not payload or (extension == "pdf" and not payload.startswith(b"%PDF")):
                raise ValueError("Empty or invalid Greenbone export")
            (output / f"{name}.{extension}").write_bytes(payload)
            exports.append(extension)
        except Exception:
            # XML is the canonical lossless export. An optional report format
            # can be absent until the feed finishes loading on first boot.
            pass
    return {"target_id": target_id, "task_id": task_id, "report_id": report_id,
            "status": "Done", "exports": ["xml"] + exports}


def main(gmp, args):
    settings = json.loads(pathlib.Path(args.argv[-1]).read_text())
    output = pathlib.Path(settings["output"])
    output.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + int(settings["timeout_seconds"])
    _feed_ready(gmp, deadline)

    # The key comes from Secrets Manager only at scan time. Greenbone stores this
    # credential on its encrypted persistent EBS volume for the scan history.
    private_key = pathlib.Path(settings["private_key_file"]).read_text()
    credential = _ok(gmp.create_credential(
        name=f"svc_vulnscan {settings['run_id']}", credential_type="usk",
        login="svc_vulnscan", private_key=private_key,
    ))
    result = {"credential_id": credential.get("id")}
    result["host"] = _scan(gmp, "host", settings["lab_private_ip"], result["credential_id"], deadline, output)
    result["public"] = _scan(gmp, "public", settings["public_hostname"], None, deadline, output)
    result["control-panel"] = _scan(gmp, "control-panel", settings["control_panel_hostname"], None, deadline, output)
    (output / "greenbone.json").write_text(json.dumps(result, indent=2))
    return 0


if __name__ == "__gmp__":
    main(gmp, args)
