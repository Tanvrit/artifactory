"""Build a bounded signing plan; never reads signing credentials or contacts Apple."""
import json
import os
import plistlib
import re
from pathlib import Path

BUNDLE = re.compile(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
METHODS = {"app-store", "ad-hoc", "development", "enterprise", "app-store-connect", "release-testing", "debugging"}


def signing_plan(bundle_id, additional_json, team_id, method):
    def valid(value):
        return isinstance(value, str) and len(value) <= 255 and BUNDLE.fullmatch(value)
    if not valid(bundle_id):
        raise ValueError("Invalid application bundle ID")
    if not re.fullmatch(r"[A-Z0-9]{10}", team_id):
        raise ValueError("Invalid Apple team ID")
    if method not in METHODS:
        raise ValueError("Unsupported export method")
    if len(additional_json) > 8192:
        raise ValueError("Additional bundle IDs are too large")
    additional = json.loads(additional_json)
    if not isinstance(additional, list) or len(additional) > 16:
        raise ValueError("Additional bundle IDs must be an array of at most 16 entries")
    if any(not valid(value) or not value.startswith(bundle_id + ".") for value in additional):
        raise ValueError("Extension bundle IDs must belong to the application")
    identifiers = [bundle_id] + additional
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Duplicate bundle ID")
    # Existing template's Match path uses AppStore profiles. Keep its default unchanged.
    profile = "match AppStore "
    return {
        "app_identifiers": ",".join(identifiers),
        "profile_specifier": profile + ("$(PRODUCT_BUNDLE_IDENTIFIER)" if additional else bundle_id),
        "export_options": {
            "method": method,
            "teamID": team_id,
            "signingStyle": "manual",
            "signingCertificate": "Apple Distribution",
            "provisioningProfiles": {value: profile + value for value in identifiers},
        },
    }


def main():
    plan = signing_plan(os.environ["BUNDLE_ID"], os.environ.get("ADDITIONAL_BUNDLE_IDS", "[]"),
                        os.environ["APPLE_TEAM_ID"], os.environ["EXPORT_METHOD"])
    output = Path(os.environ["EXPORT_OPTIONS_PATH"])
    output.write_bytes(plistlib.dumps(plan["export_options"], sort_keys=True))
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        for key in ("app_identifiers", "profile_specifier"):
            stream.write(f"{key}={plan[key]}\n")


if __name__ == "__main__":
    main()
