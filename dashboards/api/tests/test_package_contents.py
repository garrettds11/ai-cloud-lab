"""The Lambda zip (terraform/functions.tf) must hold every module the handlers import, or the function
fails at start with ImportModuleError. Run from dashboards/api: python -m pytest -q tests/test_package_contents.py"""

import ast
import pathlib
import re

API = pathlib.Path(__file__).resolve().parent.parent
ENTRY_POINTS = ["handler.py", "customer.py", "admin.py", "spend_job.py"]


def zipped():
    text = (API / "terraform" / "functions.tf").read_text()
    return set(re.findall(r'filename\s*=\s*"([A-Za-z0-9_]+\.py)"', text))


def local_imports(path):
    names = set()
    for node in ast.walk(ast.parse((API / path).read_text())):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return {n + ".py" for n in names if (API / (n + ".py")).exists()}


def test_the_zip_holds_every_module_the_functions_import():
    files, seen, todo = zipped(), set(), list(ENTRY_POINTS)
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        todo += sorted(local_imports(f))
    assert seen <= files, "missing from the zip: " + ", ".join(sorted(seen - files))
