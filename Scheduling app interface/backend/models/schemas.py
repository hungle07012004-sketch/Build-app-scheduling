from typing import Literal
from pydantic import BaseModel, Field, ConfigDict

class LoginRequest(BaseModel):
    email: str
    password: str

class SolverSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed_base: int = Field(42, ge=0, le=2147483647)
    runs: int = Field(1, ge=1, le=10)
    ga_population: int = Field(80, ge=2, le=500)
    ga_generations: int = Field(500, ge=1, le=10000)
    mlpso_population: int = Field(60, ge=3, le=500)
    mlpso_iterations: int = Field(500, ge=1, le=10000)
    mlpso_max_decodes: int = Field(12000, ge=1, le=100000)
    milp_time_limit: float = Field(600, gt=0, le=7200)
    exact_time_limit: float = Field(120, gt=0, le=7200)
    exact_workers: int = Field(0, ge=0, le=32)
    micro_population: int = Field(24, ge=4, le=100)
    micro_generations: int = Field(3000, ge=1, le=10000)
    micro_max_decodes: int = Field(12000, ge=100, le=100000)
    micro_time_limit: float = Field(120, gt=0, le=7200)
    micro_no_improve: int = Field(600, ge=0, le=10000)
    micro_min_epochs: int = Field(1300, ge=0, le=10000)
    ga_mutation_rate: float = Field(0.12, ge=0, le=0.45)
    ga_crossover_rate: float = Field(0.90, ge=0, le=1)
    micro_mutation_rate: float = Field(0.15, ge=0, le=0.60)
    micro_crossover_rate: float = Field(0.90, ge=0, le=1)
    mlpso_mr: float = Field(0.80, ge=0, le=1)
    mlpso_pcr: float = Field(0.80, ge=0, le=1)
    mlpso_gcr: float = Field(0.40, ge=0, le=1)
    target_cmax: float | None = Field(None, gt=0)
    target_polish_generations: int = Field(500, ge=1, le=5000)

class ScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: str
    job_ids: list[str] = Field(min_length=1)
    horizon_start: str
    horizon_end: str
    algorithms: list[Literal["CP-SAT","SS Micro-GA Fast","MILP","Improved GA","MLPSO"]] = Field(default_factory=list, max_length=3)
    settings: SolverSettings = Field(default_factory=SolverSettings)

class ExecutionUpdate(BaseModel):
    run_id: str
    status: Literal["Scheduled", "In Progress", "Completed", "Delayed"]

class JobUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_code: str | None = None
    priority: int | None = Field(None, ge=1)
    release_datetime: str | None = None
    element_ready_datetime: str | None = None
    loopset_release_datetime: str | None = None
    due_datetime: str | None = None
