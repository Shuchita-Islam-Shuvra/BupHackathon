from typing import List, Dict, Any, Optional
import math
from app.schemas import DirectiveInterpretation, BatteryInput

SUPPORTED_DIRECTIVE_TYPES = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op"
}

def clean_hours_list(hours_raw: Any) -> List[int]:
    """Ensures hours is a sorted, unique list of integers between 0 and 23."""
    if not isinstance(hours_raw, list):
        return []
    cleaned = set()
    for h in hours_raw:
        try:
            h_int = int(h)
            if 0 <= h_int <= 23:
                cleaned.add(h_int)
        except (ValueError, TypeError):
            continue
    return sorted(list(cleaned))

def validate_and_guardrail_directives(
    raw_directives: List[Dict[str, Any]],
    operator_notes: List[str],
    battery: BatteryInput
) -> List[DirectiveInterpretation]:
    """
    Applies Section 08 LLM Interpretation Guardrails:
    - Exactly one entry per note in note_index order (0..N-1)
    - Type verification
    - Hours sorting & bounds (0-23)
    - Factor normalization (0.0 - 1.0)
    - Reserve bounds (finite, non-negative, <= capacity)
    - Grid cap bounds (finite, non-negative)
    - applies / structured_adjustment semantics
    """
    total_notes = len(operator_notes)
    # Map by note_index
    indexed_map: Dict[int, Dict[str, Any]] = {}
    
    for item in raw_directives:
        if not isinstance(item, dict):
            continue
        idx = item.get("note_index")
        if isinstance(idx, int) and 0 <= idx < total_notes:
            indexed_map[idx] = item

    guarded_results: List[DirectiveInterpretation] = []

    for idx in range(total_notes):
        item = indexed_map.get(idx)
        
        # Default safe fallback if missing
        if item is None:
            guarded_results.append(DirectiveInterpretation(
                note_index=idx,
                applies=False,
                directive_type="no_op",
                structured_adjustment=None,
                explanation="Safe default: unparsed note treated as no_op."
            ))
            continue

        raw_type = str(item.get("directive_type", "no_op")).strip().lower()
        if raw_type not in SUPPORTED_DIRECTIVE_TYPES:
            raw_type = "no_op"

        raw_adj = item.get("structured_adjustment")
        explanation = str(item.get("explanation", "")).strip() or "Standard directive interpretation."

        if raw_type == "no_op":
            guarded_results.append(DirectiveInterpretation(
                note_index=idx,
                applies=False,
                directive_type="no_op",
                structured_adjustment=None,
                explanation=explanation or "This note does not affect the energy schedule."
            ))
            continue

        # For all other directives, applies MUST be True
        applies = True
        if not isinstance(raw_adj, dict):
            raw_adj = {}

        hours = clean_hours_list(raw_adj.get("hours", []))

        # Build clean structured_adjustment
        clean_adj: Dict[str, Any] = {"hours": hours}

        if raw_type == "solar_reduction":
            factor_val = raw_adj.get("factor", 1.0)
            try:
                factor = float(factor_val)
                # If given as percentage > 1 (e.g. 20 for 20%), normalize
                if factor > 1.0 and factor <= 100.0:
                    factor = factor / 100.0
                factor = max(0.0, min(1.0, factor))
            except (ValueError, TypeError):
                factor = 1.0
            clean_adj["factor"] = round(factor, 4)

        elif raw_type == "minimum_battery_reserve":
            res_val = raw_adj.get("minimum_energy_kwh", battery.minimum_energy_kwh)
            try:
                res_float = float(res_val)
                # If given as percentage (e.g., <= 1.0 or explicit fraction)
                if res_float <= 1.0 and res_float > 0:
                    res_float = res_float * battery.capacity_kwh
                # Ensure within bounds [0, capacity]
                res_float = max(0.0, min(battery.capacity_kwh, res_float))
                if math.isnan(res_float) or math.isinf(res_float):
                    res_float = battery.minimum_energy_kwh
            except (ValueError, TypeError):
                res_float = battery.minimum_energy_kwh
            clean_adj["minimum_energy_kwh"] = round(res_float, 2)

        elif raw_type == "max_grid_window":
            grid_val = raw_adj.get("max_grid_kwh", 0.0)
            try:
                grid_float = float(grid_val)
                grid_float = max(0.0, grid_float)
                if math.isnan(grid_float) or math.isinf(grid_float):
                    grid_float = 0.0
            except (ValueError, TypeError):
                grid_float = 0.0
            clean_adj["max_grid_kwh"] = round(grid_float, 2)

        elif raw_type in ("no_charge_window", "no_discharge_window"):
            # Only requires {"hours": [...]}
            pass

        guarded_results.append(DirectiveInterpretation(
            note_index=idx,
            applies=applies,
            directive_type=raw_type, # type: ignore
            structured_adjustment=clean_adj,
            explanation=explanation
        ))

    return guarded_results
