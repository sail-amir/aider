#!/usr/bin/env python
"""Per-format comparison tables: one table per edit_format, models as rows.

This is the transpose of summarize_runs.py (which is per-model, formats as rows).
It prints one table for each edit_format (diff / udiff / whole), with one row per
model, sorted by pass@1. Columns match the metrics in aider's benchmark.py:

  pass@1       % of cases whose FIRST try passed the AST grader (the laziness metric)
  well_formed  % of cases with NO malformed edit responses
  lazy         total "# ..." elision comments emitted
  exh          cases that hit the context window (num_exhausted_context_windows)
  err          total error events (num_error_outputs; includes recovered retries)
  avg_s        average wall-clock seconds per task

Usage:
  compare_models.py                       # all tmp.benchmarks/*--full-* runs
  compare_models.py <run_dir> [run_dir..] # specific run dirs
  compare_models.py --glob '*pangu*'      # filter the default glob

Model + format are read from each run's recorded results (falling back to the
run-dir name). If two run dirs map to the same (model, format), the one with
more results wins (so a resumed/retried run supersedes its partial predecessor).
"""
import json
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent / "tmp.benchmarks"
FORMAT_ORDER = ["diff", "udiff", "whole"]


def load_run(run_dir):
    out = []
    for rf in Path(run_dir).glob("*/.aider.results.json"):
        try:
            r = json.loads(rf.read_text())
            if r:
                out.append(r)
        except Exception:
            pass
    return out


def parse_dirname(run_dir):
    """`<date>--full-<model>-<format>` -> (model, format) as a fallback."""
    name = Path(run_dir).name
    tail = name.split("--", 1)[-1]
    if tail.startswith("full-"):
        tail = tail[len("full-"):]
    for fmt in ("udiff", "whole", "diff"):  # udiff before diff (suffix overlap)
        if tail.endswith("-" + fmt):
            return tail[: -(len(fmt) + 1)], fmt
    return tail, "?"


def summarize(run_dir):
    results = load_run(run_dir)
    n = len(results)
    if not n:
        return None
    dmodel, dfmt = parse_dirname(run_dir)
    passed_1 = malformed = lazy = exhausted = err = 0
    dur = 0.0
    model = fmt = None
    for r in results:
        model = r.get("model") or model
        fmt = r.get("edit_format") or fmt
        oc = r.get("tests_outcomes", [])
        if oc and oc[0]:
            passed_1 += 1
        if r.get("num_malformed_responses"):
            malformed += 1
        lazy += r.get("lazy_comments", 0)
        exhausted += r.get("num_exhausted_context_windows", 0)
        err += r.get("num_error_outputs", 0)
        dur += r.get("duration", 0.0)
    return dict(
        model=dmodel or model,        # dir label matches the run naming we use
        fmt=(dfmt if dfmt != "?" else fmt) or "?",
        n=n,
        pass1=100 * passed_1 / n,
        well_formed=100 * (n - malformed) / n,
        lazy=lazy,
        exh=exhausted,
        err=err,
        avg_s=dur / n,
        run=Path(run_dir).name,
    )


def main(argv):
    glob = "*--full-*"
    dirs = []
    i = 0
    while i < len(argv):
        if argv[i] == "--glob":
            glob = argv[i + 1]
            i += 2
        else:
            dirs.append(argv[i])
            i += 1
    if not dirs:
        dirs = sorted(str(d) for d in BENCH.glob(glob) if d.is_dir())

    # group by format, keeping the most-complete run per (model, format)
    by_fmt = {}
    for d in dirs:
        s = summarize(d)
        if not s:
            print(f"(no results in {d})", file=sys.stderr)
            continue
        slot = by_fmt.setdefault(s["fmt"], {})
        if s["model"] not in slot or s["n"] > slot[s["model"]]["n"]:
            slot[s["model"]] = s

    if not by_fmt:
        print("No results found.", file=sys.stderr)
        return 1

    fmts = [f for f in FORMAT_ORDER if f in by_fmt] + [
        f for f in by_fmt if f not in FORMAT_ORDER
    ]
    for fmt in fmts:
        rows = sorted(by_fmt[fmt].values(), key=lambda x: -x["pass1"])
        print(f"\n=== {fmt.upper()} ===")
        hdr = (
            f"{'model':<28} {'n':>3} {'pass@1':>7} {'well_frm':>9} "
            f"{'lazy':>5} {'exh':>4} {'err':>4} {'avg_s':>6}"
        )
        print(hdr)
        print("-" * len(hdr))
        for r in rows:
            print(
                f"{r['model']:<28} {r['n']:>3} {r['pass1']:>6.0f}% "
                f"{r['well_formed']:>8.0f}% {r['lazy']:>5d} {r['exh']:>4d} "
                f"{r['err']:>4d} {r['avg_s']:>6.0f}"
            )
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
