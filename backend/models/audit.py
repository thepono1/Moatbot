"""Audit models for MoatBot logging and traceability."""
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from enum import Enum
import uuid
import re


class AuditEventType(str, Enum):
    # Discord events
    DISCORD_MESSAGE_RECEIVED = "discord.message.received"
    DISCORD_MESSAGE_SENT = "discord.message.sent"
    DISCORD_REACTION = "discord.reaction"
    
    # Routing events
    ROUTING_DECISION = "routing.decision"
    ROUTING_FALLBACK = "routing.fallback"
    
    # LLM events
    LLM_REQUEST = "llm.request"
    LLM_RESPONSE = "llm.response"
    LLM_ERROR = "llm.error"
    LLM_CIRCUIT_OPEN = "llm.circuit.open"
    LLM_CIRCUIT_CLOSE = "llm.circuit.close"
    
    # Tool/Action events
    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"
    TOOL_ERROR = "tool.error"
    
    # Git/Source events
    GIT_COMMIT_CHECK = "git.commit.check"
    GIT_DIFF_DETECTED = "git.diff.detected"
    GIT_SNAPSHOT = "git.snapshot"
    
    # Orchestration events
    PLAN_CREATED = "orchestration.plan.created"
    PLAN_APPROVED = "orchestration.plan.approved"
    PLAN_REJECTED = "orchestration.plan.rejected"
    EXECUTION_STARTED = "orchestration.execution.started"
    EXECUTION_COMPLETED = "orchestration.execution.completed"
    EXECUTION_FAILED = "orchestration.execution.failed"
    VERIFICATION_STARTED = "orchestration.verification.started"
    VERIFICATION_PASSED = "orchestration.verification.passed"
    VERIFICATION_FAILED = "orchestration.verification.failed"
    
    # System events
    AGENT_STARTED = "system.agent.started"
    AGENT_STOPPED = "system.agent.stopped"
    SAFE_MODE_ACTIVATED = "system.safe_mode.activated"
    SAFE_MODE_DEACTIVATED = "system.safe_mode.deactivated"
    KILL_SWITCH_ACTIVATED = "system.kill_switch.activated"
    AUTH_SUCCESS = "system.auth.success"
    AUTH_FAILURE = "system.auth.failure"


class AuditSeverity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# Patterns for secret redaction
REDACTION_PATTERNS = [
    (r'(sk-[a-zA-Z0-9]{20,})', '[REDACTED_API_KEY]'),
    (r'(token["\']?\s*[:=]\s*["\']?)([^"\'\s]{10,})', r'\1[REDACTED_TOKEN]'),
    (r'(password["\']?\s*[:=]\s*["\']?)([^"\'\s]+)', r'\1[REDACTED_PASSWORD]'),
    (r'(secret["\']?\s*[:=]\s*["\']?)([^"\'\s]+)', r'\1[REDACTED_SECRET]'),
    (r'(Bearer\s+)([A-Za-z0-9._-]+)', r'\1[REDACTED_BEARER]'),
]


def redact_secrets(data: Any) -> Any:
    """Recursively redact secrets from data."""
    if isinstance(data, str):
        for pattern, replacement in REDACTION_PATTERNS:
            data = re.sub(pattern, replacement, data, flags=re.IGNORECASE)
        return data
    elif isinstance(data, dict):
        return {k: redact_secrets(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [redact_secrets(item) for item in data]
    return data


class AuditEvent(BaseModel):
    """Audit event for logging all operations."""
    model_config = ConfigDict(extra="ignore")
    
    # Identifiers
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str  # Links related events together
    parent_id: Optional[str] = None  # For nested events
    
    # Event metadata
    event_type: AuditEventType
    severity: AuditSeverity = AuditSeverity.INFO
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Context
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    user_id: Optional[str] = None
    discord_channel_id: Optional[str] = None
    discord_guild_id: Optional[str] = None
    
    # Source of truth
    git_commit_sha: Optional[str] = None
    git_branch: Optional[str] = None
    
    # Event data
    message: str
    data: Dict[str, Any] = Field(default_factory=dict)
    
    # Performance
    duration_ms: Optional[float] = None
    
    # Error info
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    stack_trace: Optional[str] = None
    
    def to_jsonl(self, redact: bool = True) -> str:
        """Convert to JSONL format with optional secret redaction."""
        import json
        data = self.model_dump()
        data['timestamp'] = data['timestamp'].isoformat()
        if redact:
            data = redact_secrets(data)
        return json.dumps(data, default=str)


class AuditEventCreate(BaseModel):
    """Create audit event request."""
    correlation_id: str
    parent_id: Optional[str] = None
    event_type: AuditEventType
    severity: AuditSeverity = AuditSeverity.INFO
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    message: str
    data: Dict[str, Any] = Field(default_factory=dict)
    duration_ms: Optional[float] = None


class AuditQuery(BaseModel):
    """Query parameters for audit log search."""
    correlation_id: Optional[str] = None
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    event_types: Optional[List[AuditEventType]] = None
    severity: Optional[List[AuditSeverity]] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    search_text: Optional[str] = None
    limit: int = 100
    offset: int = 0


class AuditTimeline(BaseModel):
    """Timeline view of correlated audit events."""
    correlation_id: str
    start_time: datetime
    end_time: Optional[datetime] = None
    agent_name: Optional[str] = None
    summary: str
    events: List[AuditEvent]
    status: str = "in_progress"  # "completed", "failed", "in_progress"
