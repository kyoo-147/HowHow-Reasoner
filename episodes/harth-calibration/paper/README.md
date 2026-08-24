# HARTH v2.1 exploratory manuscript package

This paper is a deterministic publication projection of the custody-released run3 result. It is exploratory and post-observation; it is not a rerun, a scientific acceptance, or a release of `quarantine.json`.

## Evidence contract

`tools/generate_tables.py` reads the caller-supplied strict result and custody findings from captain-controlled absolute paths. It verifies the exact released result SHA, schema, `COMPLETE` status, frozen population, exploratory boundary, and custody flags. It writes only the derived `generated/evidence-snapshot.json`, `generated/results.tex`, and `generated/figures.tex`; private destinations and raw result fields are not copied. Every derived numeric job carries a JSON pointer and source SHA.

Run:

```text
python tools/generate_tables.py
python tools/generate_tables.py --check
python tools/check_paper.py
```

`--check` is a byte-for-byte reproducibility gate and fails closed on source drift or unavailable release. Exact bootstrap/test regeneration is limited because subject-level sufficient statistics and sign counts were not retained. The lifecycle metadata defect and unchanged quarantine flag are disclosed in the paper.

## Evidence boundary

The result SHA is recorded in the generated snapshot. The external custody adjudication accepted custody and authorized manuscript generation, but did not mutate or release `quarantine.json`. No raw/private artifacts, run output directories, or project-license metadata belong in an arXiv source archive.
