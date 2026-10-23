"""
Raises MusicDeck's version: the one in package.json, which the frontend reports to Decky and the
backend reads (musicdeck/version.py). Every build put on a device gets a number of its own.

    python scripts/bump.py            # 0.2.0 -> 0.2.1 (a fix or a small change)
    python scripts/bump.py minor      # 0.2.1 -> 0.3.0 (new features)
    python scripts/bump.py major      # 0.3.0 -> 1.0.0

Prints the new version. Commit it as "Release <version>" and tag the commit v<version>.
"""
import json
import pathlib
import re
import sys

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "package.json"


def bump(version: str, part: str) -> str:
    major, minor, patch = (int(x) for x in version.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def main() -> None:
    part = sys.argv[1] if len(sys.argv) > 1 else "patch"
    if part not in ("major", "minor", "patch"):
        sys.exit("usage: bump.py [patch|minor|major]")
    text = PACKAGE.read_bytes().decode("utf-8")  # bytes: the file keeps its own line endings
    old = json.loads(text)["version"]
    new = bump(old, part)
    # only the version line changes, so the file keeps its layout
    PACKAGE.write_bytes(re.sub(r'("version":\s*")[^"]+(")', rf"\g<1>{new}\g<2>", text, count=1).encode("utf-8"))
    print(new)


if __name__ == "__main__":
    main()
