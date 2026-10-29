"""Real Xcode setting expansion in a synthetic project, without signing material."""

import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_sign import sign


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("xcodebuild"), "Xcode setting expansion requires macOS/Xcode")
class XcodeProfileScopeTest(unittest.TestCase):
    def test_app_profile_does_not_reach_resource_bundle(self):
        def identifier(number):
            return f"{number:024X}"

        objects = {}

        def put(number, isa, **values):
            objects[identifier(number)] = {"isa": isa, **values}

        put(1, "PBXProject", buildConfigurationList=identifier(4), compatibilityVersion="Xcode 14.0",
            developmentRegion="en", knownRegions=["en"], mainGroup=identifier(2),
            productRefGroup=identifier(3), projectDirPath="", projectRoot="", targets=[identifier(10), identifier(20)])
        put(2, "PBXGroup", children=[identifier(3)], sourceTree="<group>")
        put(3, "PBXGroup", children=[identifier(12), identifier(22)], name="Products", sourceTree="<group>")
        put(4, "XCConfigurationList", buildConfigurations=[identifier(5)], defaultConfigurationIsVisible=0,
            defaultConfigurationName="Release")
        put(5, "XCBuildConfiguration", name="Release", buildSettings={"SDKROOT": "iphoneos", "CODE_SIGN_STYLE": "Manual"})
        for number, name, kind, extension in ((10, "SyntheticApp", "application", "app"),
                                             (20, "SyntheticResources", "bundle", "bundle")):
            put(number, "PBXNativeTarget", buildConfigurationList=identifier(number + 3), buildPhases=[],
                buildRules=[], dependencies=[], name=name, productName=name, productReference=identifier(number + 2),
                productType="com.apple.product-type." + kind)
            put(number + 2, "PBXFileReference", explicitFileType="wrapper.application" if extension == "app" else "wrapper.cfbundle",
                includeInIndex=0, path=name + "." + extension, sourceTree="BUILT_PRODUCTS_DIR")
            put(number + 3, "XCConfigurationList", buildConfigurations=[identifier(number + 4)],
                defaultConfigurationIsVisible=0, defaultConfigurationName="Release")
            put(number + 4, "XCBuildConfiguration", name="Release", buildSettings={"PRODUCT_NAME": name,
                "PRODUCT_BUNDLE_IDENTIFIER": "invalid.fixture." + name, "GENERATE_INFOPLIST_FILE": "YES",
                "IPHONEOS_DEPLOYMENT_TARGET": "17.0"})
        profile = {"Name": "Synthetic App Store Profile", "UUID": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}
        env = {key: value for key, value in os.environ.items() if key in ("HOME", "TMPDIR", "LANG")}
        env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        with tempfile.TemporaryDirectory(prefix="xcode-profile-fixture-") as folder:
            project = Path(folder) / "Fixture.xcodeproj"
            project.mkdir()
            (project / "project.pbxproj").write_bytes(plistlib.dumps({"archiveVersion": "1", "classes": {},
                "objectVersion": "56", "objects": objects, "rootObject": identifier(1)}))
            args = ["xcodebuild", "-project", str(project), "-alltargets", "-configuration", "Release", "-sdk", "iphoneos",
                    "-showBuildSettings", "-json", *sign.application_profile_settings(profile)]
            result = subprocess.run(args, env=env, capture_output=True, timeout=120)
            self.assertEqual(result.returncode, 0, "Synthetic Xcode settings failed; output withheld")
            settings = {row["buildSettings"]["WRAPPER_EXTENSION"]: row["buildSettings"] for row in json.loads(result.stdout)}
            self.assertEqual(set(settings), {"app", "bundle"})
            self.assertEqual(settings["app"]["PROVISIONING_PROFILE_SPECIFIER"], profile["Name"])
            self.assertEqual(settings["app"]["PROVISIONING_PROFILE"], profile["UUID"])
            self.assertEqual(settings["bundle"].get("PROVISIONING_PROFILE_SPECIFIER", ""), "")
            self.assertEqual(settings["bundle"].get("PROVISIONING_PROFILE", ""), "")


if __name__ == "__main__":
    unittest.main()
