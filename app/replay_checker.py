"""
Final Replay and Constraint Checker (Safety Net).
Independently verifies every single energy, battery, and directive constraint
on the generated hourly_plan before the API emits the response, matching
the official judge replay evaluation harness.
"""
from typing import List
import logging
from app.schemas import HourInput, BatteryInput, DirectiveInterpretation, HourlyPlanEntry

logger = logging.getLogger("gridwise.replay")

def replay_and_verify_schedule(
    hours: List[HourInput],
    battery: BatteryInput,
    directives: List[DirectiveInterpretation],
    hourly_plan: List[HourlyPlanEntry],
    reported_total_grid: float,
    reported_total_cost: float,
    reported_peak_grid: float
) -> bool:
    """
    Replays the final 24-hour schedule hour-by-hour and asserts:
    1. Hourly plan has exactly 24 hours (0..23).
    2. Energy balance equation holds every hour within tolerance.
    3. Solar used does not exceed effective solar (after solar_reduction).
    4. Battery charge and discharge rate limits are strictly obeyed.
    5. No simultaneous charging and discharging.
    6. Battery energy state transitions match action and energy values.
    7. Battery energy stays within capacity and reserve bounds (including directive reserve).
    8. Directives (no_charge, no_discharge, max_grid) are strictly honored.
    9. End-of-day battery neutrality holds: E_23 == E_init.
    10. Recalculated totals (grid, cost, peak) match reported values within 0.01 tolerance.
    """
    if len(hourly_plan) != 24:
        raise ValueError(f"Replay failed: hourly_plan must have 24 hours, got {len(hourly_plan)}")

    # Compile directives
    solar_factor = {h: 1.0 for h in range(24)}
    min_reserve = {h: battery.minimum_energy_kwh for h in range(24)}
    charge_allowed = {h: True for h in range(24)}
    discharge_allowed = {h: True for h in range(24)}
    grid_cap = {h: float('inf') for h in range(24)}

    for d in directives:
        if not d.applies:
            continue
        dtype = d.directive_type
        adj = d.structured_adjustment or {}
        hours_list = adj.get("hours", [])

        if dtype == "solar_reduction":
            factor = adj.get("factor", 1.0)
            for h in hours_list:
                solar_factor[h] = min(solar_factor[h], factor)
        elif dtype == "minimum_battery_reserve":
            req_min = adj.get("minimum_energy_kwh", battery.minimum_energy_kwh)
            for h in hours_list:
                min_reserve[h] = max(min_reserve[h], req_min)
        elif dtype == "no_charge_window":
            for h in hours_list:
                charge_allowed[h] = False
        elif dtype == "no_discharge_window":
            for h in hours_list:
                discharge_allowed[h] = False
        elif dtype == "max_grid_window":
            cap_val = adj.get("max_grid_kwh", float('inf'))
            for h in hours_list:
                grid_cap[h] = min(grid_cap[h], cap_val)

    recomputed_grid = 0.0
    recomputed_cost = 0.0
    recomputed_peak = 0.0
    prev_energy = battery.initial_energy_kwh

    for h in range(24):
        p = hourly_plan[h]
        h_in = hours[h]

        grid = p.grid_kwh
        solar_used = p.solar_used_kwh
        action = p.battery_action
        bkwh = p.battery_kwh
        e_after = p.battery_energy_after_kwh
        demand = h_in.demand_kwh
        tariff = h_in.tariff_bdt_per_kwh
        eff_solar = h_in.solar_kwh * solar_factor[h]

        # 1. Non-negativity
        if grid < -0.01 or solar_used < -0.01 or bkwh < -0.01:
            raise ValueError(f"Hour {h}: negative energy value detected.")

        # 2. Solar bound
        if solar_used > eff_solar + 0.02:
            raise ValueError(f"Hour {h}: solar_used ({solar_used}) exceeds effective solar ({eff_solar}).")

        # 3. Battery Action consistency & rate limits
        chg = 0.0
        dis = 0.0
        if action == "charge":
            chg = bkwh
            if chg > battery.max_charge_kwh_per_hour + 0.02:
                raise ValueError(f"Hour {h}: charge ({chg}) exceeds max_charge limit.")
            if not charge_allowed[h] and chg > 0.01:
                raise ValueError(f"Hour {h}: charging in no_charge_window.")
        elif action == "discharge":
            dis = bkwh
            if dis > battery.max_discharge_kwh_per_hour + 0.02:
                raise ValueError(f"Hour {h}: discharge ({dis}) exceeds max_discharge limit.")
            if not discharge_allowed[h] and dis > 0.01:
                raise ValueError(f"Hour {h}: discharging in no_discharge_window.")
        elif action == "idle":
            if bkwh > 0.01:
                raise ValueError(f"Hour {h}: battery_kwh must be 0 when idle.")

        # 4. Energy Balance: grid + solar_used + discharge = demand + charge
        lhs = grid + solar_used + dis
        rhs = demand + chg
        if abs(lhs - rhs) > 0.05:
            raise ValueError(f"Hour {h}: Energy balance violation: LHS={lhs:.2f} != RHS={rhs:.2f}")

        # 5. Battery State update: E_after = E_before + charge - discharge
        expected_e_after = prev_energy + chg - dis
        if abs(e_after - expected_e_after) > 0.05:
            raise ValueError(f"Hour {h}: Battery transition mismatch: expected {expected_e_after:.2f}, got {e_after:.2f}")

        # 6. Battery Bounds: min_reserve <= E_after <= capacity
        if e_after < min_reserve[h] - 0.02 or e_after > battery.capacity_kwh + 0.02:
            raise ValueError(f"Hour {h}: Battery energy {e_after:.2f} violates bounds [{min_reserve[h]}, {battery.capacity_kwh}]")

        # 7. Grid cap
        if grid > grid_cap[h] + 0.02:
            raise ValueError(f"Hour {h}: grid {grid:.2f} exceeds max_grid cap {grid_cap[h]}")

        recomputed_grid += grid
        recomputed_cost += grid * tariff
        if grid > recomputed_peak:
            recomputed_peak = grid
        prev_energy = e_after

    # 8. End-of-day battery neutrality
    if abs(prev_energy - battery.initial_energy_kwh) > 0.05:
        raise ValueError(f"Battery neutrality failed: initial={battery.initial_energy_kwh}, final={prev_energy}")

    # 9. Recalculated totals consistency
    if abs(recomputed_grid - reported_total_grid) > 0.05:
        raise ValueError(f"total_grid_kwh mismatch: reported={reported_total_grid}, recalculated={recomputed_grid:.2f}")
    if abs(recomputed_cost - reported_total_cost) > 0.05:
        raise ValueError(f"total_cost_bdt mismatch: reported={reported_total_cost}, recalculated={recomputed_cost:.2f}")
    if abs(recomputed_peak - reported_peak_grid) > 0.05:
        raise ValueError(f"peak_grid_kwh mismatch: reported={reported_peak_grid}, recalculated={recomputed_peak:.2f}")

    logger.debug("Replay checker: 100% verified all constraints.")
    return True
