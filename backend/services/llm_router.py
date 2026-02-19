"""LLM Router Service with multi-provider support, failover, and circuit breakers."""
import os
import asyncio
import logging
import time
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone, timedelta

from emergentintegrations.llm.chat import LlmChat, UserMessage

from models.llm import (
    LLMProvider, LLMRequest, LLMResponse, ProviderHealth,
    ProviderName, ProviderStatus, CircuitState, RouterConfig,
    DEFAULT_PROVIDERS
)
from models.audit import AuditEvent, AuditEventType, AuditSeverity

logger = logging.getLogger(__name__)


class LLMRouterService:
    """Service for routing LLM requests with failover and circuit breakers."""
    
    def __init__(self, audit_logger=None):
        self.audit_logger = audit_logger
        self.config = RouterConfig()
        self.providers: Dict[ProviderName, LLMProvider] = {}
        self._initialize_providers()
        self._rate_limit_window: Dict[ProviderName, List[datetime]] = {}
    
    def _initialize_providers(self):
        """Initialize providers from defaults."""
        for name, provider in DEFAULT_PROVIDERS.items():
            # Check if provider should be enabled
            if name == ProviderName.ANTHROPIC:
                # Anthropic disabled by default
                provider.enabled = os.environ.get('ENABLE_ANTHROPIC', 'false').lower() == 'true'
            
            self.providers[name] = provider
            self._rate_limit_window[name] = []
    
    def _get_api_key(self) -> str:
        """Get the Emergent LLM key."""
        key = os.environ.get('EMERGENT_LLM_KEY')
        if not key:
            raise ValueError("EMERGENT_LLM_KEY not found in environment")
        return key
    
    async def route_request(self, request: LLMRequest) -> LLMResponse:
        """Route an LLM request to the best available provider."""
        start_time = time.time()
        
        # Get ordered list of providers to try
        providers_to_try = self._get_providers_for_request(request)
        
        if not providers_to_try:
            raise Exception("No available LLM providers")
        
        last_error = None
        used_fallback = False
        
        for i, (provider, model) in enumerate(providers_to_try):
            if i > 0:
                used_fallback = True
            
            try:
                # Check circuit breaker
                if not self._can_use_provider(provider.name):
                    logger.warning(f"Circuit open for {provider.name}, skipping")
                    continue
                
                # Check rate limit
                if not self._check_rate_limit(provider.name):
                    logger.warning(f"Rate limit hit for {provider.name}, skipping")
                    continue
                
                # Make the request
                response = await self._make_llm_request(
                    provider, model, request, used_fallback
                )
                
                # Record success
                await self._record_success(provider.name)
                
                # Calculate latency
                latency_ms = (time.time() - start_time) * 1000
                response.latency_ms = latency_ms
                
                # Log to audit
                if self.audit_logger:
                    await self._log_llm_event(
                        request, response, provider.name, model,
                        used_fallback, latency_ms
                    )
                
                return response
                
            except Exception as e:
                last_error = e
                logger.error(f"LLM request failed for {provider.name}: {e}")
                await self._record_failure(provider.name, str(e))
                
                # Log error to audit
                if self.audit_logger:
                    await self._log_llm_error(request, provider.name, str(e))
        
        # All providers failed
        raise Exception(f"All LLM providers failed. Last error: {last_error}")
    
    def _get_providers_for_request(self, request: LLMRequest) -> List[Tuple[LLMProvider, str]]:
        """Get ordered list of (provider, model) tuples to try."""
        result = []
        
        # First, try preferred provider if specified
        if request.preferred_provider:
            provider = self.providers.get(request.preferred_provider)
            if provider and provider.enabled:
                model = request.preferred_model or provider.default_model
                result.append((provider, model))
        
        # Then add fallback providers by priority
        if request.allow_fallback:
            sorted_providers = sorted(
                self.providers.values(),
                key=lambda p: (self.config.provider_priorities.get(p.name, 99), p.name.value)
            )
            
            for provider in sorted_providers:
                if not provider.enabled:
                    continue
                
                # Skip if already added as preferred
                if request.preferred_provider and provider.name == request.preferred_provider:
                    continue
                
                result.append((provider, provider.default_model))
        
        return result
    
    def _can_use_provider(self, provider_name: ProviderName) -> bool:
        """Check if provider can be used (circuit breaker check)."""
        provider = self.providers.get(provider_name)
        if not provider:
            return False
        
        if provider.circuit_state == CircuitState.OPEN:
            # Check if recovery timeout has passed
            if provider.circuit_opened_at:
                recovery_time = provider.circuit_opened_at + timedelta(
                    seconds=self.config.circuit_recovery_timeout_seconds
                )
                if datetime.now(timezone.utc) >= recovery_time:
                    # Move to half-open
                    provider.circuit_state = CircuitState.HALF_OPEN
                    provider.circuit_half_open_at = datetime.now(timezone.utc)
                    return True
            return False
        
        return True
    
    def _check_rate_limit(self, provider_name: ProviderName) -> bool:
        """Check if rate limit allows request."""
        provider = self.providers.get(provider_name)
        if not provider:
            return False
        
        # Clean old entries (older than 1 minute)
        now = datetime.now(timezone.utc)
        one_minute_ago = now - timedelta(minutes=1)
        self._rate_limit_window[provider_name] = [
            t for t in self._rate_limit_window[provider_name]
            if t > one_minute_ago
        ]
        
        # Check limit
        if len(self._rate_limit_window[provider_name]) >= provider.rate_limit_per_minute:
            return False
        
        # Add current request
        self._rate_limit_window[provider_name].append(now)
        return True
    
    async def _make_llm_request(
        self, provider: LLMProvider, model: str,
        request: LLMRequest, used_fallback: bool
    ) -> LLMResponse:
        """Make actual LLM request using emergentintegrations."""
        api_key = self._get_api_key()
        
        # Build system message
        system_message = request.system_prompt or "You are a helpful assistant."
        
        # Initialize chat
        chat = LlmChat(
            api_key=api_key,
            session_id=request.correlation_id,
            system_message=system_message
        )
        
        # Set model
        chat.with_model(provider.name.value, model)
        
        # Build message content from request messages
        user_text = ""
        for msg in request.messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "user":
                user_text += content + "\n"
            elif role == "assistant":
                # For context, we might include previous assistant responses
                pass
        
        user_message = UserMessage(text=user_text.strip())
        
        # Make request
        response_text = await chat.send_message(user_message)
        
        # Build response
        response = LLMResponse(
            request_id=request.id,
            correlation_id=request.correlation_id,
            provider=provider.name,
            model=model,
            used_fallback=used_fallback,
            content=response_text,
            finish_reason="stop"
        )
        
        return response
    
    async def _record_success(self, provider_name: ProviderName):
        """Record successful request."""
        provider = self.providers.get(provider_name)
        if not provider:
            return
        
        provider.consecutive_failures = 0
        provider.total_requests += 1
        provider.successful_requests += 1
        provider.status = ProviderStatus.HEALTHY
        provider.last_health_check = datetime.now(timezone.utc)
        
        # If in half-open, close the circuit
        if provider.circuit_state == CircuitState.HALF_OPEN:
            provider.circuit_state = CircuitState.CLOSED
            logger.info(f"Circuit closed for {provider_name}")
    
    async def _record_failure(self, provider_name: ProviderName, error: str):
        """Record failed request."""
        provider = self.providers.get(provider_name)
        if not provider:
            return
        
        provider.consecutive_failures += 1
        provider.total_requests += 1
        provider.failed_requests += 1
        provider.last_error = error
        provider.last_health_check = datetime.now(timezone.utc)
        
        # Check if circuit should open
        if provider.consecutive_failures >= self.config.circuit_failure_threshold:
            if provider.circuit_state != CircuitState.OPEN:
                provider.circuit_state = CircuitState.OPEN
                provider.circuit_opened_at = datetime.now(timezone.utc)
                provider.status = ProviderStatus.UNHEALTHY
                logger.warning(f"Circuit opened for {provider_name}")
        else:
            provider.status = ProviderStatus.DEGRADED
    
    async def _log_llm_event(
        self, request: LLMRequest, response: LLMResponse,
        provider: ProviderName, model: str,
        used_fallback: bool, latency_ms: float
    ):
        """Log LLM request/response to audit."""
        # Log request
        await self.audit_logger.log_event(AuditEvent(
            correlation_id=request.correlation_id,
            event_type=AuditEventType.LLM_REQUEST,
            severity=AuditSeverity.INFO,
            agent_id=request.agent_id,
            message=f"LLM request to {provider.value}/{model}",
            data={
                "provider": provider.value,
                "model": model,
                "max_tokens": request.max_tokens,
                "temperature": request.temperature,
                "message_count": len(request.messages)
            }
        ))
        
        # Log response
        await self.audit_logger.log_event(AuditEvent(
            correlation_id=request.correlation_id,
            event_type=AuditEventType.LLM_RESPONSE,
            severity=AuditSeverity.INFO,
            agent_id=request.agent_id,
            message=f"LLM response received ({latency_ms:.0f}ms)",
            data={
                "provider": provider.value,
                "model": model,
                "used_fallback": used_fallback,
                "content_length": len(response.content),
                "finish_reason": response.finish_reason
            },
            duration_ms=latency_ms
        ))
    
    async def _log_llm_error(self, request: LLMRequest, provider: ProviderName, error: str):
        """Log LLM error to audit."""
        await self.audit_logger.log_event(AuditEvent(
            correlation_id=request.correlation_id,
            event_type=AuditEventType.LLM_ERROR,
            severity=AuditSeverity.ERROR,
            agent_id=request.agent_id,
            message=f"LLM error from {provider.value}: {error}",
            error_type="LLMError",
            error_message=error
        ))
    
    async def health_check(self, provider_name: Optional[ProviderName] = None) -> List[ProviderHealth]:
        """Perform health check on providers."""
        results = []
        
        providers_to_check = (
            [self.providers[provider_name]] if provider_name else self.providers.values()
        )
        
        for provider in providers_to_check:
            if not provider.enabled:
                results.append(ProviderHealth(
                    provider=provider.name,
                    status=ProviderStatus.DISABLED,
                    latency_ms=0,
                    error="Provider disabled"
                ))
                continue
            
            start_time = time.time()
            try:
                # Simple health check - just verify we can create a chat instance
                api_key = self._get_api_key()
                chat = LlmChat(
                    api_key=api_key,
                    session_id="health_check",
                    system_message="Health check"
                )
                chat.with_model(provider.name.value, provider.default_model)
                
                latency_ms = (time.time() - start_time) * 1000
                
                results.append(ProviderHealth(
                    provider=provider.name,
                    status=ProviderStatus.HEALTHY,
                    latency_ms=latency_ms
                ))
                
            except Exception as e:
                latency_ms = (time.time() - start_time) * 1000
                results.append(ProviderHealth(
                    provider=provider.name,
                    status=ProviderStatus.UNHEALTHY,
                    latency_ms=latency_ms,
                    error=str(e)
                ))
        
        return results
    
    def get_provider_status(self) -> Dict[str, Dict]:
        """Get status of all providers."""
        return {
            name.value: {
                "enabled": provider.enabled,
                "status": provider.status,
                "circuit_state": provider.circuit_state,
                "consecutive_failures": provider.consecutive_failures,
                "total_requests": provider.total_requests,
                "success_rate": (
                    provider.successful_requests / provider.total_requests * 100
                    if provider.total_requests > 0 else 100
                ),
                "last_error": provider.last_error,
                "models": provider.models,
                "default_model": provider.default_model
            }
            for name, provider in self.providers.items()
        }
    
    def enable_provider(self, provider_name: ProviderName, enabled: bool = True):
        """Enable or disable a provider."""
        if provider_name in self.providers:
            self.providers[provider_name].enabled = enabled
            logger.info(f"Provider {provider_name} {'enabled' if enabled else 'disabled'}")
    
    def reset_circuit(self, provider_name: ProviderName):
        """Manually reset a circuit breaker."""
        if provider_name in self.providers:
            provider = self.providers[provider_name]
            provider.circuit_state = CircuitState.CLOSED
            provider.consecutive_failures = 0
            provider.status = ProviderStatus.HEALTHY
            logger.info(f"Circuit reset for {provider_name}")
