"""RBAC models for MoatBot access control."""
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Set
from datetime import datetime, timezone
from enum import Enum
import uuid


class UserRole(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class Permission(str, Enum):
    # Agent management
    AGENTS_VIEW = "agents:view"
    AGENTS_CREATE = "agents:create"
    AGENTS_UPDATE = "agents:update"
    AGENTS_DELETE = "agents:delete"
    AGENTS_START = "agents:start"
    AGENTS_STOP = "agents:stop"
    
    # Audit logs
    AUDIT_VIEW = "audit:view"
    AUDIT_EXPORT = "audit:export"
    AUDIT_DELETE = "audit:delete"
    
    # LLM Router
    LLM_VIEW = "llm:view"
    LLM_CONFIG = "llm:config"
    LLM_PROVIDERS_MANAGE = "llm:providers:manage"
    
    # Orchestration
    ORCHESTRATION_VIEW = "orchestration:view"
    ORCHESTRATION_APPROVE = "orchestration:approve"
    ORCHESTRATION_EXECUTE = "orchestration:execute"
    ORCHESTRATION_CANCEL = "orchestration:cancel"
    
    # System controls
    SYSTEM_SAFE_MODE = "system:safe_mode"
    SYSTEM_KILL_SWITCH = "system:kill_switch"
    SYSTEM_CONFIG = "system:config"
    
    # RBAC
    RBAC_VIEW = "rbac:view"
    RBAC_MANAGE = "rbac:manage"
    
    # Git/Source
    GIT_VIEW = "git:view"
    GIT_SYNC = "git:sync"


# Role to permissions mapping
ROLE_PERMISSIONS: Dict[UserRole, Set[Permission]] = {
    UserRole.ADMIN: set(Permission),  # All permissions
    
    UserRole.OPERATOR: {
        # Agent management (limited)
        Permission.AGENTS_VIEW,
        Permission.AGENTS_START,
        Permission.AGENTS_STOP,
        
        # Full audit access
        Permission.AUDIT_VIEW,
        Permission.AUDIT_EXPORT,
        
        # LLM viewing
        Permission.LLM_VIEW,
        
        # Orchestration
        Permission.ORCHESTRATION_VIEW,
        Permission.ORCHESTRATION_APPROVE,
        Permission.ORCHESTRATION_EXECUTE,
        Permission.ORCHESTRATION_CANCEL,
        
        # Safe mode only (not kill switch)
        Permission.SYSTEM_SAFE_MODE,
        
        # View RBAC
        Permission.RBAC_VIEW,
        
        # Git viewing
        Permission.GIT_VIEW,
    },
    
    UserRole.VIEWER: {
        Permission.AGENTS_VIEW,
        Permission.AUDIT_VIEW,
        Permission.LLM_VIEW,
        Permission.ORCHESTRATION_VIEW,
        Permission.RBAC_VIEW,
        Permission.GIT_VIEW,
    }
}


class User(BaseModel):
    """User entity for RBAC."""
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    username: str
    email: Optional[str] = None
    
    # Role
    role: UserRole = UserRole.VIEWER
    
    # Custom permissions (in addition to role permissions)
    extra_permissions: List[Permission] = Field(default_factory=list)
    denied_permissions: List[Permission] = Field(default_factory=list)
    
    # Status
    is_active: bool = True
    last_login: Optional[datetime] = None
    
    # Tracking
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    def has_permission(self, permission: Permission) -> bool:
        """Check if user has a specific permission."""
        if not self.is_active:
            return False
        
        # Check denied permissions first
        if permission in self.denied_permissions:
            return False
        
        # Check role permissions
        role_perms = ROLE_PERMISSIONS.get(self.role, set())
        if permission in role_perms:
            return True
        
        # Check extra permissions
        return permission in self.extra_permissions
    
    def get_all_permissions(self) -> Set[Permission]:
        """Get all effective permissions for this user."""
        if not self.is_active:
            return set()
        
        role_perms = ROLE_PERMISSIONS.get(self.role, set())
        all_perms = role_perms.union(set(self.extra_permissions))
        return all_perms - set(self.denied_permissions)


class UserCreate(BaseModel):
    """Create user request."""
    username: str
    email: Optional[str] = None
    role: UserRole = UserRole.VIEWER
    password: str


class UserUpdate(BaseModel):
    """Update user request."""
    email: Optional[str] = None
    role: Optional[UserRole] = None
    extra_permissions: Optional[List[Permission]] = None
    denied_permissions: Optional[List[Permission]] = None
    is_active: Optional[bool] = None


class AuthToken(BaseModel):
    """Authentication token."""
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user_id: str
    role: UserRole
    permissions: List[Permission]


class LoginRequest(BaseModel):
    """Login request."""
    username: str
    password: str


class SystemState(BaseModel):
    """Global system state."""
    safe_mode_active: bool = False
    safe_mode_activated_at: Optional[datetime] = None
    safe_mode_activated_by: Optional[str] = None
    
    kill_switch_active: bool = False
    kill_switch_activated_at: Optional[datetime] = None
    kill_switch_activated_by: Optional[str] = None
    
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
