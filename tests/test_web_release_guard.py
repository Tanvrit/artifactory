"""Exercise the reusable workflow's real final source guard against moved main."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/release-web-template.yml"


class WebReleaseGuardTest(unittest.TestCase):
    def git(self, root, *args):
        return subprocess.check_output(["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()

    def test_current_release_passes_and_moved_main_is_superseded(self):
        text = WORKFLOW.read_text()
        step = text.split("      - name: Verify current main immediately before production upload\n", 1)[1]
        step = step.split("      - name: Deploy to Cloudflare Pages\n", 1)[0]
        block = step.split("        run: |\n", 1)[1]
        script = "\n".join(line[10:] for line in block.splitlines())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            origin = root / "origin.git"
            repo = root / "repo"
            repo.mkdir()
            self.git(root, "init", "--bare", str(origin))
            self.git(repo, "init", "-b", "main")
            self.git(repo, "config", "user.name", "Release Fixture")
            self.git(repo, "config", "user.email", "fixture@example.invalid")
            self.git(repo, "config", "commit.gpgsign", "false")
            (repo / "source").write_text("reviewed source")
            self.git(repo, "add", "source")
            self.git(repo, "commit", "-m", "reviewed source")
            reviewed = self.git(repo, "rev-parse", "HEAD")
            self.git(repo, "remote", "add", "origin", str(origin))
            self.git(repo, "push", "origin", "main")
            out = root / "github_output"

            def run(sha, cwd=repo):
                out.write_text("")
                env = dict(os.environ, GITHUB_SHA=sha, GITHUB_OUTPUT=str(out))
                result = subprocess.run(["bash", "-c", script], cwd=cwd, env=env, text=True, capture_output=True)
                return result, out.read_text()

            result, outputs = run(reviewed)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("superseded=false", outputs)

            (repo / "source").write_text("later source")
            self.git(repo, "commit", "-am", "later source")
            self.git(repo, "push", "origin", "main")
            self.git(repo, "checkout", "--detach", reviewed)
            # Moved main is supersession: green, but flagged so the upload is skipped.
            result, outputs = run(reviewed)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("superseded=true", outputs)
            self.assertIn("Skipping the stale production upload", result.stdout)

            result, _ = run(self.git(repo, "rev-parse", "origin/main"))
            self.assertNotEqual(result.returncode, 0, "A different checked-out SHA must also refuse upload")

            # Being unable to read main is unverifiable, never "superseded".
            self.git(repo, "remote", "set-url", "origin", str(root / "missing.git"))
            result, outputs = run(reviewed)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("superseded=true", outputs)

    def test_superseded_run_never_uploads(self):
        text = WORKFLOW.read_text()
        deploy = text.split("      - name: Deploy to Cloudflare Pages\n", 1)[1].split("\n      - name:", 1)[0]
        self.assertIn("steps.freshness.outputs.superseded != 'true'", deploy)
        self.assertIn("superseded: ${{ steps.freshness.outputs.superseded || 'false' }}", text)
        self.assertIn("value: ${{ jobs.release-web.outputs.superseded }}", text)

if __name__ == "__main__":
    unittest.main()
