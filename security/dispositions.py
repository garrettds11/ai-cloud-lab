"""Apply exact, expiring false-positive reviews to immutable Greenbone feed data."""
import datetime
import json
import re

FEED_REPOSITORY = "registry.community.greenbone.net/community/vulnerability-tests"


def classify(items, resolved_image, review, today=None, platform="linux/amd64"):
    if review.get("schema_version") != 1:
        raise ValueError("Unsupported secret review schema")
    image = review["image"]
    if not re.fullmatch(re.escape(FEED_REPOSITORY) + r"@sha256:[a-f0-9]{64}", image):
        raise ValueError("Only immutable Greenbone feed test-data reviews are supported")
    expires = datetime.date.fromisoformat(review["expires"])
    reviewed = datetime.date.fromisoformat(review["reviewed"])
    if expires <= reviewed or (expires - reviewed).days > 31:
        raise ValueError("Secret reviews must expire within 31 days")
    matches = {}
    for entry in review["files"]:
        path = entry["path"]
        if not re.fullmatch(r"/var/lib/openvas/[0-9.]+/vt-data/nasl/[A-Za-z0-9_./-]+\.(nasl|inc)", path) or ".." in path:
            raise ValueError("Review path must identify a single feed test file")
        if not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"]) or not entry["justification"]:
            raise ValueError("Review lacks verified file hash or justification")
        for match in entry["matches"]:
            if match["rule"] not in {"private-key", "jwt-token"} or type(match["line"]) is not int or match["line"] < 1:
                raise ValueError("Unsupported test-data secret review")
            key = (path, match["rule"], match["line"])
            if key in matches:
                raise ValueError("Duplicate secret review")
            matches[key] = entry
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    result = []
    for original in items:
        item = dict(original)
        item.pop("disposition", None)
        entry = matches.get((item["path"], item["id"], item["line"]))
        if (item["kind"] == "secret" and entry and resolved_image == image
                and platform == review["platform"] == "linux/amd64" and reviewed <= today < expires):
            item["disposition"] = {
                "classification": "false_positive", "justification": entry["justification"],
                "reviewed": review["reviewed"], "expires": review["expires"],
                "image": image, "file_sha256": entry["sha256"],
                "evidence": "security/feed-secret-review.md",
            }
        result.append(item)
    return result


def load(path):
    return json.loads(path.read_text())
