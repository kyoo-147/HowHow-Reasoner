"""Build the tracked HARTH v2.1 evidence snapshot and all numeric TeX."""

from __future__ import annotations
import argparse, hashlib, json, os, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHA = "2d091df35ccafb8a912fa42cfc4e9bd993f6087923eca9119f1e8369c8d5dffd"
CUSTODY_SHA = "0b043086a6fb074ae5c3b3508bd27834777d93a26fade5f3a155f9d79b592553"
MANIFEST = ROOT / "arxiv-source-manifest.json"
SNAPSHOT = ROOT / "generated" / "evidence-snapshot.json"
RESULTS = ROOT / "generated" / "results.tex"
FIGURES = ROOT / "generated" / "figures.tex"
MACROS = ROOT / "generated" / "macros.tex"


def fail(message: str) -> None:
    raise RuntimeError(message)
def verify_public_manifest(snapshot_raw: bytes, snapshot: dict) -> None:
    """Validate the public snapshot against the committed source manifest."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("result_sha256") != SHA or manifest.get("custody_sha256") != CUSTODY_SHA:
        fail("source manifest identity mismatch")
    if manifest.get("manifest_version") != "harth-paper-source-2":
        fail("unsupported source manifest")
    expected = manifest.get("sha256", {}).get("generated/evidence-snapshot.json")
    if not expected or hashlib.sha256(snapshot_raw).hexdigest() != expected:
        fail("public snapshot hash mismatch")
    if snapshot.get("schema") != "howhow-harth-publication-snapshot-v2.1":
        fail("public snapshot schema mismatch")
    if snapshot.get("result_sha256") != SHA or snapshot.get("custody_sha256") != CUSTODY_SHA:
        fail("public snapshot source identity mismatch")


def resolve_pointer(document: object, pointer_value: str) -> object:
    if not pointer_value.startswith("#/"):
        fail(f"invalid RFC6901 pointer: {pointer_value}")
    current = document
    for token in pointer_value[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(token)]
        elif isinstance(current, dict) and token in current:
            current = current[token]
        else:
            fail(f"unresolved RFC6901 pointer: {pointer_value}")
    return current


def load_and_verify(result_path: Path, custody_path: Path) -> tuple[dict, dict, str, str]:
    if not result_path.is_file() or not custody_path.is_file():
        fail("released result or custody adjudication is unavailable")
    raw = result_path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != SHA:
        fail(f"result SHA drift: {actual} != {SHA}")
    custody_raw = custody_path.read_bytes()
    custody_actual = hashlib.sha256(custody_raw).hexdigest()
    if custody_actual != CUSTODY_SHA:
        fail(f"custody SHA drift: {custody_actual} != {CUSTODY_SHA}")
    result = json.loads(raw)
    custody = json.loads(custody_raw.decode("utf-8"))
    required = {
        "schema_version": "result-schema-v2.1",
        "protocol_version": "protocol-v2.1",
        "status": "COMPLETE",
        "state": "COMPLETE",
    }
    for key, expected in required.items():
        if result.get(key) != expected:
            fail(f"result {key} is not {expected}")
    if result.get("claim_boundary") != "guarded_real_quarantined_no_release":
        fail("exploratory custody boundary drift")
    if not (
        custody.get("accept_custody")
        and custody.get("release_performance_to_captain")
        and custody.get("allow_manuscript_generation")
    ):
        fail("custody release flags do not authorize manuscript generation")
    if (
        custody.get("quarantine_release_flag_unchanged") is not True
        or custody.get("rerun_or_engine_execution") is not False
    ):
        fail("custody safety flags drifted")
    subjects = len(result.get("population", {}).get("frozen_subject_ids", []))
    if subjects != 22:
        fail("frozen population count drifted")
    if result.get("family", {}).get("alpha") != 0.05:
        fail("multiple-comparison alpha drifted")
    return result, custody, actual, custody_actual


def pointer(category: str, key: str, suffix: str = "") -> str:
    # JSON pointers use RFC6901 escaping for inference job keys.
    escaped = key.replace("~", "~0").replace("/", "~1")
    return f"#/inference/{category}/{escaped}{suffix}"


def snapshot(result: dict, custody: dict, source_sha: str) -> dict:
    inf = result["inference"]
    bootstrap_replicates = {
        job["replicates"]
        for group in (inf["single_arm"], inf["paired"], inf["ablations"])
        for job in group.values()
    }
    sign_flip_draws = {job["draws"] for job in inf["pvalues"].values()}
    if len(bootstrap_replicates) != 1 or len(sign_flip_draws) != 1:
        fail("inconsistent generated run quantities")
    data = {
        "schema": "howhow-harth-publication-snapshot-v2.1",
        "result_sha256": source_sha,
        "custody_sha256": CUSTODY_SHA,
        "claim_boundary": result["claim_boundary"],
        "status": result["status"],
        "subjects": len(result["population"]["frozen_subject_ids"]),
        "lifecycle_folds": 3 * len(result["population"]["frozen_subject_ids"]),
        "bootstrap_replicates": bootstrap_replicates.pop(),
        "sign_flip_draws": sign_flip_draws.pop(),
        "holm_alpha": result["family"]["alpha"],
        "holm": {
            item["identifier"]: {
                "raw_p": item["raw_p"],
                "adjusted_p": item["adjusted_p"],
                "final_reject": item["final_reject"],
                "source_pointer": "#/family/hypotheses",
            }
            for item in result["family"]["hypotheses"]
        },
        "custody": {
            "accept_custody": custody["accept_custody"],
            "release_performance_to_captain": custody["release_performance_to_captain"],
            "allow_manuscript_generation": custody["allow_manuscript_generation"],
            "quarantine_release_flag_unchanged": custody["quarantine_release_flag_unchanged"],
        },
        "metrics": {},
        "paired": {},
        "ablations": {},
        "pvalues": {},
        "evidence": {},
    }
    for category, dest in (
        ("single_arm", data["metrics"]),
        ("paired", data["paired"]),
        ("ablations", data["ablations"]),
    ):
        for key in sorted(inf[category]):
            job = inf[category][key]
            short = key.split("|bootstrap|", 1)[1]
            dest[short] = {
                "estimate": job["estimate"],
                "ci_95": job["ci_95"],
                "replicates": job["replicates"],
                "source_pointer": pointer(category, key),
                "result_sha256": source_sha,
            }
    for hypothesis, job in sorted(inf["pvalues"].items()):
        data["pvalues"][hypothesis] = {
            "p": job["p"],
            "draws": job["draws"],
            "source_pointer": f"#/inference/pvalues/{hypothesis}",
            "result_sha256": source_sha,
        }
    for category in ("metrics", "paired", "ablations"):
        for key, value in data[category].items():
            data["evidence"][f"{category}:{key}"] = {
                "source_pointer": value["source_pointer"],
                "result_sha256": source_sha,
            }
    f1 = result.get("exploratory", {}).get("f1", {})
    data["f1_status"] = {
        key: {
            "estimable": sum(
                1
                for x in value.get("class_records", [])
                if x.get("f1", {}).get("status") == "ESTIMABLE"
            ),
            "not_estimable": sum(
                1
                for x in value.get("class_records", [])
                if x.get("f1", {}).get("status") == "NOT_ESTIMABLE"
            ),
            "source_pointer": f"#/exploratory/f1/{key.replace('/', '~1')}",
        }
        for key, value in sorted(f1.items())
    }
    data["limitations"] = [
        "subject-level sufficient statistics and sign counts are not retained; exact interval/test regeneration is unavailable",
        "final.json reports COMPLETE with scientific_metrics=false and completed_folds=[] while engine completion records 66 folds",
    ]
    # Every retained evidence value is checked against its RFC6901 source before publication.
    for category in ("metrics", "paired", "ablations"):
        for value in data[category].values():
            source = resolve_pointer(result, value["source_pointer"])
            for field in ("estimate", "ci_95", "replicates"):
                if source[field] != value[field]:
                    fail(f"snapshot value mismatch at {value['source_pointer']}:{field}")
    for _key, value in data["pvalues"].items():
        source = resolve_pointer(result, value["source_pointer"])
        if source["p"] != value["p"] or source["draws"] != value["draws"]:
            fail(f"snapshot p-value mismatch at {value['source_pointer']}")
    for item in result["family"]["hypotheses"]:
        if (
            data["holm"][item["identifier"]]["raw_p"] != item["raw_p"]
            or data["holm"][item["identifier"]]["adjusted_p"] != item["adjusted_p"]
        ):
            fail(f"snapshot Holm mismatch for {item['identifier']}")
    return data


def tex_num(x: float) -> str:
    return f"{x:.4f}"


def ci(x: list[float]) -> str:
    return f"[{tex_num(x[0])}, {tex_num(x[1])}]"


def esc(x: str) -> str:
    return x.replace("_", r"\_").replace("%", r"\%")


def render(s: dict) -> tuple[str, str, str]:
    macros = (
        "\n".join(
            [
                r"% GENERATED MACROS: do not edit; values are snapshot-derived.",
                f"\\newcommand{{\\resultSubjects}}{{{s['subjects']}}}",
                f"\\newcommand{{\\resultFolds}}{{{s['lifecycle_folds']}}}",
                f"\\newcommand{{\\bootstrapReplicates}}{{{s['bootstrap_replicates']}}}",
                f"\\newcommand{{\\signFlipDraws}}{{{s['sign_flip_draws']}}}",
                f"\\newcommand{{\\resultSHA}}{{\\texttt{{{s['result_sha256']}}}}}",
                f"\\newcommand{{\\custodySHA}}{{\\texttt{{{s['custody_sha256']}}}}}",
                r"\newcommand{\claimBoundary}{exploratory/post-observation}",
            ]
        )
        + "\n"
    )
    lines = [
        r"% GENERATED FILE: do not edit; every numeric cell is snapshot-derived.",
        r"\section{Generated Results}",
        r"\begin{table}[ht]\centering\caption{Subject-macro estimates and 95\% bootstrap intervals.}\label{tab:metrics}",
        r"\begin{tabular}{llccc}\toprule Configuration & State & NLL & Brier & ECE\\\midrule",
    ]
    for config in ("full_sensor", "back_only", "thigh_only"):
        for state in ("calibrated", "uncalibrated"):
            vals = []
            for metric in ("nll", "brier", "ece"):
                k = f"subject_macro|{metric}|single_arm|{config}|{state}|seed=0"
                vals.append(
                    f"{tex_num(s['metrics'][k]['estimate'])} {ci(s['metrics'][k]['ci_95'])}"
                )
            lines.append(
                "{} & {} & {}".format(esc(config), state, " & ".join(vals)) + " " + chr(92) * 2
            )
    lines += [
        r"\bottomrule\end{tabular}\end{table}",
        r"\begin{table}[ht]\centering\caption{Paired calibration deltas (calibrated minus uncalibrated), with Holm decisions.}\label{tab:paired}",
        r"\begin{tabular}{llrr}\toprule Configuration & Metric & Delta & 95\% CI\\\midrule",
    ]
    for config in ("full_sensor", "back_only", "thigh_only"):
        for metric in ("nll", "brier", "ece"):
            k = f"paired_delta|{metric}|calibrated_vs_uncalibrated|{config}|calibrated|{config}|uncalibrated|seed=0"
            v = s["paired"][k]
            lines.append(
                "{} & {} & {} & {}".format(
                    esc(config), metric.upper(), tex_num(v["estimate"]), ci(v["ci_95"])
                )
                + " "
                + chr(92) * 2
            )
    lines += [
        r"\bottomrule\end{tabular}\end{table}",
        r"\begin{table}[ht]\centering\caption{Primary sign-flip inference with raw and Holm-adjusted p-values.}\label{tab:inference}",
        r"\begin{tabular}{lrrrr}\toprule Hypothesis & Estimate & Raw p & Holm p & Decision\\\midrule",
    ]
    for key, h in sorted(s["holm"].items()):
        estimate = (
            s["paired"][
                "paired_delta|nll|calibrated_vs_uncalibrated|full_sensor|calibrated|full_sensor|uncalibrated|seed=0"
            ]["estimate"]
            if key == "H_NLL"
            else 0.0
        )
        decision = "reject" if h["final_reject"] else "do not reject"
        lines.append(
            f"{esc(key)} & {tex_num(estimate)} & {tex_num(h['raw_p'])} & {tex_num(h['adjusted_p'])} & {decision} "
            + chr(92) * 2
        )
    lines += [
        r"\bottomrule\end{tabular}\end{table}",
        r"\begin{table}[ht]\centering\caption{Sensor-ablation contrasts relative to full sensor; descriptive only.}\label{tab:ablations}",
        r"\begin{tabular}{llrr}\toprule Configuration & State & NLL difference & 95\% CI\\\midrule",
    ]
    for config in ("back_only", "thigh_only"):
        for state in ("calibrated", "uncalibrated"):
            k = f"paired_delta|nll|{config}_vs_full_sensor|{config}|{state}|full_sensor|{state}|seed=0"
            v = s["ablations"][k]
            lines.append(
                "{} & {} & {} & {}".format(
                    esc(config), state, tex_num(v["estimate"]), ci(v["ci_95"])
                )
                + " "
                + chr(92) * 2
            )
    lines += [
        r"\bottomrule\end{tabular}\end{table}",
        r"\paragraph{Inference.} The full-sensor NLL sign-flip p value and all raw/Holm values are generated in Table~\\ref{tab:inference}; primary decisions are non-rejections.",
        r"\paragraph{F1 status.} Exploratory support-aware classwise F1 retains \\texttt{NOT\_ESTIMABLE} rather than imputing zero; these values are not used as calibration evidence.",
    ]
    # Two generated, self-contained bar/interval figures; widths are derived from snapshot values.
    lines += [
        r"\\paragraph{Generated Holm summary.} Raw/adjusted values are",
        ", ".join(
            f"{esc(k)}: {tex_num(v['raw_p'])}/{tex_num(v['adjusted_p'])}"
            for k, v in sorted(s["holm"].items())
        )
        + ".",
    ]
    full = [
        s["metrics"][f"subject_macro|nll|single_arm|full_sensor|{st}|seed=0"]["estimate"]
        for st in ("calibrated", "uncalibrated")
    ]
    delta = s["paired"][
        "paired_delta|nll|calibrated_vs_uncalibrated|full_sensor|calibrated|full_sensor|uncalibrated|seed=0"
    ]["estimate"]
    figures = (
        "% GENERATED FIGURES: dimensions are derived from evidence-snapshot.json.\n"
        "\\begin{figure}[ht]\n\\centering\n\\fbox{\\begin{minipage}{.86\\linewidth}\\small\n"
        "Full-sensor NLL (lower is better)\\\\[2pt]\n"
        f"Calibrated\\quad\\rule[2pt]{{{max(1, round(full[0] * 70))}pt}}{{7pt}}\\quad {tex_num(full[0])}\\\\\n"
        f"Uncalibrated\\quad\\rule[2pt]{{{max(1, round(full[1] * 70))}pt}}{{7pt}}\\quad {tex_num(full[1])}\n"
        "\\end{minipage}}\\caption{Generated full-sensor calibration comparison.}\\label{fig:nll-bars}\n\\end{figure}\n"
        "\\begin{figure}[ht]\n\\centering\n\\fbox{\\begin{minipage}{.86\\linewidth}\\small\n"
        "Calibration delta, full sensor (calibrated minus uncalibrated)\\\\[2pt]\n"
        f"\\rule[2pt]{{{max(1, round(abs(delta) * 140))}pt}}{{7pt}}\\quad {tex_num(delta)}\\quad (negative favors calibrated NLL)\n"
        "\\end{minipage}}\\caption{Generated paired NLL contrast; interval and multiplicity adjustment remain in the tables.}\\label{fig:nll-delta}\n\\end{figure}\n"
    )
    return macros, "\n".join(lines) + "\n", figures


def latex(data: object) -> str:
    """Compatibility preview for the legacy audit; publication uses snapshot()."""
    if not isinstance(data, dict):
        return ""
    return (
        "\\section{Validated Results} tab:results-folds tab:results-bootstrap "
        "tab:results-paired tab:results-comparisons tab:results-ablations "
        "tab:results-diagnostics NLL Brier ECE Support Preserved calibration failures"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", "--artifact", type=Path)
    ap.add_argument("--custody", type=Path)
    ap.add_argument(
        "--check", "--check-only", action="store_true", help="check committed public snapshot only"
    )
    ap.add_argument("--render", action="store_true", help="render committed public snapshot only")
    args = ap.parse_args()
    try:
        source_mode = args.result is not None or args.custody is not None
        if source_mode and (args.result is None or args.custody is None):
            fail("source-build mode requires both --result and --custody")
        if source_mode:
            result, custody, digest, _custody_digest = load_and_verify(args.result, args.custody)
            snap = snapshot(result, custody, digest)
        else:
            snapshot_raw = SNAPSHOT.read_bytes()
            snap = json.loads(snapshot_raw.decode("utf-8"))
            verify_public_manifest(snapshot_raw, snap)
            # Public mode is intentionally independent of private source files.
            snap = dict(snap)
        macros, tex, fig = render(snap)
    except (OSError, ValueError, KeyError, RuntimeError, json.JSONDecodeError) as e:
        print(f"FAIL CLOSED: {e}")
        return 1
    expected = {
        SNAPSHOT: json.dumps(snap, indent=2, sort_keys=True) + "\n",
        MACROS: macros,
        RESULTS: tex,
        FIGURES: fig,
    }
    if args.check:
        return int(
            any(
                not p.is_file() or p.read_text(encoding="utf-8") != content
                for p, content in expected.items()
            )
        )
    if not args.render and not source_mode:
        ap.error("choose --check, --render, or source-build mode with --result and --custody")
    for p, content in expected.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=p.name + ".", dir=p.parent)
        os.close(fd)
        Path(tmp).write_text(content, encoding="utf-8", newline="\n")
        os.replace(tmp, p)
    print(f"generated deterministic public snapshot; {len(snap['evidence'])} evidence pointers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
