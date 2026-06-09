#!/usr/bin/env python
"""Aggregate one or more benchmark run dirs into a comparison table.

Metrics match aider's own benchmark/benchmark.py summarize_results():
  - pass_rate_1   : % of cases whose FIRST try passed the AST grader
  - pass_rate_N   : % passed by the final try (tries can be >1)
  - well_formed%  : % of cases with NO malformed edits (model produced a
                    parseable edit in the requested format)
  - lazy_comments : total "# ... " elision comments emitted
  - exhausted_ctx : cases that hit the context window
  - malformed     : cases with >=1 malformed edit response

Usage:
  summarize_runs.py <run_dir> [<run_dir> ...]
Each run_dir is a tmp.benchmarks/<date>--<name> directory. The row label is
taken from the edit_format recorded in that run's results.
"""
import json
import sys
from pathlib import Path


def load_run(run_dir):
    run_dir = Path(run_dir)
    results = []
    for rf in run_dir.glob("*/.aider.results.json"):
        try:
            results.append(json.loads(rf.read_text()))
        except Exception:
            pass
    return results


def summarize(run_dir):
    results = [r for r in load_run(run_dir) if r]
    n = len(results)
    if not n:
        return None
    tries = max((len(r.get("tests_outcomes", [])) for r in results), default=1) or 1
    passed_1 = 0
    passed_n = 0
    malformed_cases = 0
    lazy = 0
    exhausted = 0
    syntax = 0
    dur = 0.0
    model = edit_format = "?"
    for r in results:
        model = r.get("model", model)
        edit_format = r.get("edit_format", edit_format)
        outcomes = r.get("tests_outcomes", [])
        if outcomes:
            if outcomes[0]:
                passed_1 += 1
            if outcomes[-1]:
                passed_n += 1
        if r.get("num_malformed_responses"):
            malformed_cases += 1
        lazy += r.get("lazy_comments", 0)
        exhausted += r.get("num_exhausted_context_windows", 0)
        syntax += r.get("syntax_errors", 0)
        dur += r.get("duration", 0.0)
    return dict(
        edit_format=edit_format,
        model=model,
        n=n,
        pass_rate_1=100 * passed_1 / n,
        pass_rate_n=100 * passed_n / n,
        well_formed=100 * (n - malformed_cases) / n,
        lazy=lazy,
        exhausted=exhausted,
        syntax=syntax,
        avg_sec=dur / n,
        tries=tries,
    )


def main(argv):
    rows = []
    for d in argv:
        s = summarize(d)
        if s:
            rows.append(s)
        else:
            print(f"(no results in {d})", file=sys.stderr)
    if not rows:
        print("No results found.", file=sys.stderr)
        return 1

    model = rows[0]["model"]
    print(f"\nModel: {model}    (tasks per format: {rows[0]['n']}, tries: {rows[0]['tries']})\n")
    hdr = f"{'edit_format':<12} {'pass@1':>7} {'pass@N':>7} {'well_formed':>12} {'lazy':>5} {'exh_ctx':>8} {'syntax':>7} {'avg_s':>7}"
    print(hdr)
    print("-" * len(hdr))
    for r in sorted(rows, key=lambda x: -x["pass_rate_1"]):
        print(
            f"{r['edit_format']:<12} "
            f"{r['pass_rate_1']:>6.1f}% {r['pass_rate_n']:>6.1f}% "
            f"{r['well_formed']:>11.1f}% {r['lazy']:>5d} {r['exhausted']:>8d} "
            f"{r['syntax']:>7d} {r['avg_sec']:>7.1f}"
        )
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
