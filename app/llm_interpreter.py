import os
import re
import json
import logging
from typing import List, Dict, Any, Optional
from app.schemas import BatteryInput

logger = logging.getLogger("gridwise.llm")

SYSTEM_PROMPT = """You are an expert energy operations interpreter for the GridWise Smart Campus system.
Your task is to analyze 1 to 3 operator notes and output a strict JSON list of directive interpretations.

SUPPORTED DIRECTIVE TYPES:
1. "solar_reduction":
   - Use when solar output/PV/panels are reduced (e.g., due to cleaning, clouds, inverter work).
   - "factor": usable fraction remaining (between 0.0 and 1.0).
     Example: "drop to 20%" -> factor 0.2; "80% reduction" -> factor 0.2; "half" -> factor 0.5; "one-fifth" -> factor 0.2.
   - "hours": whole-hour array, start-inclusive, end-exclusive.
     Example: "noon until 2 PM" -> [12, 13]; "1 PM to 3 PM" -> [13, 14]; "11 AM and 2 PM" -> [11, 12, 13].
   - structured_adjustment: {"hours": [...], "factor": number}
   - applies: true

2. "minimum_battery_reserve":
   - Use when battery must maintain a reserve level for emergencies or critical loads.
   - "minimum_energy_kwh": required minimum energy in kWh. If stated as percentage of battery capacity (e.g. 50% with capacity 200), compute 0.5 * capacity = 100.
   - structured_adjustment: {"hours": [...], "minimum_energy_kwh": number}
   - applies: true

3. "no_charge_window":
   - Use when battery charging is disabled, charger isolated, or unavailable.
   - structured_adjustment: {"hours": [...]}
   - applies: true

4. "no_discharge_window":
   - Use when battery discharging is forbidden or relay testing occurs.
   - structured_adjustment: {"hours": [...]}
   - applies: true

5. "max_grid_window":
   - Use when campus grid import/intake/feeder is capped at a maximum kWh.
   - structured_adjustment: {"hours": [...], "max_grid_kwh": number}
   - applies: true

6. "no_op":
   - Use for irrelevant announcements, future notices, cafeteria menus, sports events, room bookings, etc. that do not affect today's energy schedule.
   - structured_adjustment: null
   - applies: false

RULES:
- Every note must produce exactly one entry with matching note_index (0, 1, ... N-1).
- Time windows are whole-hour intervals, start included, end excluded.
  - "noon" = 12, "midnight" = 0.
  - "6 PM until 9 PM" = [18, 19, 20].
  - "2 AM until 5 AM" = [2, 3, 4].
  - "11 AM until 1 PM" = [11, 12].
- Response MUST be ONLY a valid JSON list of objects. No markdown fencing, no conversational text.

Example JSON output:
[
  {
    "note_index": 0,
    "applies": true,
    "directive_type": "solar_reduction",
    "structured_adjustment": {"hours": [12, 13], "factor": 0.25},
    "explanation": "Solar availability reduced to 25% during washing."
  },
  {
    "note_index": 1,
    "applies": false,
    "directive_type": "no_op",
    "structured_adjustment": null,
    "explanation": "Unrelated announcement."
  }
]
"""

def parse_time_window(text: str) -> List[int]:
    """Robust natural-language time window extractor for whole-hour intervals."""
    text_lower = text.lower()

    # Replace words with standard tokens
    text_lower = text_lower.replace("noon", "12 pm").replace("midnight", "12 am")
    text_lower = text_lower.replace("one", "1").replace("two", "2").replace("three", "3").replace("four", "4").replace("five", "5")
    # Extended numeric words so "from six to nine pm" works
    text_lower = re.sub(r"\bsix\b", "6", text_lower)
    text_lower = re.sub(r"\bseven\b", "7", text_lower)
    text_lower = re.sub(r"\beight\b", "8", text_lower)
    text_lower = re.sub(r"\bnine\b", "9", text_lower)
    text_lower = re.sub(r"\bten\b", "10", text_lower)
    text_lower = re.sub(r"\beleven\b", "11", text_lower)

    # Insert space between digit and "am"/"pm" so "1pm" -> "1 pm" for matching.
    text_lower = re.sub(r"(\d)\s*(am|pm)\b", r"\1 \2", text_lower)

    # 24-hour time range with optional AM/PM markers between :00 and connector.
    # Matches "13:00 to 15:00", "from 6:00 PM to 9:00 PM", "14:00-16:00".
    m_24 = re.search(
        r"(\d{1,2}):00\s*(am|pm)?\s*(?:and|to|until|-)\s*(\d{1,2}):00\s*(am|pm)?", text_lower)
    if m_24:
        s, s_mer = int(m_24.group(1)), m_24.group(2)
        e, e_mer = int(m_24.group(3)), m_24.group(4)
        # If any AM/PM marker is present, both ends inherit it; otherwise 24h.
        any_mer = s_mer or e_mer
        if any_mer:
            mer = any_mer
            if mer == "pm":
                if s != 12 and s < 12: s += 12
                if e != 12 and e < 12: e += 12
            elif mer == "am":
                if s == 12: s = 0
                if e == 12: e = 0
        if 0 <= s < e <= 24:
            return list(range(s, e))

    # "1-3 pm", "1 to 3 pm", "1 pm to 3 pm" (after space-insert above).
    # The most permissive form: "X am/pm [to/until/-] Y am/pm".  Captures any
    # of the variants people actually write for whole-hour ranges.
    m_dash = re.search(r"(\d{1,2})\s*(am|pm)\s*(?:-|to|until)\s*(\d{1,2})\s*(am|pm)", text_lower)
    if m_dash:
        s, s_mer, e, e_mer = int(m_dash.group(1)), m_dash.group(2), int(m_dash.group(3)), m_dash.group(4)
        if s_mer == "pm" and s != 12: s += 12
        elif s_mer == "am" and s == 12: s = 0
        if e_mer == "pm" and e != 12: e += 12
        elif e_mer == "am" and e == 12: e = 0
        if 0 <= s < e <= 24:
            return list(range(s, e))

    # "1 to 3 pm" / "1-3 pm" / "1-3" (one am/pm marker at the end implies both)
    m_dash_simple = re.search(r"(\d{1,2})\s*(?:-|to|until)\s*(\d{1,2})\s*(am|pm)", text_lower)
    if m_dash_simple:
        s, e, mer = int(m_dash_simple.group(1)), int(m_dash_simple.group(2)), m_dash_simple.group(3)
        if mer == "pm":
            if s != 12 and s < 12: s += 12
            if e != 12 and e < 12: e += 12
        elif mer == "am":
            if s == 12: s = 0
            if e == 12: e = 0
        if 0 <= s < e <= 24:
            return list(range(s, e))

    # "from X (am/pm)? (until|to|and) Y (am/pm)" or "between X (am/pm)? and Y (am/pm)"
    m_range = re.search(r"(?:from|between)\s+(\d{1,2})\s*(am|pm)?\s+(?:until|to|and)\s+(\d{1,2})\s*(am|pm)", text_lower)
    if m_range:
        s, s_mer, e, e_mer = int(m_range.group(1)), m_range.group(2), int(m_range.group(3)), m_range.group(4)
        if not s_mer:
            s_mer = e_mer # inherit am/pm if omitted
        if s_mer == "pm" and s != 12: s += 12
        elif s_mer == "am" and s == 12: s = 0
        if e_mer == "pm" and e != 12: e += 12
        elif e_mer == "am" and e == 12: e = 0
        if 0 <= s < e <= 24:
            return list(range(s, e))

    return []

def semantic_fallback_interpreter(
    operator_notes: List[str],
    battery: BatteryInput
) -> List[Dict[str, Any]]:
    """
    High-accuracy deterministic NLP fallback parser that generalizes over
    diverse phrasing and guarantees zero failure if LLM API is unavailable.
    """
    results = []
    
    for idx, note in enumerate(operator_notes):
        n_low = note.lower()
        hours = parse_time_window(note)
        
        # 1. Distractors / No-Op checks
        # If note talks about irrelevant campus topics with no energy/battery/solar/grid constraints
        has_energy_kw = any(w in n_low for w in ["solar", "pv", "panel", "battery", "charge", "charg", "discharge", "grid", "feeder", "transformer", "substation", "kwh"])
        is_distractor = any(w in n_low for w in ["sports", "registration", "cafeteria", "menu", "library", "book", "club", "seminar", "booking", "next week", "next month"])
        
        if not has_energy_kw or (is_distractor and not hours):
            results.append({
                "note_index": idx,
                "applies": False,
                "directive_type": "no_op",
                "structured_adjustment": None,
                "explanation": "Note relates to administrative announcement without grid or battery schedule impact."
            })
            continue

        # 2. Solar Reduction
        if any(w in n_low for w in ["solar", "pv", "panel", "sun"]):
            factor = 1.0
            # Check percentage or fraction
            if "half" in n_low or "1/2" in n_low or "one-half" in n_low:
                factor = 0.5
            elif "one-fifth" in n_low or "1/5" in n_low:
                factor = 0.2
            elif "one-fourth" in n_low or "quarter" in n_low or "1/4" in n_low:
                factor = 0.25
            else:
                pct_match = re.search(r"(\d{1,3})\s*%", n_low)
                if pct_match:
                    val = float(pct_match.group(1)) / 100.0
                    # Check if it says "reduction of X%" or "drop by X%" vs "drop to X%" or "treated as X%"
                    if any(w in n_low for w in ["reduction", "drop by", "reduced by", "decrease by", "loss"]):
                        factor = max(0.0, 1.0 - val)
                    else:
                        factor = val
                else:
                    factor = 0.5

            results.append({
                "note_index": idx,
                "applies": True,
                "directive_type": "solar_reduction",
                "structured_adjustment": {
                    "hours": hours,
                    "factor": round(factor, 4)
                },
                "explanation": f"Solar output adjusted to factor {factor} during specified window."
            })
            continue

        # 3. No Charge Window
        # Note: Must check no_charge before battery reserve.  Substring matching
        # is unsafe ("discharging" contains "charging"); we use word-boundary
        # regexes on the trigger words to avoid false positives.
        nc_patterns = [
            r"\bcharger\b\s+(?:will\s+be\s+)?isolated",
            r"\bdo\s+not\s+charge\b",
            r"\bcharging\s+is\s+(?:disabled|not\s+permitted|unavailable)",
            r"\bcharging\s+circuit\s+will\s+be\s+unavailable",
            r"\bcannot\s+charge\b",
            r"\bno\s+charging\b",
            r"\bblock\s+charging\b",
            r"\bpause\s+charging\b",
            r"\bskip\s+charging\b",
            r"\bhold\s+off\s+on\s+charging",
            r"\bno\s+charge\b",
            r"\bcharging\s+(?:unavailable|disabled|not\s+allowed)",
        ]
        if any(re.search(p, n_low) for p in nc_patterns):
            results.append({
                "note_index": idx,
                "applies": True,
                "directive_type": "no_charge_window",
                "structured_adjustment": {"hours": hours},
                "explanation": "Battery charging prohibited during specified maintenance window."
            })
            continue
            results.append({
                "note_index": idx,
                "applies": True,
                "directive_type": "no_charge_window",
                "structured_adjustment": {"hours": hours},
                "explanation": "Battery charging prohibited during specified maintenance window."
            })
            continue

        # 4. No Discharge Window
        nd_patterns = [
            r"\bdo\s+not\s+discharge\b",
            r"\bmust\s+not\s+discharge\b",
            r"\bdischarging\s+is\s+(?:disabled|not\s+permitted|unavailable)",
            r"\bcannot\s+discharge\b",
            r"\bno\s+discharging\b",
            r"\bblock\s+discharging\b",
            r"\bpause\s+discharging\b",
            r"\bskip\s+discharging\b",
            r"\bhold\s+off\s+on\s+discharging",
            r"\bno\s+discharge\b",
            r"\bdo\s+not\s+use\s+battery\b",
            r"\bdischarging\s+(?:unavailable|disabled|not\s+allowed)",
        ]
        if any(re.search(p, n_low) for p in nd_patterns):
            results.append({
                "note_index": idx,
                "applies": True,
                "directive_type": "no_discharge_window",
                "structured_adjustment": {"hours": hours},
                "explanation": "Battery discharging prohibited during specified protection window."
            })
            continue

        # 5. Minimum Battery Reserve
        if any(w in n_low for w in ["reserve", "remain in the battery", "stored in the battery", "keep at least"]):
            req_kwh = battery.minimum_energy_kwh
            pct_match = re.search(r"(\d{1,3})\s*%", n_low)
            kwh_match = re.search(r"(\d+(?:\.\d+)?)\s*kwh", n_low)
            
            if pct_match and not kwh_match:
                pct = float(pct_match.group(1)) / 100.0
                req_kwh = round(pct * battery.capacity_kwh, 2)
            elif kwh_match:
                req_kwh = float(kwh_match.group(1))

            results.append({
                "note_index": idx,
                "applies": True,
                "directive_type": "minimum_battery_reserve",
                "structured_adjustment": {
                    "hours": hours,
                    "minimum_energy_kwh": req_kwh
                },
                "explanation": f"Battery reserve maintained at or above {req_kwh} kWh."
            })
            continue

        # 6. Max Grid Window
        if any(w in n_low for w in ["grid import", "grid intake", "transformer limit", "feeder", "grid must not exceed", "grid cap", "substation", "grid limit", "import no more than", "cap imports", "limit intake", "stay below", "no more than"]):
            cap_val = 0.0
            kwh_match = re.search(r"(\d+(?:\.\d+)?)\s*kwh", n_low)
            if kwh_match:
                cap_val = float(kwh_match.group(1))
            else:
                num_match = re.search(r"(?:exceed|is|below|stay at|limit(?: is)?)\s+(\d+(?:\.\d+)?)", n_low)
                if num_match:
                    cap_val = float(num_match.group(1))

            results.append({
                "note_index": idx,
                "applies": True,
                "directive_type": "max_grid_window",
                "structured_adjustment": {
                    "hours": hours,
                    "max_grid_kwh": cap_val
                },
                "explanation": f"Grid import constrained to {cap_val} kWh maximum."
            })
            continue

        # Fallback to no_op
        results.append({
            "note_index": idx,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": "No actionable energy scheduling directive found in operator note."
        })

    return results

def call_gemini_api(notes: List[str], battery: BatteryInput, api_key: str) -> Optional[List[Dict[str, Any]]]:
    """Call Google Gemini generative model for note interpretation."""
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        
        user_content = json.dumps({
            "operator_notes": notes,
            "battery_capacity_kwh": battery.capacity_kwh,
            "battery_minimum_energy_kwh": battery.minimum_energy_kwh
        })
        prompt = f"{SYSTEM_PROMPT}\n\nInput scenario data:\n{user_content}\n\nJSON output:"
        
        # Try primary model first, with automatic fallback across model versions
        for model_name in ["gemini-2.5-flash", "gemini-1.5-flash", "gemini-2.0-flash"]:
            try:
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(
                    prompt,
                    generation_config={"temperature": 0.0, "response_mime_type": "application/json"}
                )
                text = response.text.strip()
                if text.startswith("```json"):
                    text = text[7:]
                if text.startswith("```"):
                    text = text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                parsed = json.loads(text.strip())
                if isinstance(parsed, list):
                    return parsed
            except Exception as me:
                logger.debug(f"Model {model_name} failed: {me}")
                continue
        return None
    except Exception as e:
        logger.warning(f"Gemini API call failed: {e}")
        return None

def call_openai_api(notes: List[str], battery: BatteryInput, api_key: str) -> Optional[List[Dict[str, Any]]]:
    """Call OpenAI API for note interpretation."""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        
        user_content = json.dumps({
            "operator_notes": notes,
            "battery_capacity_kwh": battery.capacity_kwh,
            "battery_minimum_energy_kwh": battery.minimum_energy_kwh
        })
        
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.0,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content}
            ],
            response_format={"type": "json_object"}
        )
        content = resp.choices[0].message.content or "{}"
        parsed = json.loads(content)
        if isinstance(parsed, list):
            return parsed
        elif isinstance(parsed, dict) and "directives" in parsed:
            return parsed["directives"]
        elif isinstance(parsed, dict) and "directive_interpretation" in parsed:
            return parsed["directive_interpretation"]
        return None
    except Exception as e:
        logger.warning(f"OpenAI API call failed: {e}")
        return None

def interpret_operator_notes(
    notes: List[str],
    battery: BatteryInput
) -> List[Dict[str, Any]]:
    """
    Main LLM interpretation entrypoint.
    Checks environment for GEMINI_API_KEY or OPENAI_API_KEY.
    If available, runs model. If unavailable or fails, falls back gracefully.
    """
    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if gemini_key:
        llm_out = call_gemini_api(notes, battery, gemini_key)
        if llm_out and isinstance(llm_out, list) and len(llm_out) == len(notes):
            return llm_out
            
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        llm_out = call_openai_api(notes, battery, openai_key)
        if llm_out and isinstance(llm_out, list) and len(llm_out) == len(notes):
            return llm_out

    # Resilient fallback
    logger.info("Using deterministic NLP interpreter fallback.")
    return semantic_fallback_interpreter(notes, battery)
