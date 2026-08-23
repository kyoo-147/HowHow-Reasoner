"""Opt-in real-data smoke entry point; never runs during default tests."""

from __future__ import annotations

import os


def main() -> None:
    if os.environ.get("HOWHOW_RUN_REAL_HARTH") != "1":
        raise SystemExit("real HARTH smoke is opt-in: set HOWHOW_RUN_REAL_HARTH=1")
    raise SystemExit("HARTH archive download/feature extraction is bounded and operator-configured")


if __name__ == "__main__":
    main()
