"""RBAC Service for authentication, authorization, safe mode, and kill switch."""
import os
import logging
import hashlib
import secrets
from typing import Dict, List, Optional
from datetime import datetime, timezone, timedelta

from models.rbac import (
    User, UserCreate, UserUpdate, UserRole, Permission,
    AuthToken, LoginRequest, SystemState, ROLE_PERMISSIONS
)
from models.audit import AuditEventType, AuditSeverity

logger = logging.getLogger(__name__)

# JWT secret — auto-generates if not set
JWT_SECRET = os.environ.get("JWT_SECRET", secrets.token_hex(32))
TOKEN_EXPIRY_HOURS = 24


class RBACService:
    """Service for RBAC, auth, safe mode, and kill switch."""

    def __init__(self, db, audit_logger=None):
        self.db = db
        self.users_collection = db.users
        self.system_collection = db.system_state
        self.audit_logger = audit_logger
        self._tokens: Dict[str, AuthToken] = {}
        self._system_state: Optional[SystemState] = None

    # --- User management ---

    async def create_user(self, data: UserCreate) -> User:
        """Create a new user."""
        # Check for duplicate username
        existing = await self.users_collection.find_one({"username": data.username})
        if existing:
            raise ValueError(f"Username '{data.username}' already exists")

        user = User(
            username=data.username,
            email=data.email,
            role=data.role,
        )

        doc = user.model_dump()
        doc["password_hash"] = self._hash_password(data.password)
        doc["created_at"] = doc["created_at"].isoformat()
        doc["updated_at"] = doc["updated_at"].isoformat()
        if doc.get("last_login"):
            doc["last_login"] = doc["last_login"].isoformat()

        await self.users_collection.insert_one(doc)
        return user

    async def get_user(self, user_id: str) -> Optional[User]:
        """Get user by ID."""
        doc = await self.users_collection.find_one({"id": user_id}, {"_id": 0})
        if doc:
            return self._doc_to_user(doc)
        return None

    async def get_user_by_username(self, username: str) -> Optional[Dict]:
        """Get user doc by username (includes password_hash)."""
        doc = await self.users_collection.find_one({"username": username}, {"_id": 0})
        return doc

    async def list_users(self) -> List[User]:
        """List all users."""
        docs = await self.users_collection.find(
            {}, {"_id": 0, "password_hash": 0}
        ).to_list(100)
        return [self._doc_to_user(d) for d in docs]

    async def update_user(self, user_id: str, data: UserUpdate) -> Optional[User]:
        """Update a user."""
        update_data = {k: v for k, v in data.model_dump().items() if v is not None}
        if not update_data:
            return await self.get_user(user_id)

        update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        await self.users_collection.update_one({"id": user_id}, {"$set": update_data})
        return await self.get_user(user_id)

    async def delete_user(self, user_id: str) -> bool:
        """Delete a user."""
        result = await self.users_collection.delete_one({"id": user_id})
        return result.deleted_count > 0

    # --- Authentication ---

    async def login(self, request: LoginRequest) -> Optional[AuthToken]:
        """Authenticate user and return token."""
        doc = await self.get_user_by_username(request.username)
        if not doc:
            await self._log_event(
                "auth", AuditEventType.AUTH_FAILURE,
                f"Login failed: user '{request.username}' not found"
            )
            return None

        if not self._verify_password(request.password, doc.get("password_hash", "")):
            await self._log_event(
                "auth", AuditEventType.AUTH_FAILURE,
                f"Login failed: wrong password for '{request.username}'"
            )
            return None

        user = self._doc_to_user(doc)
        if not user.is_active:
            return None

        # Update last login
        await self.users_collection.update_one(
            {"id": user.id},
            {"$set": {"last_login": datetime.now(timezone.utc).isoformat()}}
        )

        token = self._create_token(user)
        await self._log_event(
            "auth", AuditEventType.AUTH_SUCCESS,
            f"User '{user.username}' logged in"
        )
        return token

    def validate_token(self, token_str: str) -> Optional[AuthToken]:
        """Validate an access token."""
        token = self._tokens.get(token_str)
        if not token:
            return None
        if datetime.now(timezone.utc) > token.expires_at:
            del self._tokens[token_str]
            return None
        return token

    def check_permission(self, token_str: str, permission: Permission) -> bool:
        """Check if token holder has a permission."""
        token = self.validate_token(token_str)
        if not token:
            return False
        return permission in token.permissions

    # --- System controls ---

    async def get_system_state(self) -> SystemState:
        """Get current system state."""
        if self._system_state:
            return self._system_state

        doc = await self.system_collection.find_one({"_key": "system"}, {"_id": 0})
        if doc:
            for f in ["safe_mode_activated_at", "kill_switch_activated_at", "last_updated"]:
                if doc.get(f) and isinstance(doc[f], str):
                    doc[f] = datetime.fromisoformat(doc[f])
            doc.pop("_key", None)
            self._system_state = SystemState(**doc)
        else:
            self._system_state = SystemState()

        return self._system_state

    async def activate_safe_mode(self, activated_by: str) -> SystemState:
        """Activate safe mode (pauses autonomous operations)."""
        state = await self.get_system_state()
        state.safe_mode_active = True
        state.safe_mode_activated_at = datetime.now(timezone.utc)
        state.safe_mode_activated_by = activated_by
        state.last_updated = datetime.now(timezone.utc)

        await self._persist_system_state(state)
        await self._log_event(
            "system", AuditEventType.SAFE_MODE_ACTIVATED,
            f"Safe mode activated by {activated_by}",
            severity=AuditSeverity.WARNING
        )
        return state

    async def deactivate_safe_mode(self, deactivated_by: str) -> SystemState:
        """Deactivate safe mode."""
        state = await self.get_system_state()
        state.safe_mode_active = False
        state.safe_mode_activated_at = None
        state.safe_mode_activated_by = None
        state.last_updated = datetime.now(timezone.utc)

        await self._persist_system_state(state)
        await self._log_event(
            "system", AuditEventType.SAFE_MODE_DEACTIVATED,
            f"Safe mode deactivated by {deactivated_by}"
        )
        return state

    async def activate_kill_switch(self, activated_by: str) -> SystemState:
        """Activate kill switch (stops ALL operations)."""
        state = await self.get_system_state()
        state.kill_switch_active = True
        state.kill_switch_activated_at = datetime.now(timezone.utc)
        state.kill_switch_activated_by = activated_by
        state.safe_mode_active = True
        state.safe_mode_activated_at = datetime.now(timezone.utc)
        state.safe_mode_activated_by = activated_by
        state.last_updated = datetime.now(timezone.utc)

        await self._persist_system_state(state)
        await self._log_event(
            "system", AuditEventType.KILL_SWITCH_ACTIVATED,
            f"KILL SWITCH activated by {activated_by}",
            severity=AuditSeverity.CRITICAL
        )
        return state

    async def deactivate_kill_switch(self, deactivated_by: str) -> SystemState:
        """Deactivate kill switch."""
        state = await self.get_system_state()
        state.kill_switch_active = False
        state.kill_switch_activated_at = None
        state.kill_switch_activated_by = None
        state.last_updated = datetime.now(timezone.utc)

        await self._persist_system_state(state)
        return state

    # --- helpers ---

    def _hash_password(self, password: str) -> str:
        """Hash a password with salt."""
        salt = secrets.token_hex(16)
        hashed = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
        return f"{salt}:{hashed}"

    def _verify_password(self, password: str, stored_hash: str) -> bool:
        """Verify password against stored hash."""
        if ":" not in stored_hash:
            return False
        salt, expected = stored_hash.split(":", 1)
        actual = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
        return secrets.compare_digest(actual, expected)

    def _create_token(self, user: User) -> AuthToken:
        """Create an access token for a user."""
        token_str = secrets.token_urlsafe(48)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRY_HOURS)
        permissions = list(user.get_all_permissions())

        token = AuthToken(
            access_token=token_str,
            expires_at=expires_at,
            user_id=user.id,
            role=user.role,
            permissions=permissions,
        )
        self._tokens[token_str] = token
        return token

    def _doc_to_user(self, doc: dict) -> User:
        """Convert MongoDB document to User model."""
        for f in ["created_at", "updated_at", "last_login"]:
            if doc.get(f) and isinstance(doc[f], str):
                try:
                    doc[f] = datetime.fromisoformat(doc[f])
                except (ValueError, TypeError):
                    pass
        doc.pop("password_hash", None)
        return User(**doc)

    async def _persist_system_state(self, state: SystemState):
        """Persist system state to MongoDB."""
        self._system_state = state
        doc = state.model_dump()
        for f in ["safe_mode_activated_at", "kill_switch_activated_at", "last_updated"]:
            if doc.get(f) and isinstance(doc[f], datetime):
                doc[f] = doc[f].isoformat()
        doc["_key"] = "system"
        await self.system_collection.replace_one({"_key": "system"}, doc, upsert=True)

    async def _log_event(
        self, correlation_id, event_type, message,
        severity=AuditSeverity.INFO
    ):
        """Log audit event if logger available."""
        if not self.audit_logger:
            return
        try:
            from models.audit import AuditEvent
            await self.audit_logger.log_event(AuditEvent(
                correlation_id=correlation_id,
                event_type=event_type,
                severity=severity,
                message=message,
            ))
        except Exception as e:
            logger.warning(f"Audit log failed: {e}")

    async def ensure_admin_exists(self):
        """Ensure at least one admin user exists."""
        admin = await self.users_collection.find_one({"role": "admin"})
        if not admin:
            default_password = os.environ.get("ADMIN_PASSWORD", "admin")
            await self.create_user(UserCreate(
                username="admin",
                email="admin@moatbot.local",
                role=UserRole.ADMIN,
                password=default_password,
            ))
            logger.info("Default admin user created (username: admin)")
