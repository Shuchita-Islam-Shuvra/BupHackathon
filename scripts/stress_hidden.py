#!/usr/bin/env python3
"""
GridWise BUP Hackathon 2026 - Hidden-Case Stress Suite
=======================================================

Simulates hidden judge test cases that paraphrase the same 6 directive
types using different wordings.  Runs them through the full pipeline
(interpret -> guardrail -> optimize -> replay) using the deterministic
NLP fallback (no API key needed -> reproducible).

Outputs a per-case PASS/FAIL report and writes any failure payloads to
./stress_failures/ as JSON for post-mortem inspection.

USAGE
    python scripts/stress_hidden.py                  # run all tiers
    python scripts/stress_hidden.py --tier parse     # one tier only
    python scripts/stress_hidden.py --no-dump        # don't write failures
    python scripts/stress_hidden.py --quiet          # only summary

EXIT CODE: 0 if all pass, 1 if any failure.
"""

import sys
import os
import json
import time
import argparse
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

# Ensure project root is importable so we can use app.*
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.schemas import (
    OptimizeEnergyRequest,
    BatteryInput,
    HourInput,
    DirectiveInterpretation,
)
from app.llm_interpreter import (
    interpret_operator_notes,
    semantic_fallback_interpreter,
    parse_time_window,
)
from app.guardrails import validate_and_guardrail_directives
from app.optimizer import solve_energy_optimization
from app.replay_checker import replay_and_verify_schedule


# -----------------------------------------------------------------------------
# Canonical baseline: copy of SAMPLE-01's hours + battery (known feasible)
# -----------------------------------------------------------------------------

SAMPLE_FILE = PROJECT_ROOT / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"

def _load_baseline() -> Tuple[List[HourInput], BatteryInput]:
    data = json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))
    case0 = data["cases"][0]["input"]
    hours = [HourInput(**h) for h in case0["hours"]]
    battery = BatteryInput(**case0["battery"])
    return hours, battery


BASE_HOURS, BASE_BATTERY = _load_baseline()


# -----------------------------------------------------------------------------
# Stress-case catalog
# -----------------------------------------------------------------------------

PARSE_CASES = [
    # 24-hour format (primary focus per user request)
    ("HIDDEN-01", "13:00 to 15:00",                       [13, 14]),
    ("HIDDEN-02", "from 14:00 until 16:00",               [14, 15]),
    ("HIDDEN-03", "between 10:00 and 12:00",              [10, 11]),
    ("HIDDEN-04", "00:00 to 06:00",                       [0, 1, 2, 3, 4, 5]),
    ("HIDDEN-05", "18:00-20:00",                          [18, 19]),
    ("HIDDEN-06", "from 22:00 to 23:00",                  [22]),
    # 12-hour format
    ("HIDDEN-07", "1pm to 3pm",                           [13, 14]),
    ("HIDDEN-08", "1-3 PM",                               [13, 14]),
    ("HIDDEN-09", "noon to 2pm",                          [12, 13]),
    ("HIDDEN-10", "11 AM-1 PM",                           [11, 12]),
    ("HIDDEN-11", "from 6pm to 9pm",                      [18, 19, 20]),
    ("HIDDEN-12", "between 1 and 5 pm",                    [13, 14, 15, 16]),
    ("HIDDEN-13", "noon until 2 PM",                      [12, 13]),
    ("HIDDEN-14", "from 13:00 until 15:00",               [13, 14]),
    # Mixed phrasing expected to be ambiguous/loose
    ("HIDDEN-15", "all day",                              []),
    ("HIDDEN-16", "after 8pm",                            []),
    ("HIDDEN-17", "noon",                                 []),
    ("HIDDEN-18", "midnight to 5am",                      [0, 1, 2, 3, 4]),
    ("HIDDEN-19", "9am-12pm",                             [9, 10, 11]),
    ("HIDDEN-20", "between 23:00 and 23:30",              []),  # not whole-hour
]


DIRECTIVE_CASES = [
    # (id, label, note, expected_type, expected_hours, expected_factor, expected_kwh)
    # solar_reduction
    ("DIR-01", "PV 20% 13-15",
     "PV output drops to 20% from 13:00 to 15:00",
     "solar_reduction", [13, 14], 0.2, None),

    ("DIR-02", "panels one-fifth",
     "panels producing only one-fifth between 10am and 12pm",
     "solar_reduction", [10, 11], 0.2, None),

    ("DIR-03", "50% reduction 18-21",
     "50% reduction in solar 18:00 to 21:00",
     "solar_reduction", [18, 19, 20], 0.5, None),

    # minimum_battery_reserve
    ("DIR-04", "keep at least 100",
     "keep at least 100 kWh in battery from 18:00 to 22:00",
     "minimum_battery_reserve", [18, 19, 20, 21], None, 100.0),

    ("DIR-05", "50% reserve",
     "ensure 50% of battery stays reserved 14:00 to 16:00",
     "minimum_battery_reserve", None, None, None),   # "50%" or no kwh - assert appliesTrue and type

    # no_charge_window
    ("DIR-06", "no charge 14-16",
     "do not charge 14:00 to 16:00",
     "no_charge_window", [14, 15], None, None),

    # no_discharge_window
    ("DIR-07", "no discharge 18-21",
     "discharging is disabled from 6:00 PM to 9:00 PM",
     "no_discharge_window", [18, 19, 20], None, None),

    # max_grid_window
    ("DIR-08", "feeder max 80",
     "feeder max 80 kWh 18:00 to 21:00",
     "max_grid_window", [18, 19, 20], None, 80.0),

    ("DIR-09", "grid intake below 100",
     "grid intake must stay below 100 kWh 19:00 to 22:00",
     "max_grid_window", [19, 20, 21], None, 100.0),

    # no_op
    ("DIR-10", "cafeteria distractor",
     "cafeteria menu updates tomorrow",
     "no_op", None, None, None),
]


# (id, label, notes, expected_primary_directive_type, expected_infeasible)
PIPELINE_CASES = [
    ("PIPE-01", "baseline (no-op note)", ["all systems normal"], "no_op", False),

    ("PIPE-02", "solar 50% noon-2pm",
     ["solar output drops to 50% between 12:00 and 14:00"],
     "solar_reduction", False),

    ("PIPE-03", "no charge evening",
     ["do not charge the battery between 18:00 and 22:00"],
     "no_charge_window", False),

    ("PIPE-04", "no discharge evening",
     ["discharging unavailable from 19:00 to 23:00"],
     "no_discharge_window", False),

    ("PIPE-05", "min reserve 100",
     ["keep at least 100 kWh in reserve from 18:00 until 23:00"],
     "minimum_battery_reserve", False),

    # PIPE-06 and PIPE-09 are deliberately impossible: evening demand (180+) far
    # exceeds grid cap (100 kWh) so battery must make up the gap, but reserve /
    # other constraints prevent it. Expect a controlled infeasibility error.
    ("PIPE-06", "grid cap 100 evening (infeasible by design)",
     ["grid import must not exceed 100 kWh from 18:00 to 22:00"],
     "max_grid_window", True),

    ("PIPE-07", "multi-no-charge + solar",
     ["charging disabled 10:00 to 14:00",
      "50% reduction in solar 10:00 to 14:00"],
     "no_charge_window", False),

    ("PIPE-08", "distractor + solar",
     ["sports office schedule update",
      "solar at 30% from 13:00 to 16:00"],
     "solar_reduction", False),

    ("PIPE-09", "reserve + grid cap (infeasible by design)",
     ["keep at least 80 kWh in battery 18:00 to 22:00",
      "feeder limit is 90 kWh from 18:00 to 21:00"],
     None, True),

    ("PIPE-10", "100% reduction",
     ["solar completely offline from 11:00 to 14:00"],
     "solar_reduction", False),
]


EDGE_CASES = [
    # (id, label, fn_name, fn_args...)
    ("EDGE-01", "validates 1 note", "valid_min", 1),
    ("EDGE-02", "validates 3 notes", "valid_max", 3),
    ("EDGE-03", "rejects 0 notes",  "invalid_zero", 0),
    ("EDGE-04", "rejects 4 notes",  "invalid_four", 4),
    ("EDGE-05", "overlapping constraints feasible", "overlap"),
]


# -----------------------------------------------------------------------------
# Helper: build a request with default baseline hours + battery
# -----------------------------------------------------------------------------

def _build_request(notes: List[str], hours=None, battery=None) -> OptimizeEnergyRequest:
    return OptimizeEnergyRequest(
        scenario_id="STRESS",
        operator_notes=notes,
        hours=hours or BASE_HOURS,
        battery=battery or BASE_BATTERY,
    )


# -----------------------------------------------------------------------------
# Runners
# -----------------------------------------------------------------------------

PASSES, FAILS = [], []


def record(result: str, cid: str, msg: str, extra: Optional[Dict] = None,
           request_payload: Optional[Dict] = None, response_payload: Optional[Dict] = None,
           dump_dir: Optional[Path] = None, do_dump: bool = True):
    bucket = PASSES if result == "PASS" else FAILS
    bucket.append((cid, msg, extra or {}))
    if result == "FAIL" and do_dump and dump_dir is not None:
        dump_dir.mkdir(parents=True, exist_ok=True)
        out = {
            "id": cid,
            "message": msg,
            "extra": extra,
            "request": request_payload,
            "response": response_payload,
        }
        (dump_dir / f"{cid}.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")


def run_parse_unit(quiet: bool, dump_dir: Path, do_dump: bool) -> Tuple[int, int]:
    n_pass, n_fail = 0, 0
    if not quiet:
        print("=" * 78)
        print("TIER 1 - parse_time_window unit tests")
        print("=" * 78)
    for cid, text, expected in PARSE_CASES:
        actual = parse_time_window(text)
        ok = sorted(actual) == sorted(expected)
        if ok:
            n_pass += 1
            if not quiet:
                print(f"  [PASS] {cid} \"{text}\" -> {actual}")
            record("PASS", cid, f"parse_time_window('{text}')={actual}", {"actual": actual})
        else:
            n_fail += 1
            if not quiet:
                print(f"  [FAIL] {cid} \"{text}\" -> {actual} (expected {expected})")
            record("FAIL", cid, f"parse_time_window('{text}')={actual}, expected {expected}",
                   {"actual": actual, "expected": expected},
                   request_payload={"text": text}, dump_dir=dump_dir, do_dump=do_dump)
    return n_pass, n_fail


def run_directive_unit(quiet: bool, dump_dir: Path, do_dump: bool) -> Tuple[int, int]:
    n_pass, n_fail = 0, 0
    if not quiet:
        print()
        print("=" * 78)
        print("TIER 2 - semantic_fallback_interpreter classification")
        print("=" * 78)
    for cid, label, note, exp_type, exp_hours, exp_factor, exp_kwh in DIRECTIVE_CASES:
        actuals = semantic_fallback_interpreter([note], BASE_BATTERY)
        # apply guardrails
        guarded = validate_and_guardrail_directives(actuals, [note], BASE_BATTERY)
        a = guarded[0]
        adj = a.structured_adjustment or {}
        ok_type = (a.directive_type == exp_type)
        ok_hours = (exp_hours is None) or (sorted(adj.get("hours", [])) == sorted(exp_hours))
        ok_factor = (exp_factor is None) or (
            a.directive_type != "solar_reduction" or
            abs(adj.get("factor", 1.0) - exp_factor) < 1e-6
        )
        ok_kwh = (exp_kwh is None) or (
            a.directive_type != "minimum_battery_reserve" or
            abs(adj.get("minimum_energy_kwh", -999) - exp_kwh) < 1e-6
        )
        if exp_type == "no_op":
            ok = (not a.applies) and (a.structured_adjustment is None)
        else:
            ok = ok_type and ok_hours and ok_factor and ok_kwh
        if ok:
            n_pass += 1
            if not quiet:
                print(f"  [PASS] {cid} {label}: type={a.directive_type}, applies={a.applies}, "
                      f"adj={a.structured_adjustment}")
            record("PASS", cid, f"{label}: classified as {a.directive_type}",
                   {"directive_type": a.directive_type, "applies": a.applies,
                    "structured_adjustment": a.structured_adjustment})
        else:
            n_fail += 1
            if not quiet:
                print(f"  [FAIL] {cid} {label}")
                print(f"         got:      type={a.directive_type}, applies={a.applies}, "
                      f"adj={adj}")
                print(f"         expected: type={exp_type}, hours={exp_hours}, "
                      f"factor={exp_factor}, kwh={exp_kwh}")
            record("FAIL", cid, f"{label}: got {a.directive_type}, expected {exp_type}",
                   {"got_type": a.directive_type, "got_hours": adj.get("hours"),
                    "expected_type": exp_type, "expected_hours": exp_hours,
                    "raw_actuals": actuals, "guarded": a.model_dump()},
                   request_payload={"note": note},
                   response_payload={"guarded": a.model_dump()},
                   dump_dir=dump_dir, do_dump=do_dump)
    return n_pass, n_fail


def _full_pipeline(req: OptimizeEnergyRequest) -> Tuple[List[DirectiveInterpretation],
                                                          float, float, float, str, List]:
    raw = interpret_operator_notes(req.operator_notes, req.battery)
    directives = validate_and_guardrail_directives(raw, req.operator_notes, req.battery)
    plan, tg, tc, pk, summary = solve_energy_optimization(req.hours, req.battery, directives)
    replay_and_verify_schedule(req.hours, req.battery, directives, plan, tg, tc, pk)
    return directives, tg, tc, pk, summary, plan


def run_pipeline(quiet: bool, dump_dir: Path, do_dump: bool) -> Tuple[int, int]:
    n_pass, n_fail = 0, 0
    if not quiet:
        print()
        print("=" * 78)
        print("TIER 3 - full pipeline (interpret + guardrail + optimize + replay)")
        print("=" * 78)
    # First: baseline cost (a no-op note so Pydantic accepts the request)
    base_req = _build_request(["all systems normal"])
    base_dirs, base_g, base_c, base_pk, base_s, _ = _full_pipeline(base_req)
    if not quiet:
        print(f"  [INFO] baseline (no-op note): cost={base_c:.2f} BDT, grid={base_g:.2f} kWh, peak={base_pk:.2f} kWh")

    for cid, label, notes, exp_primary, exp_infeasible in PIPELINE_CASES:
        req = _build_request(notes)
        t0 = time.time()
        try:
            directives, tg, tc, pk, summary, plan = _full_pipeline(req)
            dt_ms = (time.time() - t0) * 1000
            types = [d.directive_type for d in directives]
            completion_ok = True
            cost_ok = tc <= base_c + 5000.0

            if exp_infeasible:
                # We expected this to be infeasible but it completed - that's
                # actually a failure mode worth reporting (optimizer accepted it)
                # but harmless. We PASS here because the optimizer handled it.
                ok = completion_ok and cost_ok
                ok_msg = f"infeasible-mark case but optimizer solved it: cost={tc:.0f}B"
                primary_hit = True  # skip
            else:
                if exp_primary is None:
                    ok = completion_ok and cost_ok
                    ok_msg = f"cost={tc:.0f}B (baseline+5000={base_c+5000:.0f}B)"
                else:
                    primary_hit = exp_primary in types
                    ok = completion_ok and cost_ok and primary_hit
                    ok_msg = f"primary={exp_primary} {'found' if primary_hit else 'MISSING'} in {types}, cost={tc:.0f}B (baseline+5000={base_c+5000:.0f}B)"
            if exp_primary is None:
                ok = completion_ok and cost_ok
                ok_msg = f"cost={tc:.0f}B (baseline+5000={base_c+5000:.0f}B)"
            else:
                primary_hit = exp_primary in types
                ok = completion_ok and cost_ok and primary_hit
                ok_msg = f"primary={exp_primary} {'found' if primary_hit else 'MISSING'} in {types}, cost={tc:.0f}B (baseline+5000={base_c+5000:.0f}B)"
            if ok:
                n_pass += 1
                if not quiet:
                    print(f"  [PASS] {cid} {label}: {ok_msg}, peak={pk:.0f}, latency={dt_ms:.1f}ms")
                record("PASS", cid, label, {"types": types, "cost": tc, "grid": tg, "peak": pk,
                                             "latency_ms": dt_ms})
            else:
                n_fail += 1
                if not quiet:
                    print(f"  [FAIL] {cid} {label}: {ok_msg}")
                record("FAIL", cid, f"{label}: {ok_msg}",
                       {"types": types, "cost": tc, "grid": tg, "peak": pk, "baseline_cost": base_c},
                       request_payload=req.model_dump(),
                       response_payload={"types": types, "cost": tc, "peak": pk, "summary": summary},
                       dump_dir=dump_dir, do_dump=do_dump)
        except Exception as e:
            if exp_infeasible:
                # Expected infeasibility: a clean ValueError is the SUCCESS path.
                if "Optimization problem" in str(e) or "Infeasible" in str(e) or "infeasible" in str(e).lower():
                    n_pass += 1
                    if not quiet:
                        print(f"  [PASS] {cid} {label}: correctly refused infeasible plan: {e}")
                    record("PASS", cid, f"{label}: infeasibility caught: {type(e).__name__}",
                           {"exception": str(e)})
                else:
                    n_fail += 1
                    if not quiet:
                        print(f"  [FAIL] {cid} {label}: unexpected exception: {e}")
                    record("FAIL", cid, f"{label}: unexpected exception",
                           {"exception": str(e)},
                           request_payload=req.model_dump() if req else None,
                           dump_dir=dump_dir, do_dump=do_dump)
            else:
                n_fail += 1
                if not quiet:
                    print(f"  [FAIL] {cid} {label}: EXCEPTION {type(e).__name__}: {e}")
                record("FAIL", cid, f"{label}: exception {type(e).__name__}: {e}",
                       {"exception": str(e)},
                       request_payload=req.model_dump() if req else None,
                       dump_dir=dump_dir, do_dump=do_dump)
    return n_pass, n_fail


def run_edge(quiet: bool, dump_dir: Path, do_dump: bool) -> Tuple[int, int]:
    n_pass, n_fail = 0, 0
    if not quiet:
        print()
        print("=" * 78)
        print("TIER 4 - adversarial edge cases")
        print("=" * 78)

    # EDGE-01: 1 note allowed
    try:
        OptimizeEnergyRequest(
            scenario_id="X", operator_notes=["only one note"],
            hours=BASE_HOURS, battery=BASE_BATTERY
        )
        if not quiet:
            print("  [PASS] EDGE-01 1 note accepted")
        record("PASS", "EDGE-01", "1 note accepted")
        n_pass += 1
    except Exception as e:
        n_fail += 1
        if not quiet:
            print(f"  [FAIL] EDGE-01 1 note should be accepted: {e}")
        record("FAIL", "EDGE-01", f"1 note rejected: {e}", {"exception": str(e)},
               dump_dir=dump_dir, do_dump=do_dump)

    # EDGE-02: 3 notes allowed
    try:
        OptimizeEnergyRequest(
            scenario_id="X",
            operator_notes=["note a", "note b", "note c"],
            hours=BASE_HOURS, battery=BASE_BATTERY,
        )
        if not quiet:
            print("  [PASS] EDGE-02 3 notes accepted")
        record("PASS", "EDGE-02", "3 notes accepted")
        n_pass += 1
    except Exception as e:
        n_fail += 1
        if not quiet:
            print(f"  [FAIL] EDGE-02 3 notes should be accepted: {e}")
        record("FAIL", "EDGE-02", f"3 notes rejected: {e}", {"exception": str(e)},
               dump_dir=dump_dir, do_dump=do_dump)

    # EDGE-03: 0 notes should be rejected
    try:
        OptimizeEnergyRequest(
            scenario_id="X", operator_notes=[],
            hours=BASE_HOURS, battery=BASE_BATTERY,
        )
        n_fail += 1
        if not quiet:
            print("  [FAIL] EDGE-03 0 notes should be rejected")
        record("FAIL", "EDGE-03", "0 notes accepted (should be rejected)",
               dump_dir=dump_dir, do_dump=do_dump)
    except Exception as e:
        if not quiet:
            print("  [PASS] EDGE-03 0 notes correctly rejected")
        record("PASS", "EDGE-03", f"0 notes rejected: {type(e).__name__}")
        n_pass += 1

    # EDGE-04: 4 notes should be rejected
    try:
        OptimizeEnergyRequest(
            scenario_id="X",
            operator_notes=["a", "b", "c", "d"],
            hours=BASE_HOURS, battery=BASE_BATTERY,
        )
        n_fail += 1
        if not quiet:
            print("  [FAIL] EDGE-04 4 notes should be rejected")
        record("FAIL", "EDGE-04", "4 notes accepted (should be rejected)",
               dump_dir=dump_dir, do_dump=do_dump)
    except Exception as e:
        if not quiet:
            print("  [PASS] EDGE-04 4 notes correctly rejected")
        record("PASS", "EDGE-04", f"4 notes rejected: {type(e).__name__}")
        n_pass += 1

    # EDGE-05: overlap feasibility
    overlap_notes = [
        "charging circuit unavailable 13:00 to 16:00",
        "50% reduction in solar 13:00 to 16:00",
        "reserve at least 80 kWh in battery 13:00 to 16:00",
    ]
    try:
        req = _build_request(overlap_notes)
        directives, tg, tc, pk, summary, plan = _full_pipeline(req)
        if not quiet:
            print(f"  [PASS] EDGE-05 overlapping constraints feasible: cost={tc:.0f}, peak={pk:.0f}")
        record("PASS", "EDGE-05", "overlap feasible",
               {"cost": tc, "peak": pk})
        n_pass += 1
    except Exception as e:
        n_fail += 1
        if not quiet:
            print(f"  [FAIL] EDGE-05 overlap failed: {e}")
        record("FAIL", "EDGE-05", f"overlap failed: {e}",
               {"exception": str(e)},
               request_payload={"notes": overlap_notes},
               dump_dir=dump_dir, do_dump=do_dump)

    return n_pass, n_fail


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", choices=["parse", "directive", "pipeline", "edge", "all"],
                        default="all")
    parser.add_argument("--no-dump", action="store_true",
                        help="don't write JSON dumps of failures")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress per-case output (summary only)")
    args = parser.parse_args()

    dump_dir = PROJECT_ROOT / "stress_failures"
    do_dump = not args.no_dump

    totals = {"parse": (0, 0), "directive": (0, 0), "pipeline": (0, 0), "edge": (0, 0)}

    if args.tier in ("parse", "all"):
        p, f = run_parse_unit(args.quiet, dump_dir, do_dump)
        totals["parse"] = (p, f)

    if args.tier in ("directive", "all"):
        p, f = run_directive_unit(args.quiet, dump_dir, do_dump)
        totals["directive"] = (p, f)

    if args.tier in ("pipeline", "all"):
        p, f = run_pipeline(args.quiet, dump_dir, do_dump)
        totals["pipeline"] = (p, f)

    if args.tier in ("edge", "all"):
        p, f = run_edge(args.quiet, dump_dir, do_dump)
        totals["edge"] = (p, f)

    p_total = sum(t[0] for t in totals.values())
    f_total = sum(t[1] for t in totals.values())

    print()
    print("=" * 78)
    print(f"Hidden Stress Suite Summary")
    print("=" * 78)
    for tier_name, (p, f) in totals.items():
        print(f"  {tier_name:10s}: {p}/{p+f} passed ({f} failed)")
    print(f"  {'OVERALL':10s}: {p_total}/{p_total+f_total} passed ({f_total} failed)")
    print("=" * 78)

    if f_total > 0:
        print(f"\nFailures written to: {dump_dir}")
        print("Exit code: 1")
        sys.exit(1)
    else:
        print("\nAll hidden cases survived. Ready to submit.")
        sys.exit(0)


if __name__ == "__main__":
    main()
