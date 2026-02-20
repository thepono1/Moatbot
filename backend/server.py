"""MoatBot FastAPI server — all API routes + static SPA serving."""
from fastapi import FastAPI, APIRouter, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import sys
import logging
from pathlib import Path
from typing import List, Optional
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Paths & env
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).parent
PROJECT_ROOT = ROOT_DIR.parent
load_dotenv(ROOT_DIR / ".env")

# MongoDB (graceful fallback so the container can at least start)
mongo_url = os.environ.get("MONGO_URL", "")
db_name = os.environ.get("DB_NAME", "moatbot")

if mongo_url:
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
else:
    client = None
    db = None
    logging.warning("MONGO_URL not set — running without database")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ensure backend package is importable (when run via `uvicorn backend.server:app`)
# ---------------------------------------------------------------------------
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# ---------------------------------------------------------------------------
# Import models
# ---------------------------------------------------------------------------
from models.agent import Agent, AgentCreate, AgentUpdate, AgentStatus
from models.audit import AuditEventCreate, AuditQuery
from models.orchestration import MentorCommand, SolomonCommand, OrchestrationPhase
from models.rbac import UserCreate, LoginRequest, SystemState

# ---------------------------------------------------------------------------
# Import services
# ---------------------------------------------------------------------------
from services.agent_registry import AgentRegistryService
from services.audit_logger import AuditLoggerService
from services.llm_router import LLMRouterService
from services.git_grounding import GitGroundingService
from services.orchestrator import OrchestratorService
from services.rbac import RBACService

# ---------------------------------------------------------------------------
# Instantiate services
# ---------------------------------------------------------------------------
audit_logger = AuditLoggerService(db=db)
git_grounding = GitGroundingService(workspace_root=PROJECT_ROOT)

agent_registry = AgentRegistryService(db) if db else None
llm_router = LLMRouterService(audit_logger=audit_logger)
orchestrator = OrchestratorService(db, audit_logger=audit_logger, git_grounding=git_grounding) if db else None
rbac_service = RBACService(db, audit_logger=audit_logger) if db else None

# ---------------------------------------------------------------------------
# App & router
# ---------------------------------------------------------------------------
app = FastAPI(title="MoatBot API", version="1.0.0")
api = APIRouter(prefix="/api")

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===========================================================================
# Health / root
# ===========================================================================

@api.get("/")
async def root():
    return {"message": "MoatBot API running", "status": "ok"}


@api.get("/health")
async def health():
    return {
        "status": "healthy",
        "database": "connected" if db is not None else "disconnected",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ===========================================================================
# Agents
# ===========================================================================

@api.get("/agents")
async def list_agents():
    if not agent_registry:
        raise HTTPException(503, "Database not configured")
    agents = await agent_registry.list_agents()
    return [a.model_dump() for a in agents]


@api.post("/agents")
async def create_agent(data: AgentCreate):
    if not agent_registry:
        raise HTTPException(503, "Database not configured")
    agent = await agent_registry.create_agent(data)
    return agent.model_dump()


@api.get("/agents/{agent_id}")
async def get_agent(agent_id: str):
    if not agent_registry:
        raise HTTPException(503, "Database not configured")
    agent = await agent_registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    return agent.model_dump()


@api.put("/agents/{agent_id}")
async def update_agent(agent_id: str, data: AgentUpdate):
    if not agent_registry:
        raise HTTPException(503, "Database not configured")
    agent = await agent_registry.update_agent(agent_id, data)
    if not agent:
        raise HTTPException(404, "Agent not found")
    return agent.model_dump()


@api.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str):
    if not agent_registry:
        raise HTTPException(503, "Database not configured")
    deleted = await agent_registry.delete_agent(agent_id)
    if not deleted:
        raise HTTPException(404, "Agent not found")
    return {"deleted": True}


@api.post("/agents/validate-tokens")
async def validate_tokens():
    if not agent_registry:
        raise HTTPException(503, "Database not configured")
    return await agent_registry.validate_agent_tokens()


@api.get("/agents/missing-tokens")
async def missing_tokens():
    if not agent_registry:
        raise HTTPException(503, "Database not configured")
    return await agent_registry.get_missing_tokens()


# ===========================================================================
# Audit
# ===========================================================================

@api.post("/audit/events")
async def create_audit_event(data: AuditEventCreate):
    event = await audit_logger.create_event(data)
    return event.model_dump()


@api.post("/audit/query")
async def query_audit(query: AuditQuery):
    events = await audit_logger.query_events(query)
    return [e.model_dump() for e in events]


@api.get("/audit/recent")
async def recent_audit(limit: int = 50):
    events = await audit_logger.get_recent_events(limit)
    return [e.model_dump() for e in events]


@api.get("/audit/stats")
async def audit_stats():
    return await audit_logger.get_event_stats()


@api.get("/audit/timeline/{correlation_id}")
async def audit_timeline(correlation_id: str):
    timeline = await audit_logger.get_timeline(correlation_id)
    if not timeline:
        raise HTTPException(404, "Timeline not found")
    return timeline.model_dump()


# ===========================================================================
# LLM Router
# ===========================================================================

@api.get("/llm/providers")
async def llm_providers():
    return llm_router.get_provider_status()


@api.get("/llm/health")
async def llm_health():
    results = await llm_router.health_check()
    return [r.model_dump() for r in results]


# ===========================================================================
# Git Grounding
# ===========================================================================

@api.get("/git/state")
async def git_state():
    try:
        return git_grounding.to_dict()
    except Exception as e:
        return {"error": str(e)}


@api.get("/git/commits")
async def git_commits(limit: int = 20):
    return git_grounding.get_commit_history(limit)


@api.post("/git/snapshot")
async def git_snapshot(name: Optional[str] = None):
    snapshot = git_grounding.create_snapshot(name)
    return {
        "id": snapshot.id,
        "commit_sha": snapshot.commit_sha,
        "branch": snapshot.branch,
        "total_files": snapshot.total_files,
        "timestamp": snapshot.timestamp.isoformat(),
    }


@api.get("/git/verify/{expected_sha}")
async def git_verify(expected_sha: str):
    return git_grounding.verify_grounding(expected_sha)


# ===========================================================================
# Orchestration
# ===========================================================================

@api.post("/orchestration/plan")
async def create_plan(command: MentorCommand):
    if not orchestrator:
        raise HTTPException(503, "Database not configured")
    state = await orchestrator.create_plan(command)
    return state.model_dump()


@api.post("/orchestration/{correlation_id}/approve")
async def approve_plan(correlation_id: str, approved_by: str = "admin"):
    if not orchestrator:
        raise HTTPException(503, "Database not configured")
    state = await orchestrator.approve_plan(correlation_id, approved_by)
    if not state:
        raise HTTPException(404, "Orchestration not found")
    return state.model_dump()


@api.post("/orchestration/{correlation_id}/reject")
async def reject_plan(correlation_id: str, reason: str = ""):
    if not orchestrator:
        raise HTTPException(503, "Database not configured")
    state = await orchestrator.reject_plan(correlation_id, reason)
    if not state:
        raise HTTPException(404, "Orchestration not found")
    return state.model_dump()


@api.post("/orchestration/{correlation_id}/execute")
async def start_execution(correlation_id: str):
    if not orchestrator:
        raise HTTPException(503, "Database not configured")
    state = await orchestrator.start_execution(correlation_id)
    if not state:
        raise HTTPException(404, "Orchestration not found")
    return state.model_dump()


@api.get("/orchestration")
async def list_orchestrations(phase: Optional[str] = None, limit: int = 50):
    if not orchestrator:
        raise HTTPException(503, "Database not configured")
    p = OrchestrationPhase(phase) if phase else None
    states = await orchestrator.list_states(p, limit)
    return [s.model_dump() for s in states]


@api.get("/orchestration/{correlation_id}")
async def get_orchestration(correlation_id: str):
    if not orchestrator:
        raise HTTPException(503, "Database not configured")
    state = await orchestrator.get_state(correlation_id)
    if not state:
        raise HTTPException(404, "Orchestration not found")
    return state.model_dump()


@api.post("/orchestration/{correlation_id}/cancel")
async def cancel_orchestration(correlation_id: str, reason: str = ""):
    if not orchestrator:
        raise HTTPException(503, "Database not configured")
    state = await orchestrator.cancel(correlation_id, reason)
    if not state:
        raise HTTPException(404, "Orchestration not found")
    return state.model_dump()


# ===========================================================================
# RBAC / System
# ===========================================================================

@api.post("/auth/login")
async def login(request: LoginRequest):
    if not rbac_service:
        raise HTTPException(503, "Database not configured")
    token = await rbac_service.login(request)
    if not token:
        raise HTTPException(401, "Invalid credentials")
    return token.model_dump()


@api.get("/users")
async def list_users():
    if not rbac_service:
        raise HTTPException(503, "Database not configured")
    users = await rbac_service.list_users()
    return [u.model_dump() for u in users]


@api.post("/users")
async def create_user(data: UserCreate):
    if not rbac_service:
        raise HTTPException(503, "Database not configured")
    user = await rbac_service.create_user(data)
    return user.model_dump()


@api.get("/system/state")
async def system_state():
    if not rbac_service:
        return SystemState().model_dump()
    state = await rbac_service.get_system_state()
    return state.model_dump()


@api.post("/system/safe-mode/activate")
async def activate_safe_mode(activated_by: str = "admin"):
    if not rbac_service:
        raise HTTPException(503, "Database not configured")
    state = await rbac_service.activate_safe_mode(activated_by)
    return state.model_dump()


@api.post("/system/safe-mode/deactivate")
async def deactivate_safe_mode(deactivated_by: str = "admin"):
    if not rbac_service:
        raise HTTPException(503, "Database not configured")
    state = await rbac_service.deactivate_safe_mode(deactivated_by)
    return state.model_dump()


@api.post("/system/kill-switch/activate")
async def activate_kill_switch(activated_by: str = "admin"):
    if not rbac_service:
        raise HTTPException(503, "Database not configured")
    state = await rbac_service.activate_kill_switch(activated_by)
    return state.model_dump()


@api.post("/system/kill-switch/deactivate")
async def deactivate_kill_switch(deactivated_by: str = "admin"):
    if not rbac_service:
        raise HTTPException(503, "Database not configured")
    state = await rbac_service.deactivate_kill_switch(deactivated_by)
    return state.model_dump()


# ===========================================================================
# Register API router
# ===========================================================================

app.include_router(api)


# ===========================================================================
# Startup / shutdown events
# ===========================================================================

@app.on_event("startup")
async def startup():
    logger.info("MoatBot starting up…")
    if agent_registry:
        await agent_registry.initialize_default_agents()
        logger.info("Default agents initialized")
    if rbac_service:
        await rbac_service.ensure_admin_exists()
        logger.info("Admin user ensured")


@app.on_event("shutdown")
async def shutdown():
    if client:
        client.close()
    logger.info("MoatBot shut down")


# ===========================================================================
# Static SPA serving (React build) — must come AFTER API routes
# ===========================================================================

FRONTEND_BUILD = PROJECT_ROOT / "frontend" / "build"

if FRONTEND_BUILD.exists():
    # Serve static assets (js, css, images, etc.)
    app.mount("/static", StaticFiles(directory=FRONTEND_BUILD / "static"), name="static")

    # Catch-all: serve index.html for any non-API route (SPA client-side routing)
    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        # If the path points to a real file in the build dir, serve it
        file_path = FRONTEND_BUILD / full_path
        if full_path and file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        # Otherwise serve index.html for client-side routing
        return FileResponse(FRONTEND_BUILD / "index.html")
else:
    @app.get("/")
    async def root_fallback():
        return {
            "message": "MoatBot API is running. Frontend build not found.",
            "api_docs": "/docs",
        }
