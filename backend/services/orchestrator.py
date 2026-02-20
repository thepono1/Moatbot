"""Orchestrator Service for Plan -> Execute -> Verify lifecycle."""
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
import uuid

from models.orchestration import (
    Plan, PlanStep, Execution, Verification, OrchestrationState,
    OrchestrationPhase, AcceptanceCriterion, MentorCommand, SolomonCommand
)
from models.audit import AuditEventType, AuditSeverity

logger = logging.getLogger(__name__)


class OrchestratorService:
    """Service for managing Plan -> Execute -> Verify lifecycle."""

    def __init__(self, db, audit_logger=None, git_grounding=None):
        self.db = db
        self.collection = db.orchestrations
        self.audit_logger = audit_logger
        self.git_grounding = git_grounding
        self._active_states: Dict[str, OrchestrationState] = {}

    async def create_plan(self, command: MentorCommand) -> OrchestrationState:
        """Create a new orchestration plan from a mentor command."""
        correlation_id = str(uuid.uuid4())

        # Get git grounding
        git_sha = "unknown"
        git_branch = "unknown"
        if self.git_grounding:
            try:
                state = self.git_grounding.get_current_state()
                git_sha = state.commit_sha
                git_branch = state.branch
            except Exception as e:
                logger.warning(f"Git grounding unavailable: {e}")

        plan = Plan(
            correlation_id=correlation_id,
            name=f"Plan: {command.task[:60]}",
            description=command.task,
            created_by_agent=command.agent_id or "system",
            git_commit_sha=git_sha,
            git_branch=git_branch,
        )

        orch_state = OrchestrationState(
            correlation_id=correlation_id,
            phase=OrchestrationPhase.PLANNING,
            plan=plan,
            planner_agent_id=command.agent_id,
            trigger_data=command.context,
        )

        # Persist
        doc = orch_state.model_dump()
        self._serialize_datetimes(doc)
        await self.collection.insert_one(doc)
        self._active_states[correlation_id] = orch_state

        await self._log_event(
            correlation_id, AuditEventType.PLAN_CREATED,
            f"Plan created: {plan.name}"
        )

        return orch_state

    async def approve_plan(self, correlation_id: str, approved_by: str) -> Optional[OrchestrationState]:
        """Approve a plan for execution."""
        orch = await self.get_state(correlation_id)
        if not orch or not orch.plan:
            return None

        orch.plan.approved = True
        orch.plan.approved_by = approved_by
        orch.plan.approved_at = datetime.now(timezone.utc)
        orch.phase = OrchestrationPhase.AWAITING_APPROVAL

        await self._update_state(orch)
        await self._log_event(
            correlation_id, AuditEventType.PLAN_APPROVED,
            f"Plan approved by {approved_by}"
        )
        return orch

    async def reject_plan(self, correlation_id: str, reason: str) -> Optional[OrchestrationState]:
        """Reject a plan."""
        orch = await self.get_state(correlation_id)
        if not orch or not orch.plan:
            return None

        orch.plan.approved = False
        orch.plan.rejection_reason = reason
        orch.phase = OrchestrationPhase.CANCELLED

        await self._update_state(orch)
        await self._log_event(
            correlation_id, AuditEventType.PLAN_REJECTED,
            f"Plan rejected: {reason}"
        )
        return orch

    async def start_execution(self, correlation_id: str) -> Optional[OrchestrationState]:
        """Start executing an approved plan."""
        orch = await self.get_state(correlation_id)
        if not orch or not orch.plan:
            return None

        execution = Execution(
            plan_id=orch.plan.id,
            correlation_id=correlation_id,
            status="running",
            started_at=datetime.now(timezone.utc),
        )

        orch.execution = execution
        orch.phase = OrchestrationPhase.EXECUTING

        await self._update_state(orch)
        await self._log_event(
            correlation_id, AuditEventType.EXECUTION_STARTED,
            "Execution started"
        )
        return orch

    async def complete_execution(
        self, correlation_id: str, outputs: Dict[str, Any] = None
    ) -> Optional[OrchestrationState]:
        """Mark execution as completed."""
        orch = await self.get_state(correlation_id)
        if not orch or not orch.execution:
            return None

        orch.execution.status = "completed"
        orch.execution.completed_at = datetime.now(timezone.utc)
        if outputs:
            orch.execution.outputs = outputs
        orch.phase = OrchestrationPhase.VERIFYING

        await self._update_state(orch)
        await self._log_event(
            correlation_id, AuditEventType.EXECUTION_COMPLETED,
            "Execution completed"
        )
        return orch

    async def fail_execution(
        self, correlation_id: str, error: str
    ) -> Optional[OrchestrationState]:
        """Mark execution as failed."""
        orch = await self.get_state(correlation_id)
        if not orch or not orch.execution:
            return None

        orch.execution.status = "failed"
        orch.execution.completed_at = datetime.now(timezone.utc)
        orch.execution.errors.append({
            "error": error,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        orch.phase = OrchestrationPhase.FAILED

        await self._update_state(orch)
        await self._log_event(
            correlation_id, AuditEventType.EXECUTION_FAILED,
            f"Execution failed: {error}",
            severity=AuditSeverity.ERROR
        )
        return orch

    async def start_verification(
        self, command: SolomonCommand
    ) -> Optional[OrchestrationState]:
        """Start verification of an execution."""
        # Find orchestration by execution_id
        orch = await self._find_by_execution(command.execution_id)
        if not orch or not orch.plan:
            return None

        verification = Verification(
            execution_id=command.execution_id,
            plan_id=orch.plan.id,
            correlation_id=orch.correlation_id,
            status="running",
            total_count=len(orch.plan.acceptance_criteria),
            started_at=datetime.now(timezone.utc),
        )

        orch.verification = verification
        orch.phase = OrchestrationPhase.VERIFYING
        orch.verifier_agent_id = "solomon"

        await self._update_state(orch)
        await self._log_event(
            orch.correlation_id, AuditEventType.VERIFICATION_STARTED,
            f"Verification started (type: {command.verification_type})"
        )
        return orch

    async def complete_verification(
        self, correlation_id: str, passed: bool, summary: str = ""
    ) -> Optional[OrchestrationState]:
        """Complete verification."""
        orch = await self.get_state(correlation_id)
        if not orch or not orch.verification:
            return None

        orch.verification.status = "passed" if passed else "failed"
        orch.verification.summary = summary
        orch.verification.completed_at = datetime.now(timezone.utc)

        orch.phase = OrchestrationPhase.COMPLETED if passed else OrchestrationPhase.FAILED
        orch.completed_at = datetime.now(timezone.utc)

        await self._update_state(orch)

        event_type = (AuditEventType.VERIFICATION_PASSED if passed
                      else AuditEventType.VERIFICATION_FAILED)
        await self._log_event(
            correlation_id, event_type,
            f"Verification {'passed' if passed else 'failed'}: {summary}"
        )
        return orch

    async def get_state(self, correlation_id: str) -> Optional[OrchestrationState]:
        """Get orchestration state by correlation ID."""
        if correlation_id in self._active_states:
            return self._active_states[correlation_id]

        doc = await self.collection.find_one(
            {"correlation_id": correlation_id}, {"_id": 0}
        )
        if doc:
            self._deserialize_datetimes(doc)
            orch = OrchestrationState(**doc)
            self._active_states[correlation_id] = orch
            return orch
        return None

    async def list_states(
        self, phase: Optional[OrchestrationPhase] = None, limit: int = 50
    ) -> List[OrchestrationState]:
        """List orchestration states."""
        query = {}
        if phase:
            query["phase"] = phase
        docs = await self.collection.find(query, {"_id": 0}).sort(
            "created_at", -1
        ).to_list(limit)

        results = []
        for doc in docs:
            self._deserialize_datetimes(doc)
            results.append(OrchestrationState(**doc))
        return results

    async def cancel(self, correlation_id: str, reason: str = "") -> Optional[OrchestrationState]:
        """Cancel an orchestration."""
        orch = await self.get_state(correlation_id)
        if not orch:
            return None

        orch.phase = OrchestrationPhase.CANCELLED
        orch.completed_at = datetime.now(timezone.utc)

        if orch.execution and orch.execution.status == "running":
            orch.execution.status = "cancelled"
            orch.execution.completed_at = datetime.now(timezone.utc)

        await self._update_state(orch)
        return orch

    # --- helpers ---

    async def _find_by_execution(self, execution_id: str) -> Optional[OrchestrationState]:
        """Find orchestration by execution ID."""
        doc = await self.collection.find_one(
            {"execution.id": execution_id}, {"_id": 0}
        )
        if doc:
            self._deserialize_datetimes(doc)
            return OrchestrationState(**doc)
        return None

    async def _update_state(self, orch: OrchestrationState):
        """Persist updated state."""
        orch.updated_at = datetime.now(timezone.utc)
        doc = orch.model_dump()
        self._serialize_datetimes(doc)
        await self.collection.replace_one(
            {"correlation_id": orch.correlation_id}, doc, upsert=True
        )
        self._active_states[orch.correlation_id] = orch

    async def _log_event(
        self, correlation_id: str, event_type, message: str,
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

    def _serialize_datetimes(self, obj):
        """Recursively convert datetimes to ISO strings for MongoDB."""
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, datetime):
                    obj[k] = v.isoformat()
                elif isinstance(v, dict):
                    self._serialize_datetimes(v)
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict):
                            self._serialize_datetimes(item)

    def _deserialize_datetimes(self, obj):
        """Best-effort datetime parsing for known fields."""
        dt_fields = [
            "created_at", "updated_at", "completed_at", "started_at",
            "approved_at", "verified_at", "timestamp",
            "safe_mode_activated_at", "kill_switch_activated_at",
            "last_health_check",
        ]
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in dt_fields and isinstance(v, str):
                    try:
                        obj[k] = datetime.fromisoformat(v)
                    except (ValueError, TypeError):
                        pass
                elif isinstance(v, dict):
                    self._deserialize_datetimes(v)
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict):
                            self._deserialize_datetimes(item)
