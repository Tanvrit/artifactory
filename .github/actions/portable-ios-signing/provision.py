"""App-specific App Store profiles; never copies another app's profile or creates a certificate."""

import base64
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import plistlib
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

    def request(self, path, *, body=None):
        url = path if path.startswith(self.origin) else self.origin + path
        if not url.startswith(self.origin) or ".." in url:
            raise ProvisioningError("Unexpected provisioning API URL")
        data = json.dumps(body).encode() if body is not None else None
        request = Request(url, data=data, headers={"Authorization": " ".join(("Bearer", self.token)),
                          "Content-Type": "application/json"}, method="POST" if body is not None else "GET")
        try:
            with self.opener.open(request, timeout=60) as response:
                raw = response.read(4_000_001)
        except HTTPError as error:
            status = error.code
            error.close()
            categories = {400: ApiFailure.BAD_REQUEST, 401: ApiFailure.UNAUTHENTICATED,
                          403: ApiFailure.FORBIDDEN, 404: ApiFailure.NOT_FOUND,
                          409: ApiFailure.CONFLICT, 429: ApiFailure.RATE_LIMITED}
            category = categories.get(status, ApiFailure.UPSTREAM if 500 <= status <= 599 else ApiFailure.OTHER_HTTP)
            raise ProvisioningApiError(f"Provisioning API request failed (HTTP {status}); response withheld", category) from None
        except URLError:
            raise ProvisioningApiError("Provisioning API connection failed; diagnostics withheld", ApiFailure.TRANSPORT) from None
        if len(raw) > 4_000_000:
            raise ProvisioningError("Provisioning API response exceeded its bound")
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError):
            raise ProvisioningApiError("Provisioning API response could not be decoded", ApiFailure.SCHEMA) from None

    def collection(self, path, **query):
        page = self.request(path + "?" + urlencode(dict(limit=200, **query)))
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
    # Only capabilities already declared by this target are configured. Fail
    # closed on other capability types rather than silently broaden the App ID.
    diagnostics.begin(Stage.CAPABILITY_LOOKUP)
    capabilities = client.collection(f"bundleIds/{bundle_id}/bundleIdCapabilities")
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

    def decode(row):
        diagnostics.begin(Stage.PROFILE_VALIDATION)
        path = private / "application.mobileprovision"
        path.write_bytes(base64.b64decode(row["attributes"]["profileContent"], validate=True))
        profile = plistlib.loads(execute("Validate app-specific provisioning profile", ["security", "cms", "-D", "-i", str(path)], env))
        return profile, path

    diagnostics.begin(Stage.PROFILE_LOOKUP)
    for row in client.collection(f"bundleIds/{bundle_id}/profiles"):
        attributes = row["attributes"]
        if attributes.get("profileType") != "IOS_APP_STORE" or attributes.get("profileState") != "ACTIVE":
            continue
        profile, path = decode(row)
        if profile_matches(profile, certificate, team, bundle, entitlements):
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
