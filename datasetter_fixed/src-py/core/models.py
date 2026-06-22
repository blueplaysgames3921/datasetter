"""
core/models.py

All Pydantic schemas and enums for Datasetter.
Every agent, the pipeline state machine, and the API layer uses these.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


# ─── Enums ────────────────────────────────────────────────────────────────────


class AgentID(str, Enum):
    ORCHESTRATOR = "orchestrator"
    INTERPRETER  = "interpreter"
    ANALYSER     = "analyser"
    RESEARCHER   = "researcher"
    GENERATOR    = "generator"
    SCRIPTER     = "scripter"
    VERIFIER     = "verifier"
    FIXER        = "fixer"


class AgentStatus(str, Enum):
    IDLE     = "idle"
    RUNNING  = "running"
    DONE     = "done"
    FAILED   = "failed"
    SKIPPED  = "skipped"


class PipelineMode(str, Enum):
    VIBE     = "vibe"       # prompt only → I → G → S → V
    FILE     = "file"       # files attached → A → I → G → S → V
    RESEARCH = "research"   # internet needed → I → R → G → S → V
    EDIT     = "edit"       # modifying existing → A → I → S → V
    MINIMAL  = "minimal"    # simple job → I → S → V


class PipelineStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    PAUSED    = "paused"
    COMPLETE  = "complete"
    FAILED    = "failed"
    CANCELLED = "cancelled"


class RowStatus(str, Enum):
    PENDING = "pending"   # not yet generated
    OK      = "ok"        # verified and passed
    ERROR   = "error"     # verifier flagged, awaiting fix
    FIXING  = "fixing"    # fixer is working on it
    MANUAL  = "manual"    # manually edited by user, needs re-verify


class ErrorType(str, Enum):
    SEMANTIC    = "semantic"     # response doesn't make sense
    LOGIC       = "logic"        # logical inconsistency
    CONSTRAINT  = "constraint"   # violates a user-defined constraint
    CONSISTENCY = "consistency"  # inconsistent with other rows / tone
    FORMAT      = "format"       # wrong format or structure
    LENGTH      = "length"       # too short or too long


class InferenceMode(str, Enum):
    CLOUD = "cloud"
    LOCAL = "local"
    AUTO  = "auto"     # orchestrator decides based on job + hardware


class OutputFormat(str, Enum):
    JSONL   = "jsonl"
    CSV     = "csv"
    PARQUET = "parquet"
    TSV     = "tsv"
    JSON    = "json"
    ARROW   = "arrow"
    XML     = "xml"
    XLSX    = "xlsx"


class VerifyMode(str, Enum):
    BATCH      = "batch"
    ONE_BY_ONE = "one_by_one"


class NotificationLevel(str, Enum):
    INFO    = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR   = "error"


# ─── Hardware ─────────────────────────────────────────────────────────────────


class GPUInfo(BaseModel):
    name: str
    vram_gb: float
    vendor: Literal["nvidia", "amd", "intel", "apple", "unknown"] = "unknown"


class HardwareProfile(BaseModel):
    gpu: Optional[GPUInfo] = None
    ram_gb: float = 0.0
    cpu_name: str = "Unknown"
    has_npu: bool = False
    npu_name: Optional[str] = None
    os: str = "Unknown"
    # Inference engines installed
    has_ollama: bool = False
    has_llama_cpp: bool = False
    has_mlx: bool = False           # Apple silicon
    has_onnx_runtime: bool = False  # NPU / generic
    # Computed capability tier
    tier: Literal["low", "mid", "high", "ultra"] = "low"


# ─── Model assignment ─────────────────────────────────────────────────────────


class ModelAssignment(BaseModel):
    """Model config for one agent."""
    agent_id: AgentID
    cloud_model: Optional[str] = None   # LiteLLM model string, None = not available
    local_model: Optional[str] = None   # local model name, None = not available
    mode: InferenceMode = InferenceMode.AUTO
    # Provider for cloud (used by LiteLLM router)
    cloud_provider: Optional[str] = None
    # Inference engine for local
    local_engine: Optional[str] = None  # "ollama" | "llama.cpp" | "mlx" | "onnx"
    # Quantization level for local
    quantization: Optional[str] = None  # "q4_k_m" | "q5_k_m" | "q8_0" | etc.


class ModelConfig(BaseModel):
    """Full model assignment for all agents."""
    assignments: Dict[AgentID, ModelAssignment] = Field(default_factory=dict)


# ─── Dataset row ──────────────────────────────────────────────────────────────


class VerifierError(BaseModel):
    """Structured error from Verifier, consumed directly by Fixer."""
    row_id: int
    error_type: ErrorType
    field: str                  # which field is wrong: "response", "instruction", etc.
    description: str            # human-readable description of the problem
    fix_instruction: str        # exact instruction for Fixer to follow
    severity: Literal["fatal", "minor"] = "minor"


class DatasetRow(BaseModel):
    id: int
    status: RowStatus = RowStatus.PENDING
    category: str = ""
    fields: Dict[str, Any] = Field(default_factory=dict)  # flexible — supports any schema
    errors: List[VerifierError] = Field(default_factory=list)
    fix_rounds: int = 0
    manually_edited: bool = False
    # Metadata
    seed_id: Optional[int] = None    # which seed this was generated from
    batch_id: Optional[int] = None   # which scripter batch


# ─── Job configuration ────────────────────────────────────────────────────────


class FieldConstraint(BaseModel):
    """User-defined constraint on a specific output field."""
    field: str
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    required: bool = True
    description: str = ""
    examples: List[str] = Field(default_factory=list)
    forbidden_patterns: List[str] = Field(default_factory=list)


class JobConfig(BaseModel):
    """Everything the user configured for this generation job."""
    # Identity
    job_id: UUID = Field(default_factory=uuid4)
    name: str = "Untitled Dataset"

    # Prompt + context
    prompt: str
    extra_context: str = ""
    negative_prompt: str = ""
    attached_files: List[str] = Field(default_factory=list)  # local file paths

    # Output
    output_format: OutputFormat = OutputFormat.JSONL
    use_case: str = ""
    total_rows: int = 500
    category_count: Optional[int] = None   # None = auto
    seed_count: int = 50                   # max 150
    language: str = "English"

    # Field schema
    field_constraints: List[FieldConstraint] = Field(default_factory=list)

    # Generation
    diversity_level: int = Field(default=4, ge=1, le=5)
    edge_case_coverage: Literal["low", "medium", "high"] = "high"

    # Verification
    verify_mode: VerifyMode = VerifyMode.BATCH
    batch_size: int = 50
    strictness: int = Field(default=3, ge=1, le=5)
    error_halt_threshold: float = 0.10   # halt if >10% of batch errors
    auto_fix: bool = True
    max_fix_rounds: int = 3

    # Checks to run
    check_semantic: bool = True
    check_logic: bool = True
    check_constraints: bool = True
    check_consistency: bool = True
    check_format: bool = True
    check_length: bool = True

    # KV cache management (Verifier)
    kv_cache_clear_interval: int = 50   # clear every N rows if Transformer model

    # Models (overrides defaults if set)
    model_overrides: Dict[AgentID, ModelAssignment] = Field(default_factory=dict)

    # Pipeline (set by Interpreter, not user)
    pipeline_mode: Optional[PipelineMode] = None


# ─── Seed examples ────────────────────────────────────────────────────────────


class SeedExample(BaseModel):
    id: int
    category: str
    fields: Dict[str, Any]      # same shape as DatasetRow.fields
    is_edge_case: bool = False
    edge_case_type: Optional[str] = None   # "ambiguous", "minimal", "adversarial", etc.


class SeedPack(BaseModel):
    """Output of Generator, input to Scripter."""
    seeds: List[SeedExample]
    categories: List[str]
    category_targets: Dict[str, int]   # category → target row count
    generation_spec: Dict[str, Any]    # full spec from Interpreter
    blueprint: str                     # natural language guide for Scripter + Verifier


# ─── Pipeline state ───────────────────────────────────────────────────────────


class AgentState(BaseModel):
    agent_id: AgentID
    status: AgentStatus = AgentStatus.IDLE
    started_at: Optional[float] = None    # unix timestamp
    finished_at: Optional[float] = None
    current_task: str = ""
    rows_processed: int = 0
    error_message: Optional[str] = None
    model_used: Optional[str] = None
    tokens_used: int = 0


class PipelineState(BaseModel):
    """Live state of the running pipeline. Emitted as SSE events."""
    job_id: UUID
    status: PipelineStatus = PipelineStatus.PENDING
    pipeline_mode: Optional[PipelineMode] = None
    active_agents: List[AgentID] = Field(default_factory=list)
    agent_states: Dict[AgentID, AgentState] = Field(default_factory=dict)

    # Progress
    total_rows: int = 0
    generated_rows: int = 0
    verified_rows: int = 0
    error_rows: int = 0
    fixed_rows: int = 0

    # Timing
    started_at: Optional[float] = None
    estimated_finish: Optional[float] = None

    # Current category being processed
    current_category: Optional[str] = None
    current_batch: Optional[int] = None
    total_batches: Optional[int] = None


# ─── Events (SSE) ─────────────────────────────────────────────────────────────


class EventType(str, Enum):
    PIPELINE_STATUS  = "pipeline_status"
    AGENT_LOG        = "agent_log"
    AGENT_STATUS     = "agent_status"
    ROW_UPDATE       = "row_update"
    ROWS_BATCH       = "rows_batch"
    NOTIFICATION     = "notification"
    ERROR            = "error"
    COMPLETE         = "complete"


class AgentLogEvent(BaseModel):
    event: Literal[EventType.AGENT_LOG] = EventType.AGENT_LOG
    job_id: UUID
    agent_id: AgentID
    message: str
    timestamp: float


class AgentStatusEvent(BaseModel):
    event: Literal[EventType.AGENT_STATUS] = EventType.AGENT_STATUS
    job_id: UUID
    agent_id: AgentID
    status: AgentStatus
    model_used: Optional[str] = None
    current_task: str = ""


class RowUpdateEvent(BaseModel):
    event: Literal[EventType.ROW_UPDATE] = EventType.ROW_UPDATE
    job_id: UUID
    row: DatasetRow


class RowsBatchEvent(BaseModel):
    event: Literal[EventType.ROWS_BATCH] = EventType.ROWS_BATCH
    job_id: UUID
    rows: List[DatasetRow]


class PipelineStatusEvent(BaseModel):
    event: Literal[EventType.PIPELINE_STATUS] = EventType.PIPELINE_STATUS
    state: PipelineState


class NotificationEvent(BaseModel):
    event: Literal[EventType.NOTIFICATION] = EventType.NOTIFICATION
    level: NotificationLevel
    title: str
    message: str


class ErrorEvent(BaseModel):
    event: Literal[EventType.ERROR] = EventType.ERROR
    job_id: UUID
    agent_id: Optional[AgentID] = None
    message: str
    recoverable: bool = True


AnyEvent = Union[
    AgentLogEvent, AgentStatusEvent, RowUpdateEvent, RowsBatchEvent,
    PipelineStatusEvent, NotificationEvent, ErrorEvent
]


# ─── API request/response ─────────────────────────────────────────────────────


class StartJobRequest(BaseModel):
    config: JobConfig


class StartJobResponse(BaseModel):
    job_id: UUID
    pipeline_mode: PipelineMode
    active_agents: List[AgentID]
    message: str


class PauseJobRequest(BaseModel):
    job_id: UUID


class ResumeJobRequest(BaseModel):
    job_id: UUID


class CancelJobRequest(BaseModel):
    job_id: UUID


class EditRowRequest(BaseModel):
    job_id: UUID
    row_id: int
    fields: Dict[str, Any]


class AcceptRowRequest(BaseModel):
    job_id: UUID
    row_id: int


class FlagRowRequest(BaseModel):
    job_id: UUID
    row_id: int
    reason: str = "Manual flag."


class RegenerateRowRequest(BaseModel):
    job_id: UUID
    row_id: int
    agent_id: Optional[AgentID] = None  # None = orchestrator decides


class ExportRequest(BaseModel):
    job_id: UUID
    output_format: OutputFormat
    include_statuses: List[RowStatus] = Field(default_factory=lambda: [RowStatus.OK])
    output_path: Optional[str] = None   # None = default export dir


class ExportResponse(BaseModel):
    path: str
    row_count: int
    format: OutputFormat


class ProjectSummary(BaseModel):
    job_id: UUID
    name: str
    status: PipelineStatus
    pipeline_mode: PipelineMode
    total_rows: int
    verified_rows: int
    error_rows: int
    output_format: OutputFormat
    created_at: float
    updated_at: float


class APIKeyConfig(BaseModel):
    """User-configured API keys and endpoints."""
    anthropic: Optional[str] = None
    google: Optional[str] = None
    deepinfra: Optional[str] = None
    featherless: Optional[str] = None
    groq: Optional[str] = None
    sambanova: Optional[str] = None
    fireworks: Optional[str] = None
    novita: Optional[str] = None
    siliconflow: Optional[str] = None
    runpod: Optional[str] = None
    # Personal OpenAI-compatible endpoints
    custom_endpoints: List[Dict[str, str]] = Field(default_factory=list)
    # { "url": "http://...", "label": "Home Desktop", "api_key": "" }


class AppSettings(BaseModel):
    """Persisted app-wide settings."""
    model_config = {"populate_by_name": True}  # Pydantic v2 config

    api_keys: APIKeyConfig = Field(default_factory=APIKeyConfig)
    agent_models: ModelConfig = Field(default_factory=ModelConfig)  # renamed from model_config_ to avoid Pydantic v2 conflict
    hardware: Optional[HardwareProfile] = None

    # Generation defaults
    default_rows: int = 500
    default_seed_count: int = 50
    default_language: str = "English"
    default_format: OutputFormat = OutputFormat.JSONL
    default_diversity: int = 4
    default_edge_case_coverage: str = "high"
    default_negative_prompt: str = ""

    # Verification defaults
    default_verify_mode: VerifyMode = VerifyMode.BATCH
    default_batch_size: int = 50
    default_strictness: int = 3
    default_auto_fix: bool = True
    default_max_fix_rounds: int = 3
    default_kv_cache_clear_interval: int = 50

    # Pipeline defaults
    default_max_retries: int = 3
    default_retry_delay: float = 2.0

    # System
    background_mode: bool = True
    push_notifications: bool = True
    notify_batch_complete: bool = True
    notify_errors: bool = True
    notify_pipeline_done: bool = True
    storage_path: str = "~/datasetter/"
    export_path: str = "~/datasetter/exports/"
