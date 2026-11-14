"""Read the compile SDK without configuring Gradle or reading secrets.

The catalog is authoritative when present. Otherwise only explicit integer literals
and the supported AGP release/minor DSL are accepted; computed values need an input.
"""
from pathlib import Path
import re
import subprocess
import sys

API = r"[1-9][0-9]*(?:\.(?:0|[1-9][0-9]*))?"
ERROR = "::error::Cannot select one compile SDK; set the action's compile-sdk input explicitly"


def code_only(text):
    """Mask Kotlin strings and comments, including nested block comments."""
    token = re.compile(r'"""|"|\x27|/\*|//')
    cursor = 0
    output = []
    while match := token.search(text, cursor):
        output.append(text[cursor:match.start()])
        start, marker = match.start(), match.group()
        end = match.end()
        if marker == "//":
            newline = text.find("\n", end)
            end = len(text) if newline < 0 else newline
        elif marker == "/*":
            depth = 1
            while depth:
                nested = re.search(r"/\*|\*/", text[end:])
                if not nested:
                    raise ValueError("Unclosed Kotlin comment")
                depth += 1 if nested.group() == "/*" else -1
                end += nested.end()
        elif marker == '"""':
            close = text.find(marker, end)
            if close < 0:
                raise ValueError("Unclosed Kotlin string")
            end = close + 3
        else:
            while end < len(text):
                if text[end] == "\\":
                    end += 2
                elif text[end] == marker:
                    end += 1
                    break
                else:
                    end += 1
            else:
                raise ValueError("Unclosed Kotlin string")
        output.append(re.sub(r"[^\n]", " ", text[start:end]))
        cursor = end
    output.append(text[cursor:])
    return "".join(output)


def resolve():
    catalog = Path("gradle/libs.versions.toml")
    if catalog.is_file():
        declarations = re.findall(
            r'^\s*(?:android-compileSdk|compileSdk)\s*=\s*([^\n]*)$',
            catalog.read_text(), re.M,
        )
        if declarations:
            parsed = [re.fullmatch(r"([\"']) (" + API + r") \1 \s* (?:\#[^\n]*)?", value, re.X)
                      for value in declarations]
            if not all(parsed) or len({match[2] for match in parsed if match}) != 1:
                raise ValueError(ERROR)
            return parsed[0][2]

    paths = subprocess.check_output(["git", "ls-files", "-z", "--", "*build.gradle.kts"]).decode().split("\0")
    values = set()
    block = re.compile(
        r"\bcompileSdk\s*\{\s*version\s*=\s*release\(\s*([1-9][0-9]*)\s*\)\s*"
        r"\{\s*minorApiLevel\s*=\s*(0|[1-9][0-9]*)\s*;?\s*\}\s*\}"
    )
    literal = re.compile(r"^\s*compileSdk(?:Version)?\s*=\s*([1-9][0-9]*)\s*;?\s*$", re.M)
    for name in filter(None, paths):
        path = Path(name)
        if not path.is_file():
            continue
        code = code_only(path.read_text())
        for match in block.finditer(code):
            values.add(f"{match[1]}.{match[2]}")
        code = block.sub("", code)
        for match in literal.finditer(code):
            values.add(match[1])
        code = literal.sub("", code)
        if re.search(r"\bcompileSdk(?:Version)?\s*(?:=|\{|\()", code):
            raise ValueError(ERROR)
    if len(values) != 1:
        raise ValueError(ERROR)
    return values.pop()


try:
    override = sys.argv[1] if len(sys.argv) == 2 else ""
    if len(sys.argv) > 2 or (override and not re.fullmatch(API, override)):
        raise ValueError("::error::compile-sdk must be an API level such as 36, 37 or 37.1")
    print(override or resolve())
except ValueError as error:
    raise SystemExit(str(error))
