#!/usr/bin/env bash
# scripts/check-self-hosted-runners.sh
#
# Fails if any GitHub Actions workflow in this repo can dispatch a job to a
# GitHub-hosted runner. Every job, on every target, must land on the
# self-hosted `tanvrit` fleet.
#
# ---------------------------------------------------------------------------
# Why this is a gate and not a convention
# ---------------------------------------------------------------------------
# On 2026-09-11 the owner found two "Standard GitHub-hosted runners" billing
# against the org while every workflow header in this repo described itself as
# self-hosted. Both were real:
#
#   * release-macos.yml pinned its x64 leg to `macos-15-intel` outright, and
#     offered `-f runner=github-hosted` on the arm64 leg.
#   * release-linux.yml's arm64 leg pinned `ubuntu-24.04-arm`.
#
# Neither was a mistake at the time it was written — each was chosen because
# the self-hosted alternative was missing a toolchain or offline, and each was
# documented honestly in a comment. That is exactly why prose could not hold
# the line: the reasons were good, local, and invisible from anywhere else.
#
# ---------------------------------------------------------------------------
# What it checks, and what it deliberately does not
# ---------------------------------------------------------------------------
# Two things are STRIPPED before scanning, and both had to be, because both
# produce hosted-runner names that are not hosted-runner selections:
#
#  1. YAML comments. This repo documents its own history in place —
#     `release-linux.yml` still says "this header claimed `ubuntu-latest`
#     until 2026-09-11", and that sentence must stay legal. A recorded
#     correction is not a hosted runner; only a live value is.
#
#  2. Prose values — `run:` script bodies, `name:` and `description:`. None
#     of these can select a runner, and all three legitimately name one.
#     Both were found by running this against all 60 repos in the workspace:
#     `apps/app-madison-geeks/.github/workflows/build.yml` is already fully
#     self-hosted, and its a11y step opens with the shell comment "the
#     self-hosted `tanvrit` runners have no Chrome; the GitHub-hosted images
#     did"; and this gate's own job is called "No GitHub-hosted runners",
#     which tripped it on the first install. A gate that fails a compliant
#     repo for explaining itself trains people to work around the gate.
#
# The scan covers whatever is left: `runs-on:` values, matrix `runner:` JSON,
# `${{ }}` ternaries that could resolve to an image name, and
# `workflow_dispatch` inputs offering a `github-hosted` choice. Catching the
# dispatch input matters as much as the default — the two findings above both
# reached production through an input, not through `runs-on`.
#
# Adding a new hosted image name to HOSTED_PATTERNS is the maintenance cost.
# The list covers GitHub's published label families (`ubuntu-*`, `macos-*`,
# `windows-*`) by prefix rather than by exact image, so a new image in an
# existing family is caught without an edit here.
#
# Usage:  ./scripts/check-self-hosted-runners.sh [path-to-workflows-dir]
# Exit:   0 when every job is self-hosted, 1 on any hosted reference.

set -euo pipefail

DIR="${1:-.github/workflows}"

if [ ! -d "$DIR" ]; then
  echo "No $DIR in this repo; nothing to check."
  exit 0
fi

python3 - "$DIR" <<'PY'
import os, re, sys

WF_DIR = sys.argv[1]

# GitHub's hosted-runner label families. Prefix-matched so a future image in an
# existing family (say `ubuntu-26.04`) is caught without editing this list.
HOSTED_PATTERNS = [
    r"\bubuntu-(?:latest|\d[\w.-]*)\b",
    r"\bmacos-(?:latest|\d[\w.-]*)\b",
    r"\bwindows-(?:latest|\d[\w.-]*)\b",
    r"\bgithub-hosted\b",
]
HOSTED_RE = re.compile("|".join(HOSTED_PATTERNS), re.IGNORECASE)


BLOCK_SCALAR_RE = re.compile(r":\s*[|>][-+0-9]*\s*$")

# Keys whose values are prose, not runner selection. A job called
# "Migrate off ubuntu-latest" is a description of work, not a hosted runner.
PROSE_KEY_RE = re.compile(r"^\s*-?\s*(?:run|name|description)\s*:\s*(.*)$")


def strip_comments(line: str) -> str:
    """Remove a YAML trailing/whole-line comment, respecting quotes.

    Not a YAML parser: it only needs to know when a `#` is inside a scalar.
    A `#` starts a comment when it is at the start of the line or preceded by
    whitespace, and is not inside a single- or double-quoted run.
    """
    out, quote = [], None
    for i, ch in enumerate(line):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            continue
        if ch == "#" and (i == 0 or line[i - 1].isspace()):
            break
        out.append(ch)
    return "".join(out)


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def scan(path):
    """Yield (lineno, matched, text) for hosted names outside run: bodies."""
    hits = []
    # When inside a `run:` script, this holds the indent the body must exceed;
    # None means we are in ordinary YAML.
    skip_above = None
    # A `run: "…"` whose closing quote is on a later line (YAML folds a long
    # double-quoted scalar across lines); this holds that quote character.
    open_quote = None

    with open(path, encoding="utf-8") as fh:
        for n, raw in enumerate(fh, 1):
            line = raw.rstrip("\n")

            if open_quote is not None:
                # Inside a folded quoted scalar. A backslash at end of line is
                # YAML's line continuation, so the quote count is what matters.
                if line.count(open_quote) % 2 == 1:
                    open_quote = None
                continue

            if skip_above is not None:
                if line.strip() and indent_of(line) <= skip_above:
                    skip_above = None      # dedented out of the script body
                else:
                    continue

            code = strip_comments(line)
            stripped = code.strip()
            if not stripped:
                continue

            # A prose value (a shell script, a job name, an input's help
            # text) cannot select a runner, and all three legitimately name
            # one. Skip the value; for `run:` also skip its whole body.
            key = PROSE_KEY_RE.match(code)
            if key:
                rest = key.group(1).strip()
                if BLOCK_SCALAR_RE.search(code):
                    skip_above = indent_of(line)
                    continue
                if rest[:1] in ("\"", "'") and rest.count(rest[0]) % 2 == 1:
                    open_quote = rest[0]
                continue

            m = HOSTED_RE.search(code)
            if m:
                hits.append((n, m.group(0), stripped))
    return hits


findings = []
scanned = 0

for name in sorted(os.listdir(WF_DIR)):
    if not name.endswith((".yml", ".yaml")):
        continue
    path = os.path.join(WF_DIR, name)
    scanned += 1
    for n, hit, text in scan(path):
        findings.append((path, n, hit, text))

if findings:
    print(f"FAIL: {len(findings)} GitHub-hosted runner reference(s) in {scanned} workflow(s).\n")
    for path, n, hit, code in findings:
        print(f"  {path}:{n}")
        print(f"      matched: {hit}")
        print(f"      line:    {code}")
    print(
        "\nEvery job must run on the self-hosted `tanvrit` fleet. Replace the value with an\n"
        "explicit label set naming the OS and architecture, e.g.\n"
        "\n"
        "    runs-on:\n"
        "      group: tanvrit\n"
        "      labels: [self-hosted, Linux, X64]\n"
        "\n"
        "or, for a matrix, a JSON string consumed with fromJSON():\n"
        "\n"
        "    runner: '{\"group\": \"tanvrit\", \"labels\": [\"self-hosted\", \"Linux\", \"X64\"]}'\n"
        "\n"
        "`group: tanvrit` ALONE is not enough — the group is heterogeneous (Linux X64,\n"
        "Linux ARM64, macOS, Windows) and an unpinned job has landed on the wrong OS\n"
        "before (release-linux.yml v1.3.0, commit 41da95c).\n"
        "\n"
        "If you are recording history rather than selecting a runner, put it in a comment:\n"
        "comments are stripped before this scan precisely so corrections stay writable."
    )
    sys.exit(1)

print(f"OK: {scanned} workflow(s) scanned, no GitHub-hosted runner references.")
PY
