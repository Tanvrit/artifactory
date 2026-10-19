"""Read the repository's compile SDK without configuring Gradle or reading secrets."""
from pathlib import Path
import re
import subprocess

catalog = Path("gradle/libs.versions.toml")
if catalog.is_file():
    match = re.search(r'^\s*(?:android-compileSdk|compileSdk)\s*=\s*"([0-9]+)"', catalog.read_text(), re.M)
    if match:
        print(match.group(1))
        raise SystemExit(0)

# Core and SDK declare this literal in their module scripts instead of the catalog.
paths = subprocess.check_output(["git", "ls-files", "-z", "--", "*build.gradle.kts"]).decode().split("\0")
values = set()
for name in filter(None, paths):
    path = Path(name)
    if path.is_file():
        values.update(re.findall(r"^\s*compileSdk(?:Version)?\s*=\s*([0-9]+)\b", path.read_text(), re.M))
if len(values) != 1:
    raise SystemExit("::error::Cannot select one compile SDK; set the action's compile-sdk input explicitly")
print(values.pop())
