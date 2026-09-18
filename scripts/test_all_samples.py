#!/usr/bin/env python3
"""
GridWise BUP Hackathon 2026 - Verification & Benchmark Script
Runs all 10 public sample cases against the API service / pipeline
and validates interpretation accuracy, physical constraints, and cost optimality.
"""
import sys
import json
import time
import requests
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.schemas import OptimizeEnergyRequest, OptimizeEnergyResponse
from app.llm_interpreter import interpret_operator_notes
from app.guardrails import validate_and_guardrail_directives
from app.optimizer import solve_energy_optimization

SAMPLE_FILE = PROJECT_ROOT / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"

def verify_against_api(base_url="http://localhost:8000"):
    print(f"=== Testing API service at {base_url} ===")
    
    # 1. Test /health
    try:
        t0 = time.time()
        r_health = requests.get(f"{base_url}/health", timeout=5)
        dt_health = (time.time() - t0) * 1000
        assert r_health.status_code == 200, f"Expected 200, got {r_health.status_code}"
        assert r_health.json() == {"status": "ok"}, f"Unexpected health response: {r_health.text}"
        print(f"  [PASS] GET /health -> 200 OK in {dt_health:.1f}ms")
    except Exception as e:
        print(f"  [FAIL] Could not reach GET /health: {e}")
        return False

    # 2. Test /optimize-energy on all 10 sample cases
    data = json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))
    cases = data["cases"]
    passed_cases = 0

    print(f"\nEvaluating {len(cases)} public sample cases via HTTP API...")
    
    for i, case in enumerate(cases):
        cid = case["id"]
        label = case["label"]
        payload = case["input"]
        exp = case["expected_output"]
        
        t0 = time.time()
        res = requests.post(f"{base_url}/optimize-energy", json=payload, timeout=30)
        dt = (time.time() - t0) * 1000
        
        if res.status_code != 200:
            print(f"  [FAIL] Case {i} ({cid}): HTTP {res.status_code} - {res.text}")
            continue
            
        out = res.json()
        
        # Check scenario_id
        if out.get("scenario_id") != payload["scenario_id"]:
            print(f"  [FAIL] Case {i} ({cid}): scenario_id mismatch")
            continue
            
        # Check directives count
        if len(out["directive_interpretation"]) != len(payload["operator_notes"]):
            print(f"  [FAIL] Case {i} ({cid}): Directive count mismatch")
            continue
            
        # Check cost difference
        exp_cost = exp["total_cost_bdt"]
        act_cost = out["total_cost_bdt"]
        diff_cost = abs(exp_cost - act_cost)
        
        exp_grid = exp["total_grid_kwh"]
        act_grid = out["total_grid_kwh"]
        diff_grid = abs(exp_grid - act_grid)
        
        exp_peak = exp["peak_grid_kwh"]
        act_peak = out["peak_grid_kwh"]
        diff_peak = abs(exp_peak - act_peak)
        
        # Check energy balance on hourly plan
        hplan = out["hourly_plan"]
        if len(hplan) != 24:
            print(f"  [FAIL] Case {i} ({cid}): Hourly plan must have 24 hours")
            continue
            
        balance_ok = True
        for h in range(24):
            item = hplan[h]
            h_input = payload["hours"][h]
            demand = h_input["demand_kwh"]
            grid = item["grid_kwh"]
            solar = item["solar_used_kwh"]
            action = item["battery_action"]
            bkwh = item["battery_kwh"]
            
            dis = bkwh if action == "discharge" else 0.0
            chg = bkwh if action == "charge" else 0.0
            
            lhs = grid + solar + dis
            rhs = demand + chg
            if abs(lhs - rhs) > 0.02:
                print(f"  [FAIL] Hour {h} balance violation: {lhs} vs {rhs}")
                balance_ok = False
                break
                
        # Neutrality
        e_init = payload["battery"]["initial_energy_kwh"]
        e_final = hplan[23]["battery_energy_after_kwh"]
        if abs(e_init - e_final) > 0.02:
            print(f"  [FAIL] Battery neutrality violated: init={e_init}, final={e_final}")
            balance_ok = False
            
        if not balance_ok:
            continue
            
        if diff_cost <= 0.05 and diff_grid <= 0.05:
            passed_cases += 1
            print(f"  [PASS] Case {i:02d} ({cid}) - {label[:30]:30s} | Grid: {act_grid:.1f} kWh | Cost: {act_cost:.0f} BDT | Peak: {act_peak:.1f} | Latency: {dt:.1f}ms")
        else:
            print(f"  [WARN] Case {i:02d} ({cid}) - Cost diff: {diff_cost:.2f}, Grid diff: {diff_grid:.2f}")

    print(f"\nSummary: {passed_cases}/{len(cases)} cases passed all checks successfully!")
    return passed_cases == len(cases)

def verify_local_direct():
    print("=== Running Local Direct Pipeline Verification ===")
    data = json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))
    cases = data["cases"]
    passed = 0
    
    for i, case in enumerate(cases):
        req = OptimizeEnergyRequest(**case["input"])
        raw = interpret_operator_notes(req.operator_notes, req.battery)
        directives = validate_and_guardrail_directives(raw, req.operator_notes, req.battery)
        plan, total_grid, total_cost, peak_grid, summary = solve_energy_optimization(
            req.hours, req.battery, directives
        )
        exp = case["expected_output"]
        diff_c = abs(total_cost - exp["total_cost_bdt"])
        diff_g = abs(total_grid - exp["total_grid_kwh"])
        if diff_c <= 0.05 and diff_g <= 0.05:
            passed += 1
            print(f"  [PASS] Case {i}: {case['label']} (Cost: {total_cost} BDT, Grid: {total_grid} kWh)")
        else:
            print(f"  [FAIL] Case {i}: {case['label']} (Cost diff: {diff_c})")
            
    print(f"Local Direct: {passed}/{len(cases)} passed.")
    return passed == len(cases)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--http":
        url = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8000"
        verify_against_api(url)
    else:
        verify_local_direct()
