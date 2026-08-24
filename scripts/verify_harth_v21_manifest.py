"""Verify exact-byte hashes for the frozen HARTH v2.1 artifact manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "episodes/harth-calibration/protocol/v2.1-artifact-hashes.json"


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for relative, expected in manifest["hashes"].items():
        actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(f"manifest mismatch: {relative}: {actual} != {expected}")
    print(f"verified {len(manifest['hashes'])} exact-file-byte hashes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
