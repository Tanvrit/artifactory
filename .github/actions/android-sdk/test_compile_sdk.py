import pathlib
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).with_name("compile-sdk.py")


class CompileSdkTest(unittest.TestCase):
    def run_fixture(self, catalog=None, modules=()):
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
            return subprocess.run(["python3", str(SCRIPT)], cwd=root, capture_output=True, text=True)

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

    def test_missing_platform_fails(self):
        result = self.run_fixture()
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
