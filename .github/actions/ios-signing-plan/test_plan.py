import json
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import plan


class SigningPlanTest(unittest.TestCase):
    def make(self, additional="[]", bundle="com.fixture.App", team="F846F22MFM", method="app-store"):
        return plan.signing_plan(bundle, additional, team, method)

    def test_default_keeps_existing_single_app_profile(self):
        result = self.make()
        self.assertEqual("com.fixture.App", result["app_identifiers"])
        self.assertEqual("match AppStore com.fixture.App", result["profile_specifier"])
        self.assertEqual({"com.fixture.App": "match AppStore com.fixture.App"}, result["export_options"]["provisioningProfiles"])

    def test_each_extension_has_distinct_profile_and_match_identifier(self):
        result = self.make('["com.fixture.App.ScreenShare","com.fixture.App.Notification"]')
        self.assertEqual("com.fixture.App,com.fixture.App.ScreenShare,com.fixture.App.Notification", result["app_identifiers"])
        self.assertEqual("match AppStore $(PRODUCT_BUNDLE_IDENTIFIER)", result["profile_specifier"])
        profiles = result["export_options"]["provisioningProfiles"]
        self.assertEqual(3, len(profiles))
        self.assertEqual("match AppStore com.fixture.App.ScreenShare", profiles["com.fixture.App.ScreenShare"])

    def test_rejects_unrelated_extension_and_duplicate(self):
        for value in ('["com.other.App.ScreenShare"]', '["com.fixture.App"]',
                      '["com.fixture.App.ScreenShare","com.fixture.App.ScreenShare"]'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.make(value)

    def test_rejects_non_array_and_non_string_values(self):
        for value in ('{}', 'null', '["com.fixture.App.ScreenShare",42]', 'false', '["'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.make(value)

    def test_rejects_control_shell_and_markup_injection(self):
        for value in ("com.fixture.App\napp_identifiers=bad", "com.fixture.App$(id)",
                      "com.fixture.<App>", "com.fixture.App/ScreenShare", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.make(bundle=value)

    def test_bounded_input(self):
        with self.assertRaises(ValueError):
            self.make(json.dumps([f"com.fixture.App.Extension{i}" for i in range(17)]))
        with self.assertRaises(ValueError):
            self.make(" " * 8193)
        with self.assertRaises(ValueError):
            self.make(bundle="com." + "x" * 252)

    def test_team_and_method_are_validated(self):
        for team in ("invalid", "F846F22MFM\n", ""):
            with self.subTest(team=team), self.assertRaises(ValueError):
                self.make(team=team)
        with self.assertRaises(ValueError):
            self.make(method='app-store</string>')

    def test_actual_action_entry_writes_parseable_plist_and_safe_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "outputs"
            options = root / "export.plist"
            with patch.dict("os.environ", {"BUNDLE_ID": "com.fixture.App",
                "ADDITIONAL_BUNDLE_IDS": '["com.fixture.App.ScreenShare"]',
                "APPLE_TEAM_ID": "F846F22MFM", "EXPORT_METHOD": "app-store",
                "EXPORT_OPTIONS_PATH": str(options), "GITHUB_OUTPUT": str(output)}):
                plan.main()
            self.assertIn("com.fixture.App.ScreenShare", plistlib.loads(options.read_bytes())["provisioningProfiles"])
            self.assertEqual(2, len(output.read_text().splitlines()))


if __name__ == "__main__":
    unittest.main()
