"""Agent models for MoatBot multi-agent Discord operations."""
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from enum import Enum
import uuid


class AgentStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ERROR = "error"
    SAFE_MODE = "safe_mode"


class AgentPermission(str, Enum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    ADMIN = "admin"


class AgentConfig(BaseModel):
    """Configuration for an agent."""
    model_config = ConfigDict(extra="ignore")
    
    # Discord configuration
    discord_token_env_var: str  # e.g., "DISCORD_TOKEN_KALEO"
    allowed_channels: List[str] = Field(default_factory=list)
    allowed_guilds: List[str] = Field(default_factory=list)
    
    # LLM configuration
    preferred_provider: str = "openai"
    preferred_model: str = "gpt-5.2"
    fallback_provider: Optional[str] = "gemini"
    fallback_model: Optional[str] = "gemini-3-flash-preview"
    
    # Behavior configuration
    system_prompt: str = "You are a helpful AI assistant."
    max_tokens: int = 4096
    temperature: float = 0.7
    
    # Permission boundaries
    permissions: List[AgentPermission] = Field(default_factory=lambda: [AgentPermission.READ])
    can_execute_code: bool = False
    can_modify_files: bool = False
    requires_approval: bool = True


class Agent(BaseModel):
    """Agent entity representing a Discord bot identity."""
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str  # e.g., "Kaleo", "Solomon", "Deborah"
    description: str = ""
    role: str = "assistant"  # e.g., "mentor", "verifier", "executor"
    
    config: AgentConfig
    status: AgentStatus = AgentStatus.INACTIVE
    
    # Runtime state
    token_validated: bool = False
    last_health_check: Optional[datetime] = None
    error_message: Optional[str] = None
    
    # Tracking
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Metrics
    messages_processed: int = 0
    llm_calls_made: int = 0
    errors_count: int = 0


class AgentCreate(BaseModel):
    """Create agent request."""
    name: str
    description: str = ""
    role: str = "assistant"
    config: AgentConfig


class AgentUpdate(BaseModel):
    """Update agent request."""
    description: Optional[str] = None
    role: Optional[str] = None
    config: Optional[AgentConfig] = None
    status: Optional[AgentStatus] = None


# Default agent configurations
DEFAULT_AGENTS = {
    "kaleo": AgentConfig(
        discord_token_env_var="DISCORD_TOKEN_KALEO",
        preferred_provider="openai",
        preferred_model="gpt-5.2",
        system_prompt="You are Kaleo, an expert AI assistant specializing in strategic planning and mentorship.",
        permissions=[AgentPermission.READ, AgentPermission.WRITE],
        requires_approval=False
    ),
    "solomon": AgentConfig(
        discord_token_env_var="DISCORD_TOKEN_SOLOMON",
        preferred_provider="openai",
        preferred_model="gpt-5.2",
        fallback_provider="gemini",
        fallback_model="gemini-3-flash-preview",
        system_prompt="You are Solomon, a wise verifier and validator. Your role is to verify plans and ensure quality.",
        permissions=[AgentPermission.READ, AgentPermission.EXECUTE],
        requires_approval=True
    ),
    "deborah": AgentConfig(
        discord_token_env_var="DISCORD_TOKEN_DEBORAH",
        preferred_provider="gemini",
        preferred_model="gemini-3-flash-preview",
        system_prompt="You are Deborah, an orchestrator who coordinates between agents and manages workflows.",
        permissions=[AgentPermission.READ, AgentPermission.WRITE, AgentPermission.EXECUTE],
        requires_approval=True
    )
}
