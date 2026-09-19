#!/usr/bin/env python3
"""Add (or check) the sha256 hashes of every file PyPI publishes for each pinned requirement.

    python3 scripts/hash_lock.py backend/requirements.lock           # rewrite with hashes (network)
    python3 scripts/hash_lock.py --check backend/requirements.lock   # every pin hashed? (offline)

`pip install --require-hashes -r <lock>` then refuses any file whose content differs from what was
published when the lock was made: a compromised mirror, a replaced release file or a man in the middle
cannot slip another package in. The hashes of every published file (all wheels and the sdist) are kept,
so the lock installs on any platform, as `pip-compile --generate-hashes` would write it. Comments and
`# via` lines are kept; only the `--hash` continuation lines are rewritten.
"""

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?==([^\s\\;]+)")
INDEX = "https://pypi.org/pypi/{name}/{version}/json"


def pins(lines: list[str]) -> list[tuple[str, str]]:
    return [(match[1], match[3]) for line in lines if (match := PIN.match(line))]


def hashes(name: str, version: str) -> list[str]:
    with urllib.request.urlopen(
        INDEX.format(name=name, version=version), timeout=30
    ) as response:
        release = json.load(response)
    digests = sorted(
        {
            item["digests"]["sha256"]
            for item in release["urls"]
            if not item.get("yanked")
        }
    )
    if not digests:
        raise SystemExit(f"{name}=={version}: no file published on PyPI")
    return digests


def strip(lines: list[str]) -> list[str]:
    """The lock without its hash lines (and without the continuation backslashes they need)."""
    kept = [line for line in lines if not line.strip().startswith("--hash=")]
    return [
        line.rstrip().removesuffix("\\").rstrip() if PIN.match(line) else line
        for line in kept
    ]


def hashed(lines: list[str]) -> list[str]:
    output = []
    for line in strip(lines):
        match = PIN.match(line)
        if not match:
            output.append(line)
            continue
        digests = hashes(match[1], match[3])
        output.append(f"{line} \\")
        output += [f"    --hash=sha256:{digest}" + (" \\" if index < len(digests) - 1 else "")
                   for index, digest in enumerate(digests)]  # fmt: skip
    return output


def check(lines: list[str]) -> list[str]:
    """Pins without a hash line right after them."""
    missing = []
    for index, line in enumerate(lines):
        if PIN.match(line) and not (
            index + 1 < len(lines) and lines[index + 1].strip().startswith("--hash=")
        ):
            missing.append(line.split()[0])
    return missing


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("lock", type=Path, nargs="+")
    parser.add_argument(
        "--check", action="store_true", help="only verify that every pin is hashed"
    )
    arguments = parser.parse_args()
    failed = False
    for path in arguments.lock:
        lines = path.read_text().splitlines()
        if arguments.check:
            missing = check(lines)
            if missing:
                failed = True
                print(f"{path}: no hash for {', '.join(missing)}", file=sys.stderr)
            continue
        path.write_text("\n".join(hashed(lines)) + "\n")
        print(f"{path}: {len(pins(lines))} pins hashed")
    if failed:
        raise SystemExit(
            "Run scripts/hash_lock.py on the lock file (network needed) and commit it."
        )


if __name__ == "__main__":
    main()
