import pathlib
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).with_name("compile-sdk.py")


class CompileSdkTest(unittest.TestCase):
    def run_fixture(self, catalog=None, modules=(), override=""):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            subprocess.run(["git", "init", "-q", directory], check=True)
            if catalog is not None:
                path = root / "gradle/libs.versions.toml"
                path.parent.mkdir(parents=True)
                path.write_text(catalog)
            for index, text in enumerate(modules):
                path = root / str(index) / "build.gradle.kts"
                path.parent.mkdir()
                path.write_text(text)
                subprocess.run(["git", "add", str(path)], cwd=root, check=True)
            return subprocess.run(["python3", str(SCRIPT), override], cwd=root, capture_output=True, text=True)

    def test_catalog_selects_the_declared_platform(self):
        result = self.run_fixture('android-compileSdk = "37"\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "37")

    def test_core_and_sdk_literal_declarations(self):
        result = self.run_fixture(modules=("compileSdk = 37\n", "    compileSdk = 37\n"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "37")

    def test_comment_does_not_select_a_platform(self):
        result = self.run_fixture(modules=("// compileSdk = 36\ncompileSdk = 37\n",))
        self.assertEqual(result.stdout.strip(), "37")

    def test_conflicting_module_platforms_require_an_explicit_input(self):
        result = self.run_fixture(modules=("compileSdk = 36\n", "compileSdk = 37\n"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("compile-sdk input", result.stderr)

    def test_minor_catalog_and_explicit_input(self):
        for key in ("android-compileSdk", "compileSdk"):
            result = self.run_fixture(f'{key} = "37.1" # required minor\n')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "37.1")
        result = self.run_fixture(modules=("compileSdk = 36\n",), override="37.1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "37.1")

    def test_supported_minor_dsl_including_ktlint_whitespace(self):
        for module in (
            "compileSdk { version = release(37) { minorApiLevel = 1 } }",
            """androidLibrary {
                compileSdk {
                    version =
                        release(37) {
                            minorApiLevel = 1
                        }
                }
            }""",
            "compileSdk { /* explanation */ version = release(37) { minorApiLevel = 1; } }",
            "compileSdk {\nversion\n=\nrelease (\n37\n) {\nminorApiLevel\n=\n1\n}\n}",
        ):
            result = self.run_fixture(modules=(module, module))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "37.1")

    def test_single_quoted_catalog_is_authoritative(self):
        result = self.run_fixture("android-compileSdk = '37.1' # required minor\n", ("compileSdk = 36\n",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "37.1")

    def test_catalog_remains_authoritative(self):
        result = self.run_fixture('compileSdk = "37.1"\n', ("compileSdk = 36\n",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "37.1")

    def test_conflicting_minor_and_legacy_declarations_fail(self):
        minor = "compileSdk { version = release(37) { minorApiLevel = 1 } }"
        for other in (
            "compileSdk = 37\n",
            "compileSdk { version = release(37) { minorApiLevel = 0 } }",
            "compileSdk { version = release(38) { minorApiLevel = 1 } }",
        ):
            result = self.run_fixture(modules=(minor, other))
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("compile-sdk input", result.stderr)

    def test_comments_and_strings_are_not_declarations(self):
        result = self.run_fixture(modules=('''
            /* outer /* nested */ compileSdk = 36 */
            val example = "compileSdk = 35"
            val raw = """compileSdk { version = release(35) { minorApiLevel = 1 } }"""
            compileSdk { version = release(37) { minorApiLevel = 1 } }
        ''',))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "37.1")

    def test_unsupported_expressions_do_not_truncate_to_integer(self):
        for module in (
            "compileSdk = 37 + 1\n",
            "compileSdk = 37.1\n",
            "compileSdk = libs.versions.compileSdk.get().toInt()\n",
            "compileSdk { version = release(37) { minorApiLevel = requiredMinor } }",
            "compileSdk { version = release(37) { minorApiLevel = 1 + 1 } }",
            "compileSdk { version = release(37) { minorApiLevel = 1; unexpected() } }",
        ):
            result = self.run_fixture(modules=(module,))
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("compile-sdk input", result.stderr)

    def test_invalid_or_conflicting_catalog_and_input_fail(self):
        for value in ("37.x", "37.1.0", "37;echo bad", "-37", "37.01", "37\n38"):
            result = self.run_fixture(override=value)
            self.assertNotEqual(result.returncode, 0, result.stdout)
        for catalog in (
            'compileSdk = "37.x"\n',
            'compileSdk = 37\n',
            'compileSdk = "37.1\n',
            'compileSdk = "37.1"\nandroid-compileSdk = "37.0"\n',
        ):
            result = self.run_fixture(catalog, ("compileSdk = 37\n",))
            self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_missing_platform_fails(self):
        result = self.run_fixture()
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
