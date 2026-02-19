"""Audit Logger Service for comprehensive logging and traceability."""
import os
import json
import sqlite3
import asyncio
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from contextlib import contextmanager

from models.audit import (
    AuditEvent, AuditEventCreate, AuditEventType, AuditSeverity,
    AuditQuery, AuditTimeline, redact_secrets
)

logger = logging.getLogger(__name__)

# Paths
AUDIT_DIR = Path("/app/backend/data/audit_logs")
AUDIT_INDEX_DB = Path("/app/backend/data/audit_index.db")


class AuditLoggerService:
    """Service for audit logging with JSONL files and SQLite index."""
    
    def __init__(self, db=None):
        self.db = db  # MongoDB for backup/sync
        self.audit_dir = AUDIT_DIR
        self.index_db = AUDIT_INDEX_DB
        self._ensure_directories()
        self._init_sqlite()
    
    def _ensure_directories(self):
        """Ensure audit directories exist."""
        self.audit_dir.mkdir(parents=True, exist_ok=True)
    
    def _init_sqlite(self):
        """Initialize SQLite index database."""
        with self._get_sqlite_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_events (
                    id TEXT PRIMARY KEY,
                    correlation_id TEXT NOT NULL,
                    parent_id TEXT,
                    event_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    agent_id TEXT,
                    agent_name TEXT,
                    user_id TEXT,
                    discord_channel_id TEXT,
                    discord_guild_id TEXT,
                    git_commit_sha TEXT,
                    git_branch TEXT,
                    message TEXT NOT NULL,
                    duration_ms REAL,
                    error_type TEXT,
                    jsonl_file TEXT NOT NULL,
                    jsonl_line INTEGER NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_correlation ON audit_events(correlation_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON audit_events(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_event_type ON audit_events(event_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_agent ON audit_events(agent_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_severity ON audit_events(severity)")
            conn.commit()
    
    @contextmanager
    def _get_sqlite_connection(self):
        """Get SQLite connection."""
        conn = sqlite3.connect(str(self.index_db))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def _get_jsonl_file(self) -> Path:
        """Get current JSONL file (daily rotation)."""
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.audit_dir / f"audit_{date_str}.jsonl"
    
    async def log_event(self, event: AuditEvent) -> AuditEvent:
        """Log an audit event to JSONL and index in SQLite."""
        jsonl_file = self._get_jsonl_file()
        
        # Write to JSONL (append-only)
        jsonl_content = event.to_jsonl(redact=True)
        
        # Get line number before writing
        line_number = 0
        if jsonl_file.exists():
            with open(jsonl_file, 'r') as f:
                line_number = sum(1 for _ in f)
        
        # Append to JSONL file
        with open(jsonl_file, 'a') as f:
            f.write(jsonl_content + '\n')
        
        # Index in SQLite
        with self._get_sqlite_connection() as conn:
            conn.execute("""
                INSERT INTO audit_events (
                    id, correlation_id, parent_id, event_type, severity,
                    timestamp, agent_id, agent_name, user_id,
                    discord_channel_id, discord_guild_id,
                    git_commit_sha, git_branch, message, duration_ms,
                    error_type, jsonl_file, jsonl_line
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event.id, event.correlation_id, event.parent_id,
                event.event_type, event.severity,
                event.timestamp.isoformat(), event.agent_id, event.agent_name,
                event.user_id, event.discord_channel_id, event.discord_guild_id,
                event.git_commit_sha, event.git_branch, event.message,
                event.duration_ms, event.error_type,
                str(jsonl_file), line_number
            ))
            conn.commit()
        
        logger.debug(f"Logged audit event: {event.event_type} - {event.message}")
        return event
    
    async def create_event(self, data: AuditEventCreate, **extra_fields) -> AuditEvent:
        """Create and log an audit event."""
        event = AuditEvent(
            correlation_id=data.correlation_id,
            parent_id=data.parent_id,
            event_type=data.event_type,
            severity=data.severity,
            agent_id=data.agent_id,
            agent_name=data.agent_name,
            message=data.message,
            data=data.data,
            duration_ms=data.duration_ms,
            **extra_fields
        )
        return await self.log_event(event)
    
    async def query_events(self, query: AuditQuery) -> List[AuditEvent]:
        """Query audit events from SQLite index."""
        conditions = []
        params = []
        
        if query.correlation_id:
            conditions.append("correlation_id = ?")
            params.append(query.correlation_id)
        
        if query.agent_id:
            conditions.append("agent_id = ?")
            params.append(query.agent_id)
        
        if query.agent_name:
            conditions.append("agent_name LIKE ?")
            params.append(f"%{query.agent_name}%")
        
        if query.event_types:
            placeholders = ",".join(["?" for _ in query.event_types])
            conditions.append(f"event_type IN ({placeholders})")
            params.extend([e.value for e in query.event_types])
        
        if query.severity:
            placeholders = ",".join(["?" for _ in query.severity])
            conditions.append(f"severity IN ({placeholders})")
            params.extend([s.value for s in query.severity])
        
        if query.start_time:
            conditions.append("timestamp >= ?")
            params.append(query.start_time.isoformat())
        
        if query.end_time:
            conditions.append("timestamp <= ?")
            params.append(query.end_time.isoformat())
        
        if query.search_text:
            conditions.append("message LIKE ?")
            params.append(f"%{query.search_text}%")
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        sql = f"""
            SELECT id, jsonl_file, jsonl_line
            FROM audit_events
            WHERE {where_clause}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        """
        params.extend([query.limit, query.offset])
        
        events = []
        with self._get_sqlite_connection() as conn:
            cursor = conn.execute(sql, params)
            rows = cursor.fetchall()
        
        # Load full events from JSONL
        for row in rows:
            event = self._load_event_from_jsonl(row['jsonl_file'], row['jsonl_line'])
            if event:
                events.append(event)
        
        return events
    
    def _load_event_from_jsonl(self, file_path: str, line_number: int) -> Optional[AuditEvent]:
        """Load a specific event from JSONL file."""
        try:
            with open(file_path, 'r') as f:
                for i, line in enumerate(f):
                    if i == line_number:
                        data = json.loads(line)
                        if isinstance(data.get('timestamp'), str):
                            data['timestamp'] = datetime.fromisoformat(data['timestamp'])
                        return AuditEvent(**data)
        except Exception as e:
            logger.error(f"Error loading event from JSONL: {e}")
        return None
    
    async def get_timeline(self, correlation_id: str) -> Optional[AuditTimeline]:
        """Get timeline view for a correlation ID."""
        query = AuditQuery(correlation_id=correlation_id, limit=1000)
        events = await self.query_events(query)
        
        if not events:
            return None
        
        # Sort by timestamp
        events.sort(key=lambda e: e.timestamp)
        
        # Determine status
        status = "in_progress"
        for event in events:
            if event.event_type in [
                AuditEventType.EXECUTION_COMPLETED,
                AuditEventType.VERIFICATION_PASSED
            ]:
                status = "completed"
                break
            elif event.event_type in [
                AuditEventType.EXECUTION_FAILED,
                AuditEventType.VERIFICATION_FAILED,
                AuditEventType.LLM_ERROR
            ]:
                status = "failed"
                break
        
        # Generate summary
        agent_name = events[0].agent_name if events else None
        summary = self._generate_timeline_summary(events)
        
        return AuditTimeline(
            correlation_id=correlation_id,
            start_time=events[0].timestamp,
            end_time=events[-1].timestamp if len(events) > 1 else None,
            agent_name=agent_name,
            summary=summary,
            events=events,
            status=status
        )
    
    def _generate_timeline_summary(self, events: List[AuditEvent]) -> str:
        """Generate a human-readable summary of events."""
        if not events:
            return "No events"
        
        event_counts = {}
        for event in events:
            event_type = event.event_type.split(".")[-1]
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
        
        parts = [f"{count} {event_type}" for event_type, count in event_counts.items()]
        return f"{len(events)} total events: " + ", ".join(parts[:5])
    
    async def get_recent_events(self, limit: int = 50) -> List[AuditEvent]:
        """Get most recent events."""
        query = AuditQuery(limit=limit)
        return await self.query_events(query)
    
    async def get_event_stats(self) -> Dict[str, Any]:
        """Get audit event statistics."""
        with self._get_sqlite_connection() as conn:
            # Total events
            total = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
            
            # Events by type
            by_type = dict(conn.execute("""
                SELECT event_type, COUNT(*) 
                FROM audit_events 
                GROUP BY event_type
            """).fetchall())
            
            # Events by severity
            by_severity = dict(conn.execute("""
                SELECT severity, COUNT(*) 
                FROM audit_events 
                GROUP BY severity
            """).fetchall())
            
            # Recent activity (last hour)
            one_hour_ago = (datetime.now(timezone.utc).replace(microsecond=0)).isoformat()
            recent = conn.execute("""
                SELECT COUNT(*) FROM audit_events 
                WHERE timestamp >= ?
            """, (one_hour_ago,)).fetchone()[0]
            
            # Unique correlations
            correlations = conn.execute("""
                SELECT COUNT(DISTINCT correlation_id) FROM audit_events
            """).fetchone()[0]
        
        return {
            "total_events": total,
            "events_by_type": by_type,
            "events_by_severity": by_severity,
            "recent_events_1h": recent,
            "unique_correlations": correlations
        }
    
    async def export_timeline(self, correlation_id: str, format: str = "json") -> str:
        """Export timeline for a correlation ID."""
        timeline = await self.get_timeline(correlation_id)
        if not timeline:
            return ""
        
        if format == "json":
            return json.dumps(timeline.model_dump(), default=str, indent=2)
        elif format == "text":
            lines = [
                f"Timeline: {timeline.correlation_id}",
                f"Status: {timeline.status}",
                f"Agent: {timeline.agent_name or 'N/A'}",
                f"Duration: {timeline.start_time} - {timeline.end_time or 'ongoing'}",
                f"Summary: {timeline.summary}",
                "-" * 50,
            ]
            for event in timeline.events:
                lines.append(
                    f"[{event.timestamp.strftime('%H:%M:%S.%f')[:-3]}] "
                    f"{event.severity.upper():8} {event.event_type}: {event.message}"
                )
            return "\n".join(lines)
        
        return ""
