"""Read-only deployed Lambda artifact and HTTP API assessment.

Uses AWS CLI on the scanner host. Reports deliberately omit raw AWS responses,
environment variables, presigned URLs, and secret matches. Downloaded code is
never executed. These checks are a baseline assessment, not an IAM simulator.
"""

import base64
import hashlib
import json
import pathlib
import re
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile


class AwsReadError(RuntimeError):
    def __init__(self, operation, code):
        super().__init__(f"{operation}: {code}")
        self.code = code


class CoverageError(RuntimeError):
    """A fixed diagnostic code is safe to export; raw exceptions are not."""
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class Aws:
    def __init__(self, region):
        self.region = region

    def __call__(self, service, operation, **arguments):
        command = ["aws", service, operation, "--region", self.region,
                   "--output", "json", "--no-cli-pager"]
        for name, value in arguments.items():
            command.extend(["--" + name.replace("_", "-"), str(value)])
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
        if result.returncode:
            match = re.search(r"An error occurred \(([A-Za-z0-9.]+)\)", result.stderr)
            raise AwsReadError(operation, match[1] if match else "AWSReadFailed")
        return json.loads(result.stdout)


def values(value):
    return value if isinstance(value, list) else [value]


def statements(policy):
    if isinstance(policy, str):
        policy = json.loads(urllib.parse.unquote(policy))
    return values(policy.get("Statement", []))


def check(report, resource, name, passed, detail):
    report["checks"].append({"resource": resource, "check": name,
                             "status": "PASS" if passed else "FAIL", "detail": detail})


def review(report, resource, name, detail):
    report["checks"].append({"resource": resource, "check": name,
                             "status": "REVIEW", "detail": detail})


def download_zip(url, destination, expected_hash):
    if urllib.parse.urlparse(url).scheme != "https":
        raise CoverageError("artifact_requires_https")
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(url, timeout=120) as response, destination.open("wb") as out:
        if urllib.parse.urlparse(response.geturl()).scheme != "https":
            raise CoverageError("artifact_redirect_requires_https")
        while block := response.read(1024 * 1024):
            size += len(block)
            if size > 250 * 1024 * 1024:
                raise CoverageError("artifact_download_too_large")
            digest.update(block)
            out.write(block)
    if base64.b64encode(digest.digest()).decode() != expected_hash:
        raise CoverageError("artifact_code_sha256_mismatch")


def extract_zip(archive, destination):
    """Reject traversal, symlinks, special files and oversized ZIPs before writing."""
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if len(entries) > 100000 or sum(e.file_size for e in entries) > 512 * 1024 * 1024:
            raise CoverageError("artifact_extraction_too_large")
        for entry in entries:
            path = pathlib.PurePosixPath(entry.filename)
            mode = entry.external_attr >> 16
            if (path.is_absolute() or ".." in path.parts or "\\" in entry.filename
                    or ":" in entry.filename or stat.S_ISLNK(mode)
                    or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR))):
                raise CoverageError("unsafe_zip_member")
        package.extractall(destination)


def sanitize_trivy(raw):
    """Allowlist fields; secret Match/Code and source excerpts never reach S3."""
    report = {"SchemaVersion": raw.get("SchemaVersion"), "Results": []}
    for result in raw.get("Results") or []:
        safe = {k: result[k] for k in ("Target", "Class", "Type") if k in result}
        safe["Packages"] = [{k: p[k] for k in ("Name", "Version") if k in p}
                            for p in result.get("Packages") or []]
        safe["Vulnerabilities"] = [
            {k: v[k] for k in ("VulnerabilityID", "PkgName", "InstalledVersion",
                               "FixedVersion", "Severity", "Title", "PrimaryURL", "Status") if k in v}
            for v in result.get("Vulnerabilities") or []
        ]
        safe["Secrets"] = [{k: s[k] for k in ("RuleID", "Category", "Severity", "Title",
                                              "StartLine", "EndLine") if k in s}
                           for s in result.get("Secrets") or []]
        report["Results"].append(safe)
    return report


def scan_artifact(directory, image):
    # Only artifact and output paths are mounted, never the AWS credential
    # files, IMDS credentials, Docker socket, or host filesystem.
    with tempfile.TemporaryDirectory() as temporary:
        output = pathlib.Path(temporary)
        subprocess.run([
            "docker", "run", "--rm", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "-v", f"{directory}:/artifact:ro", "-v", f"{output}:/reports", image,
            "rootfs", "--scanners", "vuln,secret", "--list-all-pkgs",
            "--format", "json", "--output", "/reports/raw.json", "/artifact",
        ], check=True, capture_output=True, timeout=600)
        return sanitize_trivy(json.loads((output / "raw.json").read_text()))


def inspect_function(aws, name, target, baseline, work, output, image, report,
                     downloader=None, scanner=None):
    downloader = downloader or download_zip
    scanner = scanner or scan_artifact
    arn = target["arn"]
    deployed = aws("lambda", "get-function", function_name=arn)
    config = deployed["Configuration"]
    if config.get("PackageType", "Zip") != "Zip":
        raise CoverageError("expected_zip_lambda")
    if config.get("State") != "Active" or config.get("LastUpdateStatus") != "Successful":
        raise CoverageError("function_not_stable")
    entry = {k: config.get(k) for k in ("FunctionArn", "Version", "RevisionId", "CodeSha256",
                                        "LastModified", "Runtime", "Architectures", "Role")}
    entry.update(name=name, artifacts=[], coverage="incomplete")
    report["functions"].append(entry)
    check(report, arn, "runtime_baseline", config.get("Runtime") in baseline["allowed_runtimes"],
          "Runtime must match the reviewed baseline; this is not a CVE scan of the AWS-managed runtime.")
    check(report, arn, "execution_role", config.get("Role") == target["role_arn"], "Execution role matches inventory.")
    mode = aws("lambda", "get-runtime-management-config", function_name=arn)
    entry["runtime_update_mode"] = mode.get("UpdateRuntimeOn")
    entry["runtime_version_arn"] = mode.get("RuntimeVersionArn")
    check(report, arn, "automatic_runtime_updates", mode.get("UpdateRuntimeOn") == "Auto",
          "AWS-managed runtime security updates should be automatic.")
    urls = aws("lambda", "list-function-url-configs", function_name=arn).get("FunctionUrlConfigs", [])
    check(report, arn, "no_function_urls", not urls, "Panel functions should be reachable through API Gateway only.")
    try:
        policy = aws("lambda", "get-policy", function_name=arn)
    except AwsReadError as exc:
        if exc.code != "ResourceNotFoundException":
            raise
        policy = {"Policy": {"Statement": []}}
    account = arn.split(":")[4]
    region = arn.split(":")[3]
    allowed_source = f"arn:aws:execute-api:{region}:{account}:{baseline['api_id']}/*/*"
    if name == "admin":
        allowed_source += "/admin/*"
    allowed_event = f"arn:aws:events:{region}:{account}:rule/{arn.split(':')[-1]}"
    for statement in statements(policy["Policy"]):
        if statement.get("Effect") != "Allow":
            continue
        principal = statement.get("Principal")
        condition = statement.get("Condition", {})
        source = {key.lower(): value for op in ("ArnLike", "ArnEquals")
                  for key, value in condition.get(op, {}).items()}.get("aws:sourcearn")
        permitted = (principal == {"Service": "apigateway.amazonaws.com"} and source == allowed_source
                     or name == "spend" and principal == {"Service": "events.amazonaws.com"}
                     and source == allowed_event)
        check(report, arn, "invocation_policy", permitted and values(statement.get("Action", [])) == ["lambda:InvokeFunction"],
              "Allow statements must be scoped to this API (admin paths only for admin), or the spend schedule.")

    artifacts = [("code", deployed["Code"]["Location"], config["CodeSha256"], arn)]
    for index, layer in enumerate(config.get("Layers", [])):
        layer_arn = layer["Arn"]
        if layer_arn not in baseline["layer_version_arns"]:
            raise CoverageError("layer_outside_inventory")
        version = aws("lambda", "get-layer-version-by-arn", arn=layer_arn)
        artifacts.append((f"layer-{index}", version["Content"]["Location"],
                          version["Content"]["CodeSha256"], layer_arn))
    for label, url, code_hash, resource in artifacts:
        directory = work / name / label
        directory.mkdir(parents=True)
        archive = directory.parent / f"{label}.zip"
        downloader(url, archive, code_hash)
        extract_zip(archive, directory)
        findings = scanner(directory, image)
        filename = f"lambda-{name}-{label}-trivy.json"
        (output / filename).write_text(json.dumps(findings, indent=2))
        packages = sum(len(r["Packages"]) for r in findings["Results"])
        entry["artifacts"].append({"resource": resource, "code_sha256": code_hash, "report": filename,
                                   "package_count": packages,
                                   "vulnerability_count": sum(len(r["Vulnerabilities"]) for r in findings["Results"]),
                                   "secret_count": sum(len(r["Secrets"]) for r in findings["Results"]),
                                   "package_coverage": "identified_packages" if packages else "no_identified_packages"})
    current = aws("lambda", "get-function", function_name=arn)["Configuration"]
    if any(current.get(k) != config.get(k) for k in ("RevisionId", "CodeSha256", "Layers")):
        raise CoverageError("function_changed_during_assessment")
    entry["coverage"] = "complete"
    entry["excluded"] = ["AWS-managed OS/runtime/SDK", "business-logic authorization"]


def inspect_policy(report, resource, policy):
    for index, statement in enumerate(statements(policy)):
        if statement.get("Effect") != "Allow":
            continue
        actions = values(statement.get("Action", []))
        resources = values(statement.get("Resource", []))
        check(report, resource, f"policy_statement_{index}_explicit_allow", "NotAction" not in statement and "NotResource" not in statement,
              "Complement grants require an explicit policy review.")
        check(report, resource, f"policy_statement_{index}_privilege", not any(
            a.lower() in ("*", "*:*", "iam:*", "iam:passrole", "iam:putrolepolicy",
                          "iam:attachrolepolicy", "iam:createpolicyversion", "sts:assumerole")
            or "*" in a for a in actions), "No wildcard actions or role/policy escalation grants.")
        if any("*" in r for r in resources):
            review(report, resource, f"policy_statement_{index}_resources",
                   {"actions": actions, "resources": resources,
                    "condition_keys": sorted({k for v in statement.get("Condition", {}).values() for k in v}),
                    "reason": "Wildcard resources need review; some AWS read/list APIs require them."})


def inspect_role(aws, arn, baseline, report):
    name = arn.rsplit("/", 1)[-1]
    role = aws("iam", "get-role", role_name=name)["Role"]
    for statement in statements(role["AssumeRolePolicyDocument"]):
        if statement.get("Effect") == "Allow":
            check(report, arn, "lambda_only_trust", statement.get("Principal") == {"Service": "lambda.amazonaws.com"}
                  and values(statement.get("Action", [])) == ["sts:AssumeRole"], "Only the Lambda service should assume this execution role.")
    for policy in aws("iam", "list-role-policies", role_name=name).get("PolicyNames", []):
        document = aws("iam", "get-role-policy", role_name=name, policy_name=policy)["PolicyDocument"]
        inspect_policy(report, f"{arn}/inline/{policy}", document)
    attached = aws("iam", "list-attached-role-policies", role_name=name).get("AttachedPolicies", [])
    managed = {p["PolicyArn"] for p in attached}
    boundary = role.get("PermissionsBoundary", {}).get("PermissionsBoundaryArn")
    if boundary:
        managed.add(boundary)
    for policy_arn in sorted(managed):
        if policy_arn not in baseline["policy_arns"]:
            raise CoverageError("policy_outside_inventory")
        policy = aws("iam", "get-policy", policy_arn=policy_arn)["Policy"]
        document = aws("iam", "get-policy-version", policy_arn=policy_arn,
                       version_id=policy["DefaultVersionId"])["PolicyVersion"]["Document"]
        # Boundaries restrict grants; do not report their allowed action ranges
        # as privileges. They remain evidence requiring effective-access review.
        if policy_arn == boundary:
            review(report, arn, "permissions_boundary", {"policy_arn": policy_arn,
                                                       "version": policy["DefaultVersionId"]})
        if any(p["PolicyArn"] == policy_arn for p in attached):
            inspect_policy(report, policy_arn, document)
    review(report, arn, "effective_access", "Policy heuristics do not simulate SCPs, resource policies, session policies or all IAM escalation paths.")


def integration_function(uri):
    match = re.fullmatch(r"arn:aws:apigateway:[^:]+:lambda:path/2015-03-31/functions/(arn:aws:lambda:[^/]+)/invocations", uri)
    return match[1] if match else uri


def inspect_api(aws, baseline, report):
    api_id = baseline["api_id"]
    api = aws("apigatewayv2", "get-api", api_id=api_id)
    check(report, api_id, "http_api", api.get("ProtocolType") == "HTTP", "Expected HTTP API.")
    cors = api.get("CorsConfiguration", {})
    check(report, api_id, "cors_origins", set(cors.get("AllowOrigins", [])) == set(baseline["allowed_origins"]), "CORS allows only the configured control-panel origin.")
    check(report, api_id, "cors_methods_headers", set(cors.get("AllowMethods", [])) == {"GET", "POST", "PUT", "OPTIONS"}
          and set(h.lower() for h in cors.get("AllowHeaders", [])) == {"authorization", "content-type"}, "CORS methods and headers match the API baseline.")
    authorizers = {a["AuthorizerId"]: a for a in aws("apigatewayv2", "get-authorizers", api_id=api_id).get("Items", [])}
    integrations = {i["IntegrationId"]: i for i in aws("apigatewayv2", "get-integrations", api_id=api_id).get("Items", [])}
    routes = aws("apigatewayv2", "get-routes", api_id=api_id).get("Items", [])
    expected = baseline["expected_routes"]
    if not expected:
        raise CoverageError("empty_route_baseline")
    check(report, api_id, "route_inventory", {r["RouteKey"] for r in routes} == set(expected), "Deployed route inventory matches the repository API stack.")
    for route in routes:
        key = route["RouteKey"]
        resource = f"{api_id}/{key}"
        authorizer = authorizers.get(route.get("AuthorizerId"), {})
        jwt = authorizer.get("JwtConfiguration", {})
        check(report, resource, "jwt_authorization", route.get("AuthorizationType") == "JWT"
              and authorizer.get("AuthorizerType") == "JWT"
              and authorizer.get("IdentitySource") == ["$request.header.Authorization"]
              and jwt.get("Issuer") == baseline["jwt_issuer"]
              and set(jwt.get("Audience", [])) == set(baseline["jwt_audiences"]), "JWT type, source, issuer and audiences match baseline.")
        integration = integrations.get(route.get("Target", "").removeprefix("integrations/"), {})
        check(report, resource, "lambda_integration", key in expected and integration.get("IntegrationType") == "AWS_PROXY"
              and integration.get("PayloadFormatVersion") == "2.0"
              and integration_function(integration.get("IntegrationUri", "")) == expected.get(key), "Route targets the expected customer/admin Lambda.")
    stages = aws("apigatewayv2", "get-stages", api_id=api_id).get("Items", [])
    check(report, api_id, "stage_inventory", {s["StageName"] for s in stages} == {baseline["expected_stage"]}, "Only the expected stage is exposed.")
    for stage in stages:
        resource = f"{api_id}/{stage['StageName']}"
        logs = stage.get("AccessLogSettings", {})
        check(report, resource, "access_logging", bool(logs.get("DestinationArn")) and "$context.requestId" in logs.get("Format", ""), "Access logging includes request IDs.")
        check(report, resource, "current_deployment", stage.get("AutoDeploy") is True, "Auto-deploy makes the inspected routes the live configuration.")
        for key, settings in [("default", stage.get("DefaultRouteSettings", {}))] + list(stage.get("RouteSettings", {}).items()):
            effective = dict(stage.get("DefaultRouteSettings", {}))
            effective.update(settings)
            check(report, resource, f"throttle_{key}", effective.get("ThrottlingRateLimit", 0) > 0
                  and effective.get("ThrottlingBurstLimit", 0) > 0, "Explicit positive rate and burst limits are configured.")
    review(report, api_id, "application_authorization", "JWT configuration does not prove admin, customer or cross-customer authorization; authenticated API tests remain required.")


def assess(baseline, region, work, output, image, aws=None):
    # Both paths are controlled by the runner. Guard the cleanup boundary before
    # any recursive deletion (also used by the offline Windows tests).
    if work.is_symlink() or work.resolve() == output.resolve() or not work.resolve().is_relative_to(output.parent.resolve()):
        raise CoverageError("unsafe_artifact_work_directory")
    aws = aws or Aws(region)
    report = {"assessed_at": int(time.time()), "coverage": "complete", "functions": [],
              "checks": [], "errors": [], "scope": "Configured functions, execution roles and HTTP API only"}
    operations = [(f"lambda:{name}", lambda name=name, target=target: inspect_function(
        aws, name, target, baseline, work, output, image, report)) for name, target in sorted(baseline["functions"].items())]
    operations += [(arn, lambda arn=arn: inspect_role(aws, arn, baseline, report))
                   for arn in sorted({f["role_arn"] for f in baseline["functions"].values()})]
    operations += [(f"api:{baseline['api_id']}", lambda: inspect_api(aws, baseline, report))]
    try:
        for resource, operation in operations:
            try:
                operation()
            except Exception as exc:
                report["coverage"] = "incomplete"
                # Exception messages can include presigned URLs, stderr or code.
                report["errors"].append({"resource": resource, "type": type(exc).__name__,
                                         "aws_code": exc.code if isinstance(exc, AwsReadError) else None,
                                         "reason": exc.code if isinstance(exc, CoverageError) else None})
    finally:
        # Packages may contain embedded secrets; retain only sanitized reports.
        shutil.rmtree(work)
    report["summary"] = {status: sum(c["status"] == status for c in report["checks"])
                         for status in ("PASS", "FAIL", "REVIEW")}
    (output / "aws-assessment.json").write_text(json.dumps(report, indent=2))
    return report
