"""Bounded GET-only recovery fixtures; no real profile, account or tool access."""

import base64
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from diagnostics import Diagnostics, Stage
import provision
from provision import ApiFailure, ProvisioningApiError, ProvisioningError
from test_sign import Client, Tools, profile


CANARY = "PRIVATE-PROFILE-TOKEN-PATH-MUST-NOT-ESCAPE"


def profile_path(identifier):
    return "profiles/" + identifier + "?include=bundleId&fields%5BbundleIds%5D=identifier"


def linkages(ids=("PROFILE1",)):
    return {"data": [{"type": "profiles", "id": value} for value in ids],
            "links": {"next": None}, "meta": {"paging": {"total": len(ids)}}}


def resource(identifier="PROFILE1", state="ACTIVE"):
    return {"data": {"type": "profiles", "id": identifier, "attributes": {
        "profileType": "IOS_APP_STORE", "profileState": state,
        "profileContent": base64.b64encode(b"SYNTHETIC-PROFILE").decode()},
        "relationships": {"bundleId": {"data": {"type": "bundleIds", "id": "BUNDLE1"}}}},
        "included": [{"type": "bundleIds", "id": "BUNDLE1", "attributes": {"identifier": "com.example.test"}}]}


class RecoveryClient(Client):
    def __init__(self, category=ApiFailure.UPSTREAM, linkage=None, resources=None):
        super().__init__("SYNTHETIC")
        self.category = category
        self.linkage = linkages() if linkage is None else linkage
        self.resources = {"PROFILE1": resource()} if resources is None else resources
        self.reads = []
        self.collections = []

    def collection(self, path, **query):
        self.collections.append(path)
        if path == "bundleIds/BUNDLE1/profiles":
            raise ProvisioningApiError(CANARY, self.category)
        return super().collection(path, **query)

    def request(self, path, *, body=None, timeout=None, deadline=None):
        if body is not None:
            raise AssertionError("Recovery must never mutate provisioning state")
        self.reads.append((path, timeout, deadline))
        if path == "bundleIds/BUNDLE1/relationships/profiles?limit=25":
            return self.linkage
        endpoint = urlsplit(path)
        identifier = endpoint.path.removeprefix("profiles/")
        if (endpoint.path != "profiles/" + identifier or identifier not in self.resources
                or parse_qs(endpoint.query) != {"include": ["bundleId"], "fields[bundleIds]": ["identifier"]}):
            raise AssertionError("No unapproved profile path is allowed")
        result = self.resources[identifier]
        if isinstance(result, Exception):
            raise result
        return result


class ProfileFallbackTest(unittest.TestCase):
    def application(self, client, *, decoded=None, entitlements=None, diagnostics=None):
        class DecodeTools(Tools):
            def __call__(self, stage, args, env, timeout=180):
                if decoded is not None and stage == "Validate app-specific provisioning profile":
                    return plistlib.dumps(decoded)
                return super().__call__(stage, args, env, timeout)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result, _ = provision.application_profile(client, b"SYNTHETIC-CERT", "ABCDEFGHIJ",
                                                       "com.example.test", root, DecodeTools(root), {},
                                                       entitlements or {}, diagnostics)
            return result

    def test_only_primary_transport_and_5xx_use_linkage_then_stop_after_valid_profile(self):
        for category in (ApiFailure.TRANSPORT, ApiFailure.UPSTREAM):
            with self.subTest(category=category):
                client = RecoveryClient(category, linkage=linkages(("PROFILE1", "UNREAD")))
                self.assertEqual(self.application(client)["UUID"], profile()["UUID"])
                self.assertEqual([row[0] for row in client.reads], [
                    "bundleIds/BUNDLE1/relationships/profiles?limit=25", profile_path("PROFILE1")])
                self.assertTrue(all(0 < timeout <= 20 for _, timeout, _ in client.reads))
                self.assertTrue(all(deadline == client.reads[0][2] for _, _, deadline in client.reads))

    def test_other_primary_api_categories_never_fallback(self):
        for category in set(ApiFailure) - {ApiFailure.TRANSPORT, ApiFailure.UPSTREAM}:
            with self.subTest(category=category):
                client = RecoveryClient(category)
                with self.assertRaises(ProvisioningApiError) as caught:
                    self.application(client)
                self.assertIs(caught.exception.category, category)
                self.assertEqual(client.reads, [])

    def test_linkage_must_be_complete_bounded_unique_and_path_safe_before_any_profile_get(self):
        invalid = [{}, {"data": None}, linkages(()), linkages(("PROFILE1", "PROFILE1")),
                   linkages(tuple("PROFILE" + str(i) for i in range(26)))]
        for identifier in ("../escape", "x/y", "x?query", "x#fragment", "x%2fy", "x\n" + CANARY):
            invalid.append(linkages(("PROFILE1", identifier)))
        for change in ({"meta": {}}, {"meta": {"paging": {"total": True}}},
                       {"meta": {"paging": {"total": 2}}}, {"links": None},
                       {"links": {"next": "https://private.invalid/" + CANARY}}):
            invalid.append(dict(linkages(), **change))
        wrong_type = linkages(); wrong_type["data"][0]["type"] = "certificates"; invalid.append(wrong_type)
        for linkage in invalid:
            with self.subTest(shape=repr(linkage)), self.assertRaises(ProvisioningError) as caught:
                client = RecoveryClient(linkage=linkage)
                self.application(client)
            self.assertEqual(len(client.reads), 1)
            self.assertNotIn(CANARY, str(caught.exception))

    def test_single_resource_identity_and_expanded_owner_relationship_must_match(self):
        invalid = [{}, {"data": []}, {"data": [resource()["data"]]}]
        for key, value in (("type", "certificates"), ("id", "OTHER"), ("attributes", None),
                           ("relationships", [])):
            row = resource(); row["data"][key] = value; invalid.append(row)
        for key, value in (("profileType", {}), ("profileType", "UNKNOWN"), ("profileState", "UNKNOWN"),
                           ("profileContent", ""), ("profileContent", None)):
            row = resource(); row["data"]["attributes"][key] = value; invalid.append(row)
        for owner in (None, {}, {"data": {"type": "profiles", "id": "BUNDLE1"}},
                      {"data": {"type": "bundleIds", "id": "FOREIGN"}}):
            row = resource(); row["data"]["relationships"]["bundleId"] = owner; invalid.append(row)
        row = resource(); del row["data"]["relationships"]; invalid.append(row)
        for included in (None, [], [None], [resource()["included"][0]] * 2,
                         [{"type": "profiles", "id": "BUNDLE1", "attributes": {"identifier": "com.example.test"}}],
                         [{"type": "bundleIds", "id": "FOREIGN", "attributes": {"identifier": "com.example.test"}}],
                         [{"type": "bundleIds", "id": "BUNDLE1", "attributes": {"identifier": "other.app"}}]):
            row = resource(); row["included"] = included; invalid.append(row)
        for row in invalid:
            with self.subTest(shape=repr(row)), self.assertRaises(ProvisioningApiError) as caught:
                client = RecoveryClient(resources={"PROFILE1": row})
                self.application(client)
            self.assertIs(caught.exception.category, ApiFailure.SCHEMA)
            self.assertEqual(len(client.reads), 2)

    def test_bundle_path_is_validated_before_any_related_api_request(self):
        class ForeignBundle(RecoveryClient):
            def collection(self, path, **query):
                if path == "bundleIds":
                    return [{"id": "BUNDLE1/../" + CANARY, "attributes": {"identifier": "com.example.test"}}]
                if path.startswith("bundleIds/"):
                    raise AssertionError("Invalid bundle ID must never enter a related path")
                return super().collection(path, **query)

        with self.assertRaises(ProvisioningApiError) as caught:
            self.application(ForeignBundle())
        self.assertIs(caught.exception.category, ApiFailure.SCHEMA)
        self.assertNotIn(CANARY, str(caught.exception))

    def test_decoded_app_team_certificate_expiry_and_entitlement_guards_still_apply(self):
        invalid = []
        for key, value in (("TeamIdentifier", ["OTHERTEAM1"]), ("DeveloperCertificates", [b"OTHER"]),
                           ("ExpirationDate", datetime.now() - timedelta(days=1)),
                           ("ProvisionedDevices", ["DEVICE"]), ("ProvisionsAllDevices", True)):
            row = profile(); row[key] = value; invalid.append(row)
        for key, value in (("application-identifier", "OLDPREFIX1.other.app"),
                           ("com.apple.developer.team-identifier", "OTHERTEAM1"), ("get-task-allow", True)):
            row = profile(); row["Entitlements"][key] = value; invalid.append(row)
        for decoded in invalid:
            with self.subTest(keys=list(decoded)), self.assertRaisesRegex(ProvisioningError, "No matching existing"):
                self.application(RecoveryClient(), decoded=decoded)
        with self.assertRaisesRegex(ProvisioningError, "No matching existing"):
            # The bundle already has this capability; the profile still must carry it.
            client = RecoveryClient()
            original = client.collection
            client.collection = lambda path, **query: [{"attributes": {"capabilityType": "PUSH_NOTIFICATIONS"}}] \
                if path.endswith("/bundleIdCapabilities") else original(path, **query)
            self.application(client, entitlements={"aps-environment": "development"})

    def test_partial_individual_failure_never_creates_and_stage_returns_to_lookup(self):
        client = RecoveryClient(linkage=linkages(("PROFILE1", "PROFILE2")), resources={
            "PROFILE1": resource(), "PROFILE2": ProvisioningApiError(CANARY, ApiFailure.UPSTREAM)})
        decoded = profile(); decoded["TeamIdentifier"] = ["OTHERTEAM1"]
        diagnostics = Diagnostics()
        with self.assertRaises(ProvisioningApiError):
            self.application(client, decoded=decoded, diagnostics=diagnostics)
        diagnostics.failed()
        self.assertEqual(diagnostics.values()[0], Stage.PROFILE_LOOKUP.value)
        self.assertEqual(len(client.reads), 3)

    def test_all_25_ineligible_resources_are_bounded_and_cannot_authorize_creation(self):
        identifiers = tuple("PROFILE" + str(i) for i in range(25))
        client = RecoveryClient(linkage=linkages(identifiers), resources={i: resource(i, "INVALID") for i in identifiers})
        with self.assertRaisesRegex(ProvisioningError, "No matching existing"):
            self.application(client)
        self.assertEqual(len(client.reads), 26)

    def test_operation_budget_limits_next_request_timeout_and_blocks_further_reads(self):
        now = [0]
        client = RecoveryClient(linkage=linkages(("PROFILE1", "PROFILE2", "PROFILE3")), resources={
            i: resource(i, "INVALID") for i in ("PROFILE1", "PROFILE2", "PROFILE3")})
        original = client.request

        def request(path, **kwargs):
            result = original(path, **kwargs)
            if path == profile_path("PROFILE1"):
                now[0] = 179.5
            elif path == profile_path("PROFILE2"):
                now[0] = 180
            return result

        client.request = request
        with patch.object(provision.time, "monotonic", side_effect=lambda: now[0]), \
                self.assertRaisesRegex(ProvisioningError, "operation budget"):
            self.application(client)
        self.assertEqual([row[0] for row in client.reads], [
            "bundleIds/BUNDLE1/relationships/profiles?limit=25", profile_path("PROFILE1"), profile_path("PROFILE2")])
        self.assertEqual(client.reads[-1][1], 0.5)

    def test_decoding_after_operation_budget_cannot_accept_a_matching_profile(self):
        now = [0]
        client = RecoveryClient()
        timeouts = []
        original = Tools.__call__

        def slow_decode(tools, stage, args, env, timeout=180):
            if stage == "Validate app-specific provisioning profile":
                timeouts.append(timeout)
                now[0] = 181
            return original(tools, stage, args, env, timeout)

        with patch.object(provision.time, "monotonic", side_effect=lambda: now[0]), \
                patch.object(Tools, "__call__", slow_decode), \
                self.assertRaisesRegex(ProvisioningError, "operation budget"):
            self.application(client)
        self.assertEqual(timeouts, [180])
        self.assertEqual(len(client.reads), 2)

    def test_recovery_decode_receives_remaining_budget_and_api_rows_are_not_modified(self):
        now = [0]
        client = RecoveryClient()
        original_request, original_tools = client.request, Tools.__call__
        timeouts = []

        def request(path, **kwargs):
            result = original_request(path, **kwargs)
            if path == profile_path("PROFILE1"):
                now[0] = 170
            return result

        def decode(tools, stage, args, env, timeout=180):
            if stage == "Validate app-specific provisioning profile":
                timeouts.append(timeout)
            return original_tools(tools, stage, args, env, timeout)

        client.request = request
        before = json.dumps(client.resources)
        with patch.object(provision.time, "monotonic", side_effect=lambda: now[0]), \
                patch.object(Tools, "__call__", decode):
            self.assertEqual(self.application(client)["UUID"], profile()["UUID"])
        self.assertEqual(timeouts, [10])
        self.assertEqual(json.dumps(client.resources), before)

    def test_deadline_is_rechecked_after_identity_validation_before_acceptance(self):
        now = [0]

        def match(*args):
            now[0] = 181
            return True

        with patch.object(provision.time, "monotonic", side_effect=lambda: now[0]), \
                patch.object(provision, "profile_matches", side_effect=match), \
                self.assertRaisesRegex(ProvisioningError, "operation budget"):
            self.application(RecoveryClient())

    def test_socket_timeout_and_body_reads_observe_remaining_operation_budget(self):
        client = provision.AppStoreClient("SYNTHETIC")
        response = io.BytesIO(json.dumps(resource()).encode())
        with patch.object(client.opener, "open", return_value=response) as opened, \
                patch.object(provision.time, "monotonic", side_effect=[90, 90, 91, 100]), \
                self.assertRaisesRegex(ProvisioningError, "operation budget"):
            client.request("profiles/PROFILE1", timeout=20, deadline=100)
        self.assertEqual(opened.call_args.kwargs["timeout"], 10)
        self.assertTrue(response.closed)
        with patch.object(client.opener, "open", side_effect=TimeoutError(CANARY)), \
                self.assertRaises(ProvisioningApiError) as caught:
            client.request("profiles/PROFILE1")
        self.assertIs(caught.exception.category, ApiFailure.TRANSPORT)
        self.assertNotIn(CANARY, str(caught.exception))

    def test_primary_healthy_creation_failure_is_not_retried_or_recovered(self):
        class CreateFailure(Client):
            def __init__(self):
                super().__init__("SYNTHETIC"); self.calls = []

            def request(self, path, *, body=None):
                self.calls.append((path, body))
                self.assert_post(body)
                raise ProvisioningApiError(CANARY, ApiFailure.UPSTREAM)

            def assert_post(self, body):
                if body is None:
                    raise AssertionError("A failed POST must never cause a recovery GET")

        client = CreateFailure()
        with self.assertRaises(ProvisioningApiError):
            self.application(client)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][0], "profiles")


if __name__ == "__main__":
    unittest.main()
