"""Git Grounding Service for source-of-truth management."""
import os
import subprocess
import hashlib
import json
import logging
from pathlib import Path
from typing import Optional, Dict, List, Any
from datetime import datetime, timezone
from dataclasses import dataclass

logger = logging.getLogger(__name__)

WORKSPACE_ROOT = Path("/app")


@dataclass
class GitState:
    """Current Git state."""
    commit_sha: str
    branch: str
    remote_url: Optional[str]
    is_dirty: bool
    uncommitted_changes: List[str]
    ahead_behind: Dict[str, int]


@dataclass 
class WorkspaceSnapshot:
    """Workspace snapshot for grounding."""
    id: str
    commit_sha: str
    branch: str
    timestamp: datetime
    file_hashes: Dict[str, str]
    total_files: int


class GitGroundingService:
    """Service for Git-based source-of-truth grounding."""
    
    def __init__(self, workspace_root: Path = WORKSPACE_ROOT):
        self.workspace_root = workspace_root
        self._snapshots: Dict[str, WorkspaceSnapshot] = {}
    
    def _run_git(self, *args, check: bool = True) -> subprocess.CompletedProcess:
        """Run a git command."""
        try:
            result = subprocess.run(
                ["git"] + list(args),
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                check=check
            )
            return result
        except subprocess.CalledProcessError as e:
            logger.error(f"Git command failed: {e.stderr}")
            raise
    
    def get_current_state(self) -> GitState:
        """Get current Git state."""
        # Get commit SHA
        result = self._run_git("rev-parse", "HEAD", check=False)
        commit_sha = result.stdout.strip() if result.returncode == 0 else "unknown"
        
        # Get branch
        result = self._run_git("rev-parse", "--abbrev-ref", "HEAD", check=False)
        branch = result.stdout.strip() if result.returncode == 0 else "unknown"
        
        # Get remote URL
        result = self._run_git("remote", "get-url", "origin", check=False)
        remote_url = result.stdout.strip() if result.returncode == 0 else None
        
        # Check for uncommitted changes
        result = self._run_git("status", "--porcelain", check=False)
        uncommitted = result.stdout.strip().split("\n") if result.stdout.strip() else []
        uncommitted = [f for f in uncommitted if f]
        is_dirty = len(uncommitted) > 0
        
        # Get ahead/behind status
        ahead_behind = {"ahead": 0, "behind": 0}
        if remote_url:
            result = self._run_git("rev-list", "--left-right", "--count", f"HEAD...origin/{branch}", check=False)
            if result.returncode == 0:
                parts = result.stdout.strip().split()
                if len(parts) == 2:
                    ahead_behind["ahead"] = int(parts[0])
                    ahead_behind["behind"] = int(parts[1])
        
        return GitState(
            commit_sha=commit_sha,
            branch=branch,
            remote_url=remote_url,
            is_dirty=is_dirty,
            uncommitted_changes=uncommitted,
            ahead_behind=ahead_behind
        )
    
    def get_commit_info(self, sha: str = "HEAD") -> Dict[str, Any]:
        """Get information about a specific commit."""
        result = self._run_git(
            "log", "-1", "--format=%H|%an|%ae|%ai|%s", sha,
            check=False
        )
        
        if result.returncode != 0:
            return {"error": "Commit not found"}
        
        parts = result.stdout.strip().split("|")
        if len(parts) >= 5:
            return {
                "sha": parts[0],
                "author_name": parts[1],
                "author_email": parts[2],
                "date": parts[3],
                "message": "|".join(parts[4:])  # Message might contain |
            }
        
        return {"error": "Invalid commit format"}
    
    def get_diff(self, from_ref: str = "HEAD~1", to_ref: str = "HEAD") -> str:
        """Get diff between two refs."""
        result = self._run_git("diff", from_ref, to_ref, check=False)
        return result.stdout if result.returncode == 0 else ""
    
    def get_local_remote_diff(self) -> Dict[str, Any]:
        """Get diff between local and remote."""
        state = self.get_current_state()
        
        if not state.remote_url:
            return {
                "has_remote": False,
                "message": "No remote configured"
            }
        
        # Fetch latest
        self._run_git("fetch", "origin", check=False)
        
        # Get diff
        diff_result = self._run_git(
            "diff", f"origin/{state.branch}", "HEAD",
            check=False
        )
        
        return {
            "has_remote": True,
            "branch": state.branch,
            "local_sha": state.commit_sha,
            "ahead": state.ahead_behind["ahead"],
            "behind": state.ahead_behind["behind"],
            "has_diverged": state.ahead_behind["ahead"] > 0 and state.ahead_behind["behind"] > 0,
            "diff": diff_result.stdout[:5000] if diff_result.stdout else None,
            "diff_truncated": len(diff_result.stdout) > 5000 if diff_result.stdout else False
        }
    
    def create_snapshot(self, name: str = None) -> WorkspaceSnapshot:
        """Create a workspace snapshot for grounding."""
        state = self.get_current_state()
        
        # Generate snapshot ID
        timestamp = datetime.now(timezone.utc)
        snapshot_id = hashlib.sha256(
            f"{state.commit_sha}:{timestamp.isoformat()}:{name or ''}".encode()
        ).hexdigest()[:16]
        
        # Hash tracked files
        file_hashes = {}
        result = self._run_git("ls-files", check=False)
        if result.returncode == 0:
            for file_path in result.stdout.strip().split("\n"):
                if file_path:
                    full_path = self.workspace_root / file_path
                    if full_path.exists() and full_path.is_file():
                        try:
                            content = full_path.read_bytes()
                            file_hashes[file_path] = hashlib.sha256(content).hexdigest()[:16]
                        except Exception:
                            pass
        
        snapshot = WorkspaceSnapshot(
            id=snapshot_id,
            commit_sha=state.commit_sha,
            branch=state.branch,
            timestamp=timestamp,
            file_hashes=file_hashes,
            total_files=len(file_hashes)
        )
        
        self._snapshots[snapshot_id] = snapshot
        return snapshot
    
    def compare_snapshots(self, snapshot_id1: str, snapshot_id2: str) -> Dict[str, Any]:
        """Compare two snapshots."""
        s1 = self._snapshots.get(snapshot_id1)
        s2 = self._snapshots.get(snapshot_id2)
        
        if not s1 or not s2:
            return {"error": "Snapshot not found"}
        
        # Find changes
        added = set(s2.file_hashes.keys()) - set(s1.file_hashes.keys())
        removed = set(s1.file_hashes.keys()) - set(s2.file_hashes.keys())
        modified = {
            f for f in s1.file_hashes.keys() & s2.file_hashes.keys()
            if s1.file_hashes[f] != s2.file_hashes[f]
        }
        
        return {
            "snapshot1": snapshot_id1,
            "snapshot2": snapshot_id2,
            "added": list(added),
            "removed": list(removed),
            "modified": list(modified),
            "total_changes": len(added) + len(removed) + len(modified)
        }
    
    def verify_grounding(self, expected_sha: str) -> Dict[str, Any]:
        """Verify current state matches expected commit SHA."""
        state = self.get_current_state()
        
        matches = state.commit_sha.startswith(expected_sha) or expected_sha.startswith(state.commit_sha)
        
        return {
            "expected_sha": expected_sha,
            "current_sha": state.commit_sha,
            "matches": matches,
            "is_dirty": state.is_dirty,
            "uncommitted_changes": state.uncommitted_changes if state.is_dirty else [],
            "verified_at": datetime.now(timezone.utc).isoformat()
        }
    
    def get_file_at_commit(self, file_path: str, commit_sha: str = "HEAD") -> Optional[str]:
        """Get file content at a specific commit."""
        result = self._run_git("show", f"{commit_sha}:{file_path}", check=False)
        return result.stdout if result.returncode == 0 else None
    
    def get_commit_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent commit history."""
        result = self._run_git(
            "log", f"-{limit}", "--format=%H|%an|%ai|%s",
            check=False
        )
        
        if result.returncode != 0:
            return []
        
        commits = []
        for line in result.stdout.strip().split("\n"):
            if line:
                parts = line.split("|")
                if len(parts) >= 4:
                    commits.append({
                        "sha": parts[0][:8],
                        "full_sha": parts[0],
                        "author": parts[1],
                        "date": parts[2],
                        "message": "|".join(parts[3:])
                    })
        
        return commits
    
    def to_dict(self) -> Dict[str, Any]:
        """Get full Git grounding status."""
        state = self.get_current_state()
        
        return {
            "current_state": {
                "commit_sha": state.commit_sha,
                "branch": state.branch,
                "remote_url": state.remote_url,
                "is_dirty": state.is_dirty,
                "uncommitted_changes": state.uncommitted_changes,
                "ahead": state.ahead_behind["ahead"],
                "behind": state.ahead_behind["behind"]
            },
            "has_remote": state.remote_url is not None,
            "snapshots_count": len(self._snapshots),
            "recent_commits": self.get_commit_history(limit=5)
        }
