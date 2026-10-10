"""openapi.yaml must describe the routes handler.py really serves.

Run from dashboards/api:   python -m pytest -q tests/test_openapi.py
Needs PyYAML (the route checks fail without it, on purpose, so drift cannot go unnoticed).
The full 3.1 check also needs openapi-spec-validator and is skipped without it.
"""

import os
import re

import pytest

import yaml  # noqa: E402  (pip install pyyaml)

HERE = os.path.dirname(__file__)
SPEC = yaml.safe_load(open(os.path.join(HERE, "..", "openapi.yaml"), encoding="utf-8"))
SOURCE = open(os.path.join(HERE, "..", "handler.py"), encoding="utf-8").read()
METHODS = ("get", "post", "put", "delete", "patch")


def handler_routes(function):
    """Route keys such as 'GET /instances' that one router function answers."""
    body = SOURCE.split(f"def {function}(", 1)[1].split("\ndef ", 1)[0]
    return set(re.findall(r'key == "([A-Z]+ /[^"]*)"', body))


def spec_routes(lambda_name):
    return {
        f"{method.upper()} {path}"
        for path, item in SPEC["paths"].items()
        for method, op in item.items()
        if method in METHODS and op["x-lambda"] == lambda_name
    }


def test_customer_routes_match_the_handler():
    assert spec_routes("customer") == handler_routes("_customer_routes")


def test_admin_routes_match_the_handler():
    assert spec_routes("admin") == handler_routes("_admin_routes")


def test_admin_paths_are_all_served_by_the_admin_function():
    for path, item in SPEC["paths"].items():
        for method, op in item.items():
            if method in METHODS:
                assert (op["x-lambda"] == "admin") == path.startswith("/admin/"), f"{method} {path}"


def test_every_operation_has_a_unique_id_and_documents_the_standard_errors():
    ids = []
    for path, item in SPEC["paths"].items():
        for method, op in item.items():
            if method in METHODS:
                ids.append(op["operationId"])
                assert "401" in op["responses"], f"{method} {path} has no 401"
                assert op["x-required-roles"], f"{method} {path} lists no roles"
    assert len(ids) == len(set(ids))


def test_the_spec_is_valid_openapi_3_1():
    validator = pytest.importorskip("openapi_spec_validator")
    assert SPEC["openapi"].startswith("3.1")
    validator.validate(SPEC)


def terraform_routes():
    """Route key => function from the API stack (dashboards/api/terraform/api.tf)."""
    text = open(os.path.join(HERE, "..", "terraform", "api.tf"), encoding="utf-8").read()
    block = text.split("routes = {", 1)[1].split("\n  }", 1)[0]
    return dict(re.findall(r'"([A-Z]+ /[^"]*)"\s*=\s*"(customer|admin)"', block))


def test_the_terraform_stack_creates_exactly_the_spec_routes_on_the_right_function():
    expected = {
        f"{method.upper()} {path}": op["x-lambda"]
        for path, item in SPEC["paths"].items()
        for method, op in item.items()
        if method in METHODS
    }
    assert terraform_routes() == expected
