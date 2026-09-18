from typing import List, Optional, Literal, Dict, Any, Union
from pydantic import BaseModel, Field, field_validator

class HourInput(BaseModel):
    hour: int = Field(..., ge=0, le=23, description="Hour of the day (0-23)")
    demand_kwh: float = Field(..., ge=0, description="Campus electricity demand in kWh")
    solar_kwh: float = Field(..., ge=0, description="Forecasted solar generation in kWh")
    tariff_bdt_per_kwh: float = Field(..., ge=0, description="Grid tariff in BDT per kWh")

class BatteryInput(BaseModel):
    capacity_kwh: float = Field(..., gt=0, description="Maximum energy the battery can store")
    initial_energy_kwh: float = Field(..., ge=0, description="Battery energy at the start of hour 0")
    minimum_energy_kwh: float = Field(..., ge=0, description="Base reserve level the battery must never go below")
    max_charge_kwh_per_hour: float = Field(..., ge=0, description="Maximum energy added per hour")
    max_discharge_kwh_per_hour: float = Field(..., ge=0, description="Maximum energy removed per hour")

    @field_validator("initial_energy_kwh")
    @classmethod
    def validate_initial_energy(cls, v, info):
        # Initial energy cannot exceed capacity
        cap = info.data.get("capacity_kwh")
        if cap is not None and v > cap:
            raise ValueError(f"initial_energy_kwh ({v}) cannot exceed capacity_kwh ({cap})")
        return v

class OptimizeEnergyRequest(BaseModel):
    scenario_id: str = Field(..., description="Unique synthetic scenario identifier")
    operator_notes: List[str] = Field(..., min_length=1, max_length=3, description="1-3 natural-language notes")
    hours: List[HourInput] = Field(..., min_length=24, max_length=24, description="Hourly entries for 0 through 23")
    battery: BatteryInput = Field(..., description="Battery specifications")

    @field_validator("hours")
    @classmethod
    def validate_hours_sequence(cls, v):
        hours_seen = [h.hour for h in v]
        if sorted(hours_seen) != list(range(24)):
            raise ValueError("hours array must contain exactly 24 entries with unique hours 0 through 23")
        return v

DirectiveType = Literal[
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op"
]

BatteryAction = Literal["charge", "discharge", "idle"]

class DirectiveInterpretation(BaseModel):
    note_index: int = Field(..., ge=0)
    applies: bool
    directive_type: DirectiveType
    structured_adjustment: Optional[Dict[str, Any]] = None
    explanation: str

class HourlyPlanEntry(BaseModel):
    hour: int = Field(..., ge=0, le=23)
    grid_kwh: float = Field(..., ge=0)
    solar_used_kwh: float = Field(..., ge=0)
    battery_action: BatteryAction
    battery_kwh: float = Field(..., ge=0)
    battery_energy_after_kwh: float = Field(..., ge=0)

class OptimizeEnergyResponse(BaseModel):
    scenario_id: str
    directive_interpretation: List[DirectiveInterpretation]
    hourly_plan: List[HourlyPlanEntry]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str

class HealthResponse(BaseModel):
    status: str = "ok"
