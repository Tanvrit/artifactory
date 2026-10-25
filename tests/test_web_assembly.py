"""Exercise the real reusable workflow's bundle-hashing shell block offline."""

import hashlib
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/release-web-template.yml"


class WebAssemblyTest(unittest.TestCase):
    def run_hash_block(self, work):
        workflow = WORKFLOW.read_text()
        start = workflow.index("          find dist -name composeApp.js | while read -r cj; do")
        end = workflow.index("          # ── Self-consistent index.html", start)
        block = "\n".join(line[10:] for line in workflow[start:end].splitlines())
        subprocess.run(["bash", "-e", "-c", block], cwd=work, check=True, capture_output=True, text=True)

    def test_license_pointer_keeps_exact_attribution_after_hashing(self):
        for nested in ["app", "an app with spaces"]:
            with self.subTest(nested=nested), tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp)
                folder = work / "dist" / nested
                folder.mkdir(parents=True)
                script = "/*! For license information please see composeApp.js.LICENSE.txt */\nconsole.log('composeApp.js');\n"
                license_bytes = b"Copyright fixture authors\nSPDX-License-Identifier: Apache-2.0\n"
                (folder / "composeApp.js").write_text(script)
                (folder / "composeApp.js.LICENSE.txt").write_bytes(license_bytes)
                self.run_hash_block(work)
                name = "composeApp." + hashlib.sha256(script.encode()).hexdigest()[:10] + ".js"
                renamed = folder / name
                self.assertTrue(renamed.is_file())
                pointer = re.search(r"For license information please see ([^\s*]+)", renamed.read_text()).group(1)
                self.assertEqual(pointer, name + ".LICENSE.txt")
                self.assertEqual((folder / pointer).read_bytes(), license_bytes)
                self.assertEqual((folder / "composeApp.js.LICENSE.txt").read_bytes(), license_bytes)

    def test_bundle_without_a_license_file_still_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            folder = work / "dist/app"
            folder.mkdir(parents=True)
            script = "console.log('no third party license bundle');\n"
            (folder / "composeApp.js").write_text(script)
            self.run_hash_block(work)
            self.assertEqual(len(list(folder.glob("composeApp.*.js"))), 1)
            self.assertEqual(list(folder.glob("*.LICENSE.txt")), [])

    def test_existing_hashed_sibling_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            folder = work / "dist/app"
            folder.mkdir(parents=True)
            original = "console.log('original');\n"
            prehashed = "console.log('already hashed by the compiler');\n"
            (folder / "composeApp.js").write_text(original)
            (folder / "composeApp.0123456789.js").write_text(prehashed)
            self.run_hash_block(work)
            self.assertEqual((folder / "composeApp.js").read_text(), original)
            self.assertEqual((folder / "composeApp.0123456789.js").read_text(), prehashed)


if __name__ == "__main__":
    unittest.main()
