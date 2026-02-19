"""Agent Registry Service for managing Discord bot agents."""
import os
import logging
from typing import Dict, List, Optional
from datetime import datetime, timezone

from models.agent import (
    Agent, AgentCreate, AgentUpdate, AgentConfig, AgentStatus,
    DEFAULT_AGENTS
)

logger = logging.getLogger(__name__)


class AgentRegistryService:
    """Service for managing agent registry."""
    
    def __init__(self, db):
        self.db = db
        self.collection = db.agents
        self._agents_cache: Dict[str, Agent] = {}
        self._token_validation_cache: Dict[str, bool] = {}
    
    async def initialize_default_agents(self):
        """Initialize default agents if not present."""
        for name, config in DEFAULT_AGENTS.items():
            existing = await self.get_agent_by_name(name)
            if not existing:
                agent = Agent(
                    name=name.capitalize(),
                    description=f"Default {name} agent",
                    role=self._get_default_role(name),
                    config=config
                )
                await self.create_agent_internal(agent)
                logger.info(f"Created default agent: {name}")
    
    def _get_default_role(self, name: str) -> str:
        """Get default role for agent."""
        roles = {
            "kaleo": "mentor",
            "solomon": "verifier",
            "deborah": "orchestrator"
        }
        return roles.get(name.lower(), "assistant")
    
    async def create_agent_internal(self, agent: Agent) -> Agent:
        """Create agent from internal Agent object."""
        doc = agent.model_dump()
        doc['created_at'] = doc['created_at'].isoformat()
        doc['updated_at'] = doc['updated_at'].isoformat()
        if doc.get('last_health_check'):
            doc['last_health_check'] = doc['last_health_check'].isoformat()
        
        await self.collection.insert_one(doc)
        self._agents_cache[agent.id] = agent
        return agent
    
    async def create_agent(self, data: AgentCreate) -> Agent:
        """Create a new agent."""
        agent = Agent(
            name=data.name,
            description=data.description,
            role=data.role,
            config=data.config
        )
        return await self.create_agent_internal(agent)
    
    async def get_agent(self, agent_id: str) -> Optional[Agent]:
        """Get agent by ID."""
        if agent_id in self._agents_cache:
            return self._agents_cache[agent_id]
        
        doc = await self.collection.find_one({"id": agent_id}, {"_id": 0})
        if doc:
            agent = self._doc_to_agent(doc)
            self._agents_cache[agent_id] = agent
            return agent
        return None
    
    async def get_agent_by_name(self, name: str) -> Optional[Agent]:
        """Get agent by name."""
        doc = await self.collection.find_one(
            {"name": {"$regex": f"^{name}$", "$options": "i"}},
            {"_id": 0}
        )
        if doc:
            return self._doc_to_agent(doc)
        return None
    
    async def list_agents(self) -> List[Agent]:
        """List all agents."""
        docs = await self.collection.find({}, {"_id": 0}).to_list(100)
        return [self._doc_to_agent(doc) for doc in docs]
    
    async def update_agent(self, agent_id: str, data: AgentUpdate) -> Optional[Agent]:
        """Update an agent."""
        update_data = {k: v for k, v in data.model_dump().items() if v is not None}
        if not update_data:
            return await self.get_agent(agent_id)
        
        update_data['updated_at'] = datetime.now(timezone.utc).isoformat()
        
        # Handle nested config update
        if 'config' in update_data:
            update_data['config'] = update_data['config'].model_dump()
        
        await self.collection.update_one(
            {"id": agent_id},
            {"$set": update_data}
        )
        
        # Invalidate cache
        self._agents_cache.pop(agent_id, None)
        return await self.get_agent(agent_id)
    
    async def delete_agent(self, agent_id: str) -> bool:
        """Delete an agent."""
        result = await self.collection.delete_one({"id": agent_id})
        self._agents_cache.pop(agent_id, None)
        return result.deleted_count > 0
    
    async def validate_agent_tokens(self) -> Dict[str, Dict]:
        """Validate all agent tokens from environment."""
        results = {}
        agents = await self.list_agents()
        
        for agent in agents:
            env_var = agent.config.discord_token_env_var
            token = os.environ.get(env_var)
            
            validation = {
                "agent_id": agent.id,
                "agent_name": agent.name,
                "env_var": env_var,
                "token_present": token is not None and len(token) > 0,
                "token_length": len(token) if token else 0,
                "validated_at": datetime.now(timezone.utc).isoformat()
            }
            
            # Update agent status based on token validation
            if validation["token_present"]:
                agent.token_validated = True
                agent.status = AgentStatus.ACTIVE
            else:
                agent.token_validated = False
                agent.status = AgentStatus.INACTIVE
                agent.error_message = f"Missing environment variable: {env_var}"
            
            await self.collection.update_one(
                {"id": agent.id},
                {"$set": {
                    "token_validated": agent.token_validated,
                    "status": agent.status,
                    "error_message": agent.error_message,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }}
            )
            
            results[agent.name] = validation
            self._token_validation_cache[agent.id] = validation["token_present"]
        
        return results
    
    async def get_missing_tokens(self) -> List[Dict]:
        """Get list of agents with missing tokens."""
        agents = await self.list_agents()
        missing = []
        
        for agent in agents:
            env_var = agent.config.discord_token_env_var
            if not os.environ.get(env_var):
                missing.append({
                    "agent_name": agent.name,
                    "env_var": env_var,
                    "required": True
                })
        
        return missing
    
    async def set_agent_status(self, agent_id: str, status: AgentStatus, error: Optional[str] = None):
        """Set agent status."""
        update = {
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        if error:
            update["error_message"] = error
        elif status == AgentStatus.ACTIVE:
            update["error_message"] = None
        
        await self.collection.update_one({"id": agent_id}, {"$set": update})
        self._agents_cache.pop(agent_id, None)
    
    async def increment_metrics(self, agent_id: str, messages: int = 0, llm_calls: int = 0, errors: int = 0):
        """Increment agent metrics."""
        await self.collection.update_one(
            {"id": agent_id},
            {"$inc": {
                "messages_processed": messages,
                "llm_calls_made": llm_calls,
                "errors_count": errors
            }}
        )
        self._agents_cache.pop(agent_id, None)
    
    def _doc_to_agent(self, doc: dict) -> Agent:
        """Convert MongoDB document to Agent model."""
        # Parse datetime fields
        for field in ['created_at', 'updated_at', 'last_health_check']:
            if doc.get(field) and isinstance(doc[field], str):
                doc[field] = datetime.fromisoformat(doc[field])
        
        # Parse config
        if isinstance(doc.get('config'), dict):
            doc['config'] = AgentConfig(**doc['config'])
        
        return Agent(**doc)
