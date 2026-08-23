# Commands

From the repository root:

```bash
uv run python -m pytest tests/episodes/test_harth.py
HOWHOW_RUN_REAL_HARTH=1 uv run python -m howhow.episodes.harth.smoke --data-dir episodes/harth-calibration/data
```

The real-data smoke is opt-in, CPU-first, bounded, and writes only machine-readable artifacts. It must not be treated as scientific evidence without a reviewed run manifest.
