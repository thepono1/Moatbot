"""LLM models for multi-provider routing."""
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from enum import Enum
import uuid


class ProviderName(str, Enum):
    OPENAI = "openai"
    GEMINI = "gemini"
    ANTHROPIC = "anthropic"  # Disabled by default


class ProviderStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    DISABLED = "disabled"


class CircuitState(str, Enum):
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, rejecting requests
    HALF_OPEN = "half_open"  # Testing recovery


class LLMProvider(BaseModel):
    """LLM Provider configuration and state."""
    model_config = ConfigDict(extra="ignore")
    
    name: ProviderName
    enabled: bool = True
    priority: int = 1  # Lower = higher priority
    
    # Available models
    models: List[str] = Field(default_factory=list)
    default_model: str = ""
    
    # Health state
    status: ProviderStatus = ProviderStatus.HEALTHY
    last_health_check: Optional[datetime] = None
    consecutive_failures: int = 0
    last_error: Optional[str] = None
    
    # Circuit breaker
    circuit_state: CircuitState = CircuitState.CLOSED
    circuit_opened_at: Optional[datetime] = None
    circuit_half_open_at: Optional[datetime] = None
    
    # Rate limiting
    requests_this_minute: int = 0
    rate_limit_per_minute: int = 60
    
    # Cost tracking
    total_tokens_used: int = 0
    total_cost_usd: float = 0.0
    budget_limit_usd: Optional[float] = None
    
    # Metrics
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    avg_latency_ms: float = 0.0


class ProviderHealth(BaseModel):
    """Provider health check result."""
    provider: ProviderName
    status: ProviderStatus
    latency_ms: float
    error: Optional[str] = None
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LLMRequest(BaseModel):
    """LLM request model."""
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str
    agent_id: str
    
    # Request parameters
    messages: List[Dict[str, str]]
    system_prompt: Optional[str] = None
    max_tokens: int = 4096
    temperature: float = 0.7
    
    # Routing preferences
    preferred_provider: Optional[ProviderName] = None
    preferred_model: Optional[str] = None
    allow_fallback: bool = True
    
    # Metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LLMResponse(BaseModel):
    """LLM response model."""
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    request_id: str
    correlation_id: str
    
    # Provider info
    provider: ProviderName
    model: str
    used_fallback: bool = False
    
    # Response
    content: str
    finish_reason: Optional[str] = None
    
    # Usage
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    
    # Performance
    latency_ms: float = 0.0
    
    # Metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RouterConfig(BaseModel):
    """LLM Router configuration."""
    # Provider priorities (lower = higher priority)
    provider_priorities: Dict[ProviderName, int] = Field(default_factory=lambda: {
        ProviderName.OPENAI: 1,
        ProviderName.GEMINI: 2,
        ProviderName.ANTHROPIC: 99  # Disabled by default
    })
    
    # Circuit breaker settings
    circuit_failure_threshold: int = 5
    circuit_recovery_timeout_seconds: int = 60
    circuit_half_open_max_requests: int = 3
    
    # Retry settings
    max_retries: int = 3
    retry_delay_seconds: float = 1.0
    retry_backoff_multiplier: float = 2.0
    
    # Timeout settings
    request_timeout_seconds: int = 30
    
    # Health check settings
    health_check_interval_seconds: int = 60
    
    # Budget settings
    global_budget_limit_usd: Optional[float] = None
    alert_threshold_percent: float = 80.0


# Default provider configurations
DEFAULT_PROVIDERS = {
    ProviderName.OPENAI: LLMProvider(
        name=ProviderName.OPENAI,
        enabled=True,
        priority=1,
        models=["gpt-5.2", "gpt-5.1", "gpt-4o", "gpt-4.1"],
        default_model="gpt-5.2",
        rate_limit_per_minute=60
    ),
    ProviderName.GEMINI: LLMProvider(
        name=ProviderName.GEMINI,
        enabled=True,
        priority=2,
        models=["gemini-3-flash-preview", "gemini-3-pro-preview", "gemini-2.5-pro", "gemini-2.5-flash"],
        default_model="gemini-3-flash-preview",
        rate_limit_per_minute=60
    ),
    ProviderName.ANTHROPIC: LLMProvider(
        name=ProviderName.ANTHROPIC,
        enabled=False,  # Disabled by default per requirements
        priority=99,
        models=["claude-sonnet-4-5-20250929", "claude-4-sonnet-20250514"],
        default_model="claude-sonnet-4-5-20250929",
        rate_limit_per_minute=60
    )
}
