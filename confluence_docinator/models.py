"""
Data models for Confluence Docinator.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
import json


class DiffStatus(Enum):
    """Status of a file comparison."""
    UNCHANGED = "unchanged"
    LOCAL_MODIFIED = "local_modified"
    REMOTE_MODIFIED = "remote_modified"
    CONFLICT = "conflict"
    LOCAL_ONLY = "local_only"
    REMOTE_ONLY = "remote_only"
    DELETED_LOCAL = "deleted_local"
    DELETED_REMOTE = "deleted_remote"


@dataclass
class PageMetadata:
    """Metadata for a Confluence page stored locally."""
    page_id: str
    title: str
    space_key: str
    version: int
    last_modified: str
    last_modified_by: str
    parent_id: Optional[str] = None
    web_url: Optional[str] = None
    content_hash: Optional[str] = None
    local_path: Optional[str] = None
    labels: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "page_id": self.page_id,
            "title": self.title,
            "space_key": self.space_key,
            "version": self.version,
            "last_modified": self.last_modified,
            "last_modified_by": self.last_modified_by,
            "parent_id": self.parent_id,
            "web_url": self.web_url,
            "content_hash": self.content_hash,
            "local_path": self.local_path,
            "labels": self.labels or [],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PageMetadata":
        return cls(
            page_id=data["page_id"],
            title=data["title"],
            space_key=data["space_key"],
            version=data["version"],
            last_modified=data["last_modified"],
            last_modified_by=data["last_modified_by"],
            parent_id=data.get("parent_id"),
            web_url=data.get("web_url"),
            content_hash=data.get("content_hash"),
            local_path=data.get("local_path"),
            labels=data.get("labels", []),
        )


@dataclass
class FolderMetadata:
    """Metadata for a Confluence folder stored locally."""
    folder_id: str
    title: str
    space_key: str
    parent_id: Optional[str] = None
    local_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "folder_id": self.folder_id,
            "title": self.title,
            "space_key": self.space_key,
            "parent_id": self.parent_id,
            "local_path": self.local_path,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FolderMetadata":
        return cls(
            folder_id=data["folder_id"],
            title=data["title"],
            space_key=data["space_key"],
            parent_id=data.get("parent_id"),
            local_path=data.get("local_path"),
        )


@dataclass
class DiffResult:
    """Result of comparing a local file with its remote counterpart."""
    local_path: str
    page_id: Optional[str]
    title: str
    status: DiffStatus
    local_version: Optional[int] = None
    remote_version: Optional[int] = None
    local_modified: Optional[str] = None
    remote_modified: Optional[str] = None
    remote_modified_by: Optional[str] = None
    local_content: Optional[str] = None
    remote_content: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "local_path": self.local_path,
            "page_id": self.page_id,
            "title": self.title,
            "status": self.status.value,
            "local_version": self.local_version,
            "remote_version": self.remote_version,
            "local_modified": self.local_modified,
            "remote_modified": self.remote_modified,
            "remote_modified_by": self.remote_modified_by,
        }


@dataclass
class SyncConfig:
    """Configuration for sync operations."""
    base_url: str
    username: str
    api_key: str
    space_key: str
    editor_version: int = 2
    local_root: str = "confluence_pages"

    @classmethod
    def from_env(cls, env_dict: Dict[str, str]) -> "SyncConfig":
        return cls(
            base_url=env_dict.get("CONFLUENCE_BASE_URL", ""),
            username=env_dict.get("CONFLUENCE_USERNAME", ""),
            api_key=env_dict.get("CONFLUENCE_API_KEY", ""),
            space_key=env_dict.get("CONFLUENCE_SPACE_KEY", ""),
            editor_version=int(env_dict.get("CONFLUENCE_EDITOR_VERSION", "2")),
        )
