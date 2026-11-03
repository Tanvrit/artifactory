"""App-specific App Store profiles; never copies another app's profile or creates a certificate."""

import base64
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import plistlib
import re
import time
import uuid
from diagnostics import CleanupStage, Diagnostics, Stage
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ProvisioningError(Exception):
    pass


class ApiFailure(Enum):
    BAD_REQUEST = "api-http-400"
    UNAUTHENTICATED = "api-http-401"
    FORBIDDEN = "api-http-403"
    NOT_FOUND = "api-http-404"
    CONFLICT = "api-http-409"
    RATE_LIMITED = "api-http-429"
    UPSTREAM = "api-http-5xx"
    OTHER_HTTP = "api-http-other"
    TRANSPORT = "api-transport"
    SCHEMA = "api-response-schema"


class ProvisioningApiError(ProvisioningError):
    def __init__(self, message, category):
        super().__init__(message)
        self.category = category if type(category) is ApiFailure else ApiFailure.SCHEMA


class NoRedirect(HTTPRedirectHandler):
    """A provisioning JWT must never be forwarded to a redirected endpoint."""

    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def b64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def raw_es256(signature):
    """Convert OpenSSL's short ASN.1 ECDSA sequence to JWT's fixed-width R || S."""
    if len(signature) < 8 or signature[0] != 0x30 or signature[1] != len(signature) - 2:
        raise ProvisioningError("App Store key did not produce an ES256 signature")
    offset, values = 2, []
    for _ in range(2):
        if offset + 2 > len(signature) or signature[offset] != 2:
            raise ProvisioningError("Invalid ES256 signature structure")
        size = signature[offset + 1]
        value = signature[offset + 2:offset + 2 + size]
        if len(value) != size or not size or value[0] & 0x80:
            raise ProvisioningError("Invalid ES256 signature integer")
        value = value.lstrip(b"\0")
        if len(value) > 32:
            raise ProvisioningError("App Store key must use the P-256 curve")
        values.append(value.rjust(32, b"\0"))
        offset += 2 + size
    if offset != len(signature):
        raise ProvisioningError("Unexpected ES256 signature data")
    return b"".join(values)


def authentication_token(private, key, key_id, issuer, execute, env):
    now = int(time.time())
    header = b64url(json.dumps({"alg": "ES256", "kid": key_id, "typ": "JWT"}, separators=(",", ":")).encode())
    claims = b64url(json.dumps({"iss": issuer, "iat": now - 30, "exp": now + 900,
                               "aud": "appstoreconnect-v1"}, separators=(",", ":")).encode())
    message = private / "api-claims"
    message.write_bytes(f"{header}.{claims}".encode())
    signature = execute("Authenticate provisioning request", ["openssl", "dgst", "-sha256", "-sign", str(key), str(message)], env)
    return f"{header}.{claims}.{b64url(raw_es256(signature))}"


class AppStoreClient:
    origin = "https://api.appstoreconnect.apple.com/v1/"

    def __init__(self, token):
        self.token = token
        self.opener = build_opener(NoRedirect())

    def request(self, path, *, body=None, timeout=60, deadline=None):
        url = path if path.startswith(self.origin) else self.origin + path
        if not url.startswith(self.origin) or ".." in url:
            raise ProvisioningError("Unexpected provisioning API URL")
        data = json.dumps(body).encode() if body is not None else None
        request = Request(url, data=data, headers={"Authorization": " ".join(("Bearer", self.token)),
                          "Content-Type": "application/json"}, method="POST" if body is not None else "GET")
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProvisioningError("Existing profile lookup exceeded its operation budget")
            timeout = min(timeout, remaining)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                if deadline is None:
                    raw = response.read(4_000_001)
                else:
                    parts, size = [], 0
                    while size <= 4_000_000:
                        if time.monotonic() >= deadline:
                            raise ProvisioningError("Existing profile lookup exceeded its operation budget")
                        chunk = response.read1(min(65536, 4_000_001 - size))
                        if time.monotonic() >= deadline:
                            raise ProvisioningError("Existing profile lookup exceeded its operation budget")
                        if not chunk:
                            break
                        parts.append(chunk)
                        size += len(chunk)
                    raw = b"".join(parts)
        except HTTPError as error:
            status = error.code
            error.close()
            categories = {400: ApiFailure.BAD_REQUEST, 401: ApiFailure.UNAUTHENTICATED,
                          403: ApiFailure.FORBIDDEN, 404: ApiFailure.NOT_FOUND,
                          409: ApiFailure.CONFLICT, 429: ApiFailure.RATE_LIMITED}
            category = categories.get(status, ApiFailure.UPSTREAM if 500 <= status <= 599 else ApiFailure.OTHER_HTTP)
            raise ProvisioningApiError(f"Provisioning API request failed (HTTP {status}); response withheld", category) from None
        except (URLError, TimeoutError, ConnectionError):
            raise ProvisioningApiError("Provisioning API connection failed; diagnostics withheld", ApiFailure.TRANSPORT) from None
        if len(raw) > 4_000_000:
            raise ProvisioningError("Provisioning API response exceeded its bound")
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError):
            raise ProvisioningApiError("Provisioning API response could not be decoded", ApiFailure.SCHEMA) from None

    def collection(self, path, *, limit=200, **query):
        parameters = dict(query)
        if limit is not None:
            parameters["limit"] = limit
        suffix = "?" + urlencode(parameters) if parameters else ""
        page = self.request(path + suffix)
        rows = []
        for _ in range(5):
            if not isinstance(page, dict) or not isinstance(page.get("data"), list):
                raise ProvisioningApiError("Provisioning API collection has invalid shape", ApiFailure.SCHEMA)
            rows.extend(page["data"])
            next_page = page.get("links", {}).get("next")
            if not next_page:
                return rows
            if not next_page.startswith(self.origin):
                raise ProvisioningError("Unexpected provisioning pagination URL")
            page = self.request(next_page)
        raise ProvisioningError("Provisioning pagination exceeded its bound")


PROFILE_LOOKUP_LIMIT = 25
PROFILE_LOOKUP_BUDGET = 180
PROFILE_READ_TIMEOUT = 20
RESOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
PROFILE_TYPES = frozenset({
    "IOS_APP_DEVELOPMENT", "IOS_APP_STORE", "IOS_APP_ADHOC", "IOS_APP_INHOUSE",
    "MAC_APP_DEVELOPMENT", "MAC_APP_STORE", "MAC_APP_DIRECT", "TVOS_APP_DEVELOPMENT",
    "TVOS_APP_STORE", "TVOS_APP_ADHOC", "TVOS_APP_INHOUSE", "MAC_CATALYST_APP_DEVELOPMENT",
    "MAC_CATALYST_APP_STORE", "MAC_CATALYST_APP_DIRECT",
})


def resource_id(value):
    return type(value) is str and RESOURCE_ID.fullmatch(value) is not None


def remaining_profile_budget(deadline):
    if deadline is None:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ProvisioningError("Existing profile lookup exceeded its operation budget")
    return remaining


def linked_profile_rows(client, bundle_id, bundle, diagnostics):
    """GET-only recovery; the operation budget is not a slow-drip wall deadline."""
    if not resource_id(bundle_id):
        raise ProvisioningApiError("Provisioning bundle identity has invalid shape", ApiFailure.SCHEMA)
    deadline = time.monotonic() + PROFILE_LOOKUP_BUDGET

    def read(path):
        diagnostics.begin(Stage.PROFILE_LOOKUP)
        remaining = remaining_profile_budget(deadline)
        result = client.request(path, timeout=min(PROFILE_READ_TIMEOUT, remaining), deadline=deadline)
        remaining_profile_budget(deadline)
        return result

    page = read(f"bundleIds/{bundle_id}/relationships/profiles?limit={PROFILE_LOOKUP_LIMIT}")
    rows = page.get("data") if type(page) is dict else None
    links = page.get("links") if type(page) is dict else None
    meta = page.get("meta") if type(page) is dict else None
    paging = meta.get("paging") if type(meta) is dict else None
    total = paging.get("total") if type(paging) is dict else None
    if (type(rows) is not list or len(rows) > PROFILE_LOOKUP_LIMIT or type(links) is not dict
            or links.get("next") is not None or type(total) is not int or total != len(rows)):
        raise ProvisioningApiError("Provisioning profile linkage is incomplete or invalid", ApiFailure.SCHEMA)
    seen = set()
    for row in rows:
        if (type(row) is not dict or row.get("type") != "profiles" or not resource_id(row.get("id"))
                or row["id"] in seen):
            raise ProvisioningApiError("Provisioning profile linkage has invalid identity", ApiFailure.SCHEMA)
        seen.add(row["id"])
    for linkage in rows:
        # Apple documents relationship linkage as optional without expansion.
        query = urlencode({"include": "bundleId", "fields[bundleIds]": "identifier"})
        response = read("profiles/" + linkage["id"] + "?" + query)
        row = response.get("data") if type(response) is dict else None
        attributes = row.get("attributes") if type(row) is dict else None
        if (type(row) is not dict or row.get("type") != "profiles" or row.get("id") != linkage["id"]
                or type(attributes) is not dict or type(attributes.get("profileType")) is not str
                or attributes["profileType"] not in PROFILE_TYPES
                or attributes.get("profileState") not in ("ACTIVE", "INVALID")):
            raise ProvisioningApiError("Provisioning profile resource has invalid identity", ApiFailure.SCHEMA)
        relationships = row.get("relationships")
        if type(relationships) is not dict:
            raise ProvisioningApiError("Provisioning profile relationship has invalid shape", ApiFailure.SCHEMA)
        relationship = relationships.get("bundleId")
        owner = relationship.get("data") if type(relationship) is dict else None
        if type(owner) is not dict or owner.get("type") != "bundleIds" or owner.get("id") != bundle_id:
            raise ProvisioningApiError("Provisioning profile owner is unverified", ApiFailure.SCHEMA)
        included = response.get("included")
        if type(included) is not list or len(included) != 1:
            raise ProvisioningApiError("Provisioning expanded bundle identity is unverified", ApiFailure.SCHEMA)
        included_owner = included[0]
        included_attributes = included_owner.get("attributes") if type(included_owner) is dict else None
        if (type(included_owner) is not dict or included_owner.get("type") != "bundleIds"
                or included_owner.get("id") != bundle_id or type(included_attributes) is not dict
                or included_attributes.get("identifier") != bundle):
            raise ProvisioningApiError("Provisioning expanded bundle identity does not match", ApiFailure.SCHEMA)
        if attributes["profileType"] == "IOS_APP_STORE" and attributes["profileState"] == "ACTIVE":
            if type(attributes.get("profileContent")) is not str or not attributes["profileContent"]:
                raise ProvisioningApiError("Provisioning profile content is missing", ApiFailure.SCHEMA)
        yield row, deadline
    # Recovery reads never turn an unavailable collection into permission to create.
    raise ProvisioningError("No matching existing profile was found through bounded recovery")


def existing_profile_rows(client, bundle_id, bundle, diagnostics):
    try:
        rows = client.collection(f"bundleIds/{bundle_id}/profiles")
    except ProvisioningApiError as error:
        if error.category not in (ApiFailure.TRANSPORT, ApiFailure.UPSTREAM):
            raise
        yield from linked_profile_rows(client, bundle_id, bundle, diagnostics)
    else:
        for row in rows:
            yield row, None


def profile_matches(profile, certificate, team, bundle, required_entitlements):
    entitlements = profile.get("Entitlements", {})
    app_identifier = entitlements.get("application-identifier", "")
    prefixes = profile.get("ApplicationIdentifierPrefix", [])
    expiration = profile.get("ExpirationDate")
    if not isinstance(expiration, datetime):
        return False
    expiration = expiration.replace(tzinfo=timezone.utc) if expiration.tzinfo is None else expiration
    if (profile.get("TeamIdentifier") != [team] or certificate not in profile.get("DeveloperCertificates", [])
            or not any(app_identifier == prefix + "." + bundle for prefix in prefixes)
            or entitlements.get("com.apple.developer.team-identifier") != team
            or entitlements.get("get-task-allow") is not False
            or profile.get("ProvisionedDevices") is not None or profile.get("ProvisionsAllDevices")
            or expiration.timestamp() <= time.time() + 86400):
        return False
    for name in required_entitlements:
        if name == "aps-environment":
            if entitlements.get(name) != "production":
                return False
        elif name not in entitlements:
            return False
    return True


def application_profile(client, certificate, team, bundle, private, execute, env, entitlements, diagnostics=None):
    diagnostics = diagnostics or Diagnostics()
    # Validate declared capabilities before making any Apple-side changes.
    diagnostics.begin(Stage.CAPABILITIES)
    supported = {"aps-environment": "PUSH_NOTIFICATIONS",
                 "com.apple.developer.associated-domains": "ASSOCIATED_DOMAINS"}
    ignored = {"application-identifier", "com.apple.developer.team-identifier", "get-task-allow", "keychain-access-groups"}
    if set(entitlements) - set(supported) - ignored:
        raise ProvisioningError("Portable provisioning does not support one of this target's declared capabilities")
    diagnostics.begin(Stage.REGISTERED_CERTIFICATE)
    certificates = client.collection("certificates", **{"filter[certificateType]": "DISTRIBUTION,IOS_DISTRIBUTION"})
    matching = [row for row in certificates if base64.b64decode(row["attributes"]["certificateContent"], validate=True) == certificate]
    if len(matching) != 1:
        raise ProvisioningError("Imported distribution certificate is not uniquely registered with this API account")
    certificate_id = matching[0]["id"]
    diagnostics.begin(Stage.BUNDLE_LOOKUP)
    bundles = client.collection("bundleIds", **{"filter[identifier]": bundle})
    bundles = [row for row in bundles if row["attributes"].get("identifier") == bundle]
    if not bundles:
        diagnostics.begin(Stage.BUNDLE_CREATE)
        bundles = [client.request("bundleIds", body={"data": {"type": "bundleIds", "attributes": {
            "identifier": bundle, "name": "Tanvrit " + bundle, "platform": "IOS"}}})["data"]]
    if len(bundles) != 1:
        raise ProvisioningError("Application bundle identity is not unique")
    bundle_id = bundles[0]["id"]
    if not resource_id(bundle_id):
        raise ProvisioningApiError("Provisioning bundle identity has invalid shape", ApiFailure.SCHEMA)
    # Only capabilities already declared by this target are configured. Fail
    # closed on other capability types rather than silently broaden the App ID.
    diagnostics.begin(Stage.CAPABILITY_LOOKUP)
    # The related-capability endpoint permits an omitted limit. Use its
    # server default while retaining the same bounded next-page traversal.
    capabilities = client.collection(f"bundleIds/{bundle_id}/bundleIdCapabilities", limit=None)
    if any(not isinstance(row, dict) or not isinstance(row.get("attributes"), dict) or
           not isinstance(row["attributes"].get("capabilityType"), str) for row in capabilities):
        raise ProvisioningApiError("Provisioning API capabilities have invalid shape", ApiFailure.SCHEMA)
    enabled = {row["attributes"]["capabilityType"] for row in capabilities}
    for name in entitlements:
        capability = supported.get(name)
        if capability and capability not in enabled:
            diagnostics.begin(Stage.CAPABILITY_ENABLE)
            client.request("bundleIdCapabilities", body={"data": {"type": "bundleIdCapabilities",
                "attributes": {"capabilityType": capability}, "relationships": {
                    "bundleId": {"data": {"type": "bundleIds", "id": bundle_id}}}}})

    def decode(row, deadline=None):
        diagnostics.begin(Stage.PROFILE_VALIDATION)
        remaining_profile_budget(deadline)
        path = private / "application.mobileprovision"
        path.write_bytes(base64.b64decode(row["attributes"]["profileContent"], validate=True))
        remaining = remaining_profile_budget(deadline)
        args = ["security", "cms", "-D", "-i", str(path)]
        raw = execute("Validate app-specific provisioning profile", args, env) if remaining is None else \
            execute("Validate app-specific provisioning profile", args, env, timeout=min(180, remaining))
        remaining_profile_budget(deadline)
        profile = plistlib.loads(raw)
        remaining_profile_budget(deadline)
        return profile, path

    diagnostics.begin(Stage.PROFILE_LOOKUP)
    for row, deadline in existing_profile_rows(client, bundle_id, bundle, diagnostics):
        attributes = row["attributes"]
        if attributes.get("profileType") != "IOS_APP_STORE" or attributes.get("profileState") != "ACTIVE":
            continue
        profile, path = decode(row, deadline)
        matches = profile_matches(profile, certificate, team, bundle, entitlements)
        remaining_profile_budget(deadline)
        if matches:
            return profile, path
    # Existing invalid/expired profiles may retain their names. A new profile
    # gets a unique bounded name, while future builds still reuse its contents.
    name = "Tanvrit " + bundle[:50] + " " + hashlib.sha256(certificate).hexdigest()[:8] + " " + uuid.uuid4().hex[:8]
    diagnostics.begin(Stage.PROFILE_CREATE)
    row = client.request("profiles", body={"data": {"type": "profiles",
        "attributes": {"name": name, "profileType": "IOS_APP_STORE"}, "relationships": {
            "bundleId": {"data": {"type": "bundleIds", "id": bundle_id}},
            "certificates": {"data": [{"type": "certificates", "id": certificate_id}]}}}})["data"]
    profile, path = decode(row)
    if not profile_matches(profile, certificate, team, bundle, entitlements):
        raise ProvisioningError("Created provisioning profile does not match the application/certificate/team")
    return profile, path


def install_profile(profile, path, home, diagnostics=None):
    """Both current and legacy Xcode profile locations; never overwrite existing files."""
    diagnostics = diagnostics or Diagnostics()
    identifier = str(uuid.UUID(profile["UUID"]))
    created = []
    try:
        for relative in ("Library/Developer/Xcode/UserData/Provisioning Profiles",
                         "Library/MobileDevice/Provisioning Profiles"):
            directory = home / relative
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / (identifier + ".mobileprovision")
            try:
                descriptor = destination.open("xb")
            except FileExistsError:
                if destination.read_bytes() != path.read_bytes():
                    raise ProvisioningError("Existing profile with the same UUID has different content") from None
                continue
            created.append(destination)
            with descriptor:
                destination.chmod(0o600)
                descriptor.write(path.read_bytes())
    except BaseException:
        diagnostics.failed()
        with diagnostics.cleaning(CleanupStage.PROFILES):
            for destination in created:
                destination.unlink(missing_ok=True)
        raise
    return created
