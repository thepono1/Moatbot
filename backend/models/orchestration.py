"""Orchestration models for Plan -> Execute -> Verify lifecycle."""
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from enum import Enum
import uuid


class OrchestrationPhase(str, Enum):
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AcceptanceCriterion(BaseModel):
    """Single acceptance criterion for verification."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    description: str
    test_method: str  # How to test this criterion
    expected_result: str
    actual_result: Optional[str] = None
    passed: Optional[bool] = None
    verified_at: Optional[datetime] = None


class PlanStep(BaseModel):
    """Single step in an execution plan."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    order: int
    action: str
    description: str
    agent_id: Optional[str] = None
    tool_name: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    
    # Grounding
    git_commit_sha: Optional[str] = None
    file_references: List[str] = Field(default_factory=list)
    
    # Execution state
    status: str = "pending"  # pending, running, completed, failed, skipped
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class Plan(BaseModel):
    """Execution plan for an orchestration workflow."""
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str
    
    # Plan metadata
    name: str
    description: str
    created_by_agent: str
    
    # Grounding
    git_commit_sha: str
    git_branch: str
    workspace_snapshot_id: Optional[str] = None
    
    # Plan content
    steps: List[PlanStep] = Field(default_factory=list)
    acceptance_criteria: List[AcceptanceCriterion] = Field(default_factory=list)
    
    # Risk assessment
    risk_level: str = "low"  # low, medium, high, critical
    requires_approval: bool = True
    estimated_duration_seconds: Optional[int] = None
    
    # Approval
    approved: Optional[bool] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    
    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Execution(BaseModel):
    """Execution record for a plan."""
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    plan_id: str
    correlation_id: str
    
    # Execution state
    status: str = "pending"  # pending, running, paused, completed, failed, cancelled
    current_step_index: int = 0
    
    # Results
    completed_steps: int = 0
    failed_steps: int = 0
    skipped_steps: int = 0
    
    # Outputs
    outputs: Dict[str, Any] = Field(default_factory=dict)
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    
    # Safe mode / kill switch
    paused_by_safe_mode: bool = False
    cancelled_by_kill_switch: bool = False
    
    # Timestamps
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # Performance
    total_duration_ms: Optional[float] = None
    llm_tokens_used: int = 0
    llm_cost_usd: float = 0.0


class Verification(BaseModel):
    """Verification record for an execution."""
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    execution_id: str
    plan_id: str
    correlation_id: str
    
    # Verification state
    status: str = "pending"  # pending, running, passed, failed, partial
    
    # Results
    criteria_results: List[AcceptanceCriterion] = Field(default_factory=list)
    passed_count: int = 0
    failed_count: int = 0
    total_count: int = 0
    
    # Summary
    summary: str = ""
    recommendations: List[str] = Field(default_factory=list)
    
    # Timestamps
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class OrchestrationState(BaseModel):
    """Full orchestration state for a workflow."""
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str
    
    # Current phase
    phase: OrchestrationPhase = OrchestrationPhase.PLANNING
    
    # Components
    plan: Optional[Plan] = None
    execution: Optional[Execution] = None
    verification: Optional[Verification] = None
    
    # Context
    trigger_source: str = "manual"  # manual, discord, webhook, scheduled
    trigger_data: Dict[str, Any] = Field(default_factory=dict)
    
    # Agent assignments
    planner_agent_id: Optional[str] = None
    executor_agent_id: Optional[str] = None
    verifier_agent_id: Optional[str] = None
    
    # Safe mode
    safe_mode_active: bool = False
    
    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None


class MentorCommand(BaseModel):
    """Mentor command input (/mentor)."""
    task: str
    context: Dict[str, Any] = Field(default_factory=dict)
    agent_id: Optional[str] = None


class VerifyPlanCommand(BaseModel):
    """Verify plan command input (/verify-plan)."""
    plan_id: str
    auto_generate_criteria: bool = True
    custom_criteria: List[str] = Field(default_factory=list)


class SolomonCommand(BaseModel):
    """Solomon verification command input (/solomon)."""
    execution_id: str
    verification_type: str = "full"  # full, quick, custom
    focus_areas: List[str] = Field(default_factory=list)
