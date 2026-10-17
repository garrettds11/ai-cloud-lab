"""Offline regression tests for deployed assessment coverage and report safety."""

import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).parent))
import assess_aws as assessment


ARN = "arn:aws:lambda:us-east-1:123456789012:function:panel-customer"
ROLE = "arn:aws:iam::123456789012:role/panel-customer"
LAYER = "arn:aws:lambda:us-east-1:123456789012:layer:packages:1"


def baseline():
    return {"functions": {"customer": {"arn": ARN, "role_arn": ROLE}},
            "layer_version_arns": [], "policy_arns": [], "allowed_runtimes": ["python3.12"],
            "api_id": "api123", "jwt_issuer": "https://issuer.example", "jwt_audiences": ["client"],
            "allowed_origins": ["https://cp.aiwebdemo.click"], "expected_stage": "$default",
            "expected_routes": {"GET /instances": ARN}}


class FakeAWS:
    def __init__(self):
        self.calls = 0
        self.changed = False
        self.layers = []
        self.denied = None
        self.routes = [{"RouteKey": "GET /instances", "AuthorizationType": "JWT",
                        "AuthorizerId": "auth", "Target": "integrations/integration"}]
        self.authorizer = {"AuthorizerId": "auth", "AuthorizerType": "JWT",
                           "IdentitySource": ["$request.header.Authorization"],
                           "JwtConfiguration": {"Issuer": "https://issuer.example", "Audience": ["client"]}}
        self.integration = {"IntegrationId": "integration", "IntegrationType": "AWS_PROXY",
                            "PayloadFormatVersion": "2.0", "IntegrationUri": ARN}
        self.policy = {"Statement": [{"Effect": "Allow", "Principal": {"Service": "apigateway.amazonaws.com"},
                                      "Action": "lambda:InvokeFunction",
                                      "Condition": {"ArnLike": {"AWS:SourceArn": "arn:aws:execute-api:us-east-1:123456789012:api123/*/*"}}}]}
        self.stage = {"StageName": "$default", "AutoDeploy": True,
                      "AccessLogSettings": {"DestinationArn": "arn:logs", "Format": "$context.requestId"},
                      "DefaultRouteSettings": {"ThrottlingRateLimit": 10, "ThrottlingBurstLimit": 20}}

    def __call__(self, service, operation, **arguments):
        if operation == self.denied:
            raise assessment.AwsReadError(operation, "AccessDeniedException")
        if operation == "get-function":
            self.calls += 1
            return {"Configuration": {"FunctionArn": ARN, "Version": "$LATEST", "CodeSha256": "hash",
                                      "RevisionId": "changed" if self.changed and self.calls > 1 else "rev1",
                                      "Runtime": "python3.12", "Role": ROLE, "State": "Active",
                                      "LastUpdateStatus": "Successful", "Layers": self.layers,
                                      "Environment": {"Variables": {"SECRET": "never-export-this"}}},
                    "Code": {"Location": "https://download.example/zip?secret=presigned-secret"}}
        if operation == "get-runtime-management-config":
            return {"UpdateRuntimeOn": "Auto"}
        if operation == "list-function-url-configs":
            return {"FunctionUrlConfigs": []}
        if operation == "get-policy" and service == "lambda":
            return {"Policy": json.dumps(self.policy)}
        if operation == "get-layer-version-by-arn":
            return {"Content": {"Location": "https://download.example/layer", "CodeSha256": "layerhash"}}
        if operation == "get-role":
            return {"Role": {"AssumeRolePolicyDocument": {"Statement": [{"Effect": "Allow",
                             "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]}}}
        if operation == "list-role-policies":
            return {"PolicyNames": ["read"]}
        if operation == "get-role-policy":
            return {"PolicyDocument": {"Statement": [{"Effect": "Allow", "Action": "ec2:DescribeInstances", "Resource": "*"}]}}
        if operation == "list-attached-role-policies":
            return {"AttachedPolicies": []}
        if operation == "get-api":
            return {"ProtocolType": "HTTP", "CorsConfiguration": {"AllowOrigins": ["https://cp.aiwebdemo.click"],
                    "AllowMethods": ["GET", "POST", "PUT", "OPTIONS"], "AllowHeaders": ["authorization", "content-type"]}}
        if operation == "get-authorizers":
            return {"Items": [self.authorizer]}
        if operation == "get-integrations":
            return {"Items": [self.integration]}
        if operation == "get-routes":
            return {"Items": self.routes}
        if operation == "get-stages":
            return {"Items": [self.stage]}
        raise AssertionError(operation)


def fake_download(url, destination, expected_hash):
    with zipfile.ZipFile(destination, "w") as archive:
        archive.writestr("handler.py", "# source, never executed")


def fake_scan(directory, image):
    return {"Results": [{"Target": "handler.py", "Packages": [], "Vulnerabilities": [], "Secrets": []}]}


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        # Keep temporary test artifacts in the checkout on restricted Windows
        # hosts. Python's system temporary directory may lack child access.
        self.previous_tempdir = tempfile.tempdir
        tempfile.tempdir = str(Path(__file__).parent / ".test-tmp")
        Path(tempfile.tempdir).mkdir(exist_ok=True)

    def tearDown(self):
        Path(tempfile.tempdir).rmdir()
        tempfile.tempdir = self.previous_tempdir

    def run_assessment(self, aws=None, config=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work, output = root / "artifacts", root / "reports"
            work.mkdir()
            output.mkdir()
            with patch.object(assessment, "download_zip", fake_download), patch.object(assessment, "scan_artifact", fake_scan):
                result = assessment.assess(config or baseline(), "us-east-1", work, output, "trivy@sha256:test", aws=aws or FakeAWS())
            self.assertFalse(work.exists(), "Downloaded code must be removed after assessment")
            self.assertEqual(json.loads((output / "aws-assessment.json").read_text()), result)
            return result

    def test_source_only_lambda_reports_limited_package_coverage(self):
        result = self.run_assessment()
        self.assertEqual(result["coverage"], "complete")
        self.assertEqual(result["summary"]["FAIL"], 0)
        self.assertGreater(result["summary"]["REVIEW"], 0)
        self.assertEqual(result["functions"][0]["artifacts"][0]["package_coverage"], "no_identified_packages")
        text = json.dumps(result)
        self.assertNotIn("never-export-this", text)
        self.assertNotIn("presigned-secret", text)

    def test_changed_deployment_is_incomplete(self):
        aws = FakeAWS()
        aws.changed = True
        result = self.run_assessment(aws)
        self.assertEqual(result["coverage"], "incomplete")
        self.assertEqual(result["functions"][0]["coverage"], "incomplete")

    def test_denied_api_read_is_not_clean_and_other_scans_survive(self):
        aws = FakeAWS()
        aws.denied = "get-routes"
        result = self.run_assessment(aws)
        self.assertEqual(result["coverage"], "incomplete")
        self.assertEqual(result["functions"][0]["coverage"], "complete")
        self.assertEqual(result["errors"][0]["aws_code"], "AccessDeniedException")

    def test_exact_layer_version_scanned_separately(self):
        aws = FakeAWS()
        aws.layers = [{"Arn": LAYER}]
        config = baseline()
        config["layer_version_arns"] = [LAYER]
        result = self.run_assessment(aws, config)
        self.assertEqual(result["coverage"], "complete")
        self.assertEqual(result["functions"][0]["artifacts"][1]["code_sha256"], "layerhash")

    def test_unlisted_layer_cannot_silently_disappear(self):
        aws = FakeAWS()
        aws.layers = [{"Arn": LAYER}]
        self.assertEqual(self.run_assessment(aws)["coverage"], "incomplete")

    def test_auth_and_integration_drift_produces_findings(self):
        aws = FakeAWS()
        aws.routes[0]["AuthorizationType"] = "NONE"
        aws.integration["IntegrationUri"] = ARN.replace("customer", "admin")
        aws.authorizer["JwtConfiguration"]["Audience"] = ["other-client"]
        result = self.run_assessment(aws)
        self.assertEqual(result["coverage"], "complete")
        failures = {c["check"] for c in result["checks"] if c["status"] == "FAIL"}
        self.assertIn("jwt_authorization", failures)
        self.assertIn("lambda_integration", failures)

    def test_unexpected_route_and_disabled_route_throttle_are_findings(self):
        aws = FakeAWS()
        aws.routes.append({"RouteKey": "$default", "AuthorizationType": "NONE"})
        aws.stage["RouteSettings"] = {"GET /instances": {"ThrottlingRateLimit": 0}}
        failures = {c["check"] for c in self.run_assessment(aws)["checks"] if c["status"] == "FAIL"}
        self.assertIn("route_inventory", failures)
        self.assertIn("throttle_GET /instances", failures)

    def test_public_lambda_policy_is_a_finding(self):
        aws = FakeAWS()
        aws.policy = {"Statement": [{"Effect": "Allow", "Principal": "*", "Action": "lambda:InvokeFunction"}]}
        self.assertTrue(any(c["check"] == "invocation_policy" and c["status"] == "FAIL" for c in self.run_assessment(aws)["checks"]))

    def test_admin_policy_requires_admin_source_path(self):
        config = baseline()
        config["functions"] = {"admin": {"arn": ARN, "role_arn": ROLE}}
        self.assertTrue(any(c["check"] == "invocation_policy" and c["status"] == "FAIL" for c in self.run_assessment(config=config)["checks"]))

    def test_policy_escalation_and_complement_grants_are_findings(self):
        report = {"checks": []}
        assessment.inspect_policy(report, ROLE, {"Statement": [
            {"Effect": "Allow", "Action": "iam:PassRole", "Resource": "*"},
            {"Effect": "Allow", "NotAction": "iam:DeleteRole", "Resource": "*"}]})
        self.assertEqual(sum(c["status"] == "FAIL" for c in report["checks"]), 2)

    def test_secret_report_allowlist_removes_values_and_source(self):
        raw = {"Metadata": {"secret": "hidden"}, "Results": [{"Target": "handler.py",
               "Secrets": [{"RuleID": "aws-key", "Severity": "HIGH", "Match": "hidden",
                            "Code": {"Lines": [{"Content": "hidden"}]}, "StartLine": 12}]}]}
        safe = assessment.sanitize_trivy(raw)
        self.assertNotIn("hidden", json.dumps(safe))
        self.assertEqual(safe["Results"][0]["Secrets"][0]["StartLine"], 12)

    def test_zip_traversal_and_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, mode in [("../outside", 0), ("/absolute", 0), ("safe", 0o120777 << 16), ("..\\outside", 0)]:
                archive = root / "archive.zip"
                with zipfile.ZipFile(archive, "w") as package:
                    entry = zipfile.ZipInfo(name)
                    entry.external_attr = mode
                    package.writestr(entry, "data")
                with self.assertRaises(assessment.CoverageError):
                    assessment.extract_zip(archive, root / "output")
            self.assertFalse((root / "output").exists())

    def test_download_hash_checked_without_logging_presigned_url(self):
        class Response(io.BytesIO):
            def geturl(self):
                return "https://download.example/?secret=signed"
        with tempfile.TemporaryDirectory() as temporary, patch.object(assessment.urllib.request, "urlopen", return_value=Response(b"zipdata")):
            with self.assertRaisesRegex(assessment.CoverageError, "artifact_code_sha256_mismatch"):
                assessment.download_zip("https://download.example/?secret=signed", Path(temporary) / "a.zip", "wronghash")
        with tempfile.TemporaryDirectory() as temporary, patch.object(assessment.urllib.request, "urlopen", return_value=Response(b"zipdata")):
            expected = base64.b64encode(hashlib.sha256(b"zipdata").digest()).decode()
            assessment.download_zip("https://download.example/?secret=signed", Path(temporary) / "a.zip", expected)

    def test_failed_scanner_is_incomplete_and_artifacts_removed(self):
        with patch.object(assessment, "scan_artifact", side_effect=RuntimeError("sensitive stderr")):
            # Invoke without the helper's fake scan patch.
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                work, output = root / "artifacts", root / "reports"
                work.mkdir()
                output.mkdir()
                with patch.object(assessment, "download_zip", fake_download):
                    result = assessment.assess(baseline(), "us-east-1", work, output, "image", FakeAWS())
                self.assertEqual(result["coverage"], "incomplete")
                self.assertNotIn("sensitive stderr", json.dumps(result))
                self.assertFalse(work.exists())


if __name__ == "__main__":
    unittest.main()
