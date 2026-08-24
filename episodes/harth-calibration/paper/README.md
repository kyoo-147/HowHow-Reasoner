# HARTH v2.1 exploratory manuscript package

This paper is a deterministic publication projection of the custody-released run3 result. It is exploratory and post-observation; it is not a rerun, scientific acceptance, or release of `quarantine.json`.

## Reproducibility modes

Public snapshot-only checks require no private files:

```text
python tools/generate_tables.py --check
python tools/generate_tables.py --render
python tools/check_paper.py
```

Explicit source-build mode requires both caller-supplied inputs and fails closed unless their exact SHA-256 identities, schema, state, custody flags, and every retained RFC6901 pointer/value match:

```text
python tools/generate_tables.py --result /absolute/result-v2.1.json --custody /absolute/findings.json
```

There are no private path defaults. Source builds atomically write only the snapshot and generated TeX; public checks/render consume only the committed snapshot and generated outputs. The exact result SHA is `2d091df35ccafb8a912fa42cfc4e9bd993f6087923eca9119f1e8369c8d5dffd`; custody findings SHA is `0b043086a6fb074ae5c3b3508bd27834777d93a26fade5f3a155f9d79b592553`.

## Evidence contract

The snapshot contains sanitized derived values, source pointers, and immutable identities. No raw/private artifacts, run output directories, or project-license metadata belong in an arXiv source archive. Exact bootstrap/test regeneration remains limited because subject-level sufficient statistics and sign counts were not retained. The lifecycle metadata defect and unchanged quarantine flag are disclosed in the paper.
