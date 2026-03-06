"""
Local storage management for Confluence pages.

Handles:
- Saving/loading page content and metadata
- Directory structure management
- Metadata index for tracking synced pages
- Format conversion (XHTML/Markdown)
"""

import os
import json
import hashlib
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple
from datetime import datetime

from .models import PageMetadata, FolderMetadata, SyncConfig


class StorageManager:
    """
    Manages local storage of Confluence pages.

    Directory structure:
    {root}/
        .confluence/
            config.json         # Sync configuration
            index.json          # Index of all tracked pages
            pages/
                {page_id}.json  # Individual page metadata
            macros/
                {page_id}.json  # Macro store for markdown files
        {folder_name}/
            {page_title}.md     # Page content in Markdown (or .xhtml)
            {subfolder}/
                ...
    """

    METADATA_DIR = ".confluence"
    PAGES_METADATA_DIR = ".confluence/pages"
    MACROS_DIR = ".confluence/macros"
    CONFIG_FILE = ".confluence/config.json"
    INDEX_FILE = ".confluence/index.json"

    # Supported formats
    FORMAT_XHTML = "xhtml"
    FORMAT_MARKDOWN = "md"

    EXTENSIONS = {
        FORMAT_XHTML: ".xhtml",
        FORMAT_MARKDOWN: ".md",
    }

    def __init__(self, root_path: str, content_format: str = FORMAT_MARKDOWN):
        self.root = Path(root_path).resolve()
        self.metadata_dir = self.root / self.METADATA_DIR
        self.pages_dir = self.metadata_dir / "pages"
        self.macros_dir = self.metadata_dir / "macros"
        self.content_format = content_format
        self.CONTENT_EXTENSION = self.EXTENSIONS.get(content_format, ".md")

    def initialize(self, config: SyncConfig, target_url: str, root_page_id: str, content_format: str = None):
        """Initialize storage with configuration."""
        self.metadata_dir.mkdir(parents=True, exist_ok=True)
        self.pages_dir.mkdir(parents=True, exist_ok=True)
        self.macros_dir.mkdir(parents=True, exist_ok=True)

        if content_format:
            self.content_format = content_format
            self.CONTENT_EXTENSION = self.EXTENSIONS.get(content_format, ".md")

        # Save config
        config_data = {
            "base_url": config.base_url,
            "space_key": config.space_key,
            "target_url": target_url,
            "root_page_id": root_page_id,
            "initialized_at": datetime.now().isoformat(),
            "editor_version": config.editor_version,
            "content_format": self.content_format,
        }

        config_path = self.root / self.CONFIG_FILE
        with open(config_path, 'w') as f:
            json.dump(config_data, f, indent=2)

        # Initialize empty index
        index_path = self.root / self.INDEX_FILE
        if not index_path.exists():
            with open(index_path, 'w') as f:
                json.dump({"pages": {}, "folders": {}}, f, indent=2)

    def is_initialized(self) -> bool:
        """Check if storage is initialized."""
        return (self.root / self.CONFIG_FILE).exists()

    def get_config(self) -> Optional[Dict[str, Any]]:
        """Get stored configuration."""
        config_path = self.root / self.CONFIG_FILE
        if config_path.exists():
            with open(config_path) as f:
                return json.load(f)
        return None

    def get_index(self) -> Dict[str, Any]:
        """Get the page index."""
        index_path = self.root / self.INDEX_FILE
        if index_path.exists():
            with open(index_path) as f:
                return json.load(f)
        return {"pages": {}, "folders": {}}

    def save_index(self, index: Dict[str, Any]):
        """Save the page index."""
        index_path = self.root / self.INDEX_FILE
        with open(index_path, 'w') as f:
            json.dump(index, f, indent=2)

    def save_page(
        self,
        content: str,
        metadata: PageMetadata,
        relative_path: str = "",
    ) -> str:
        """
        Save a page's content and metadata.

        Args:
            content: The page content in storage format
            metadata: Page metadata
            relative_path: Relative path within the root directory

        Returns:
            The local file path where content was saved
        """
        # Determine file path
        safe_title = self._sanitize_filename(metadata.title)
        file_name = f"{safe_title}{self.CONTENT_EXTENSION}"

        if relative_path:
            content_dir = self.root / relative_path
        else:
            content_dir = self.root

        content_dir.mkdir(parents=True, exist_ok=True)
        file_path = content_dir / file_name

        # Save content
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)

        # Calculate content hash
        content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()

        # Update metadata with local path and hash
        relative_file_path = str(file_path.relative_to(self.root))
        metadata.local_path = relative_file_path
        metadata.content_hash = content_hash

        # Save page metadata
        self._save_page_metadata(metadata)

        # Update index
        self._update_index(metadata)

        return relative_file_path

    def _save_page_metadata(self, metadata: PageMetadata):
        """Save individual page metadata."""
        meta_file = self.pages_dir / f"{metadata.page_id}.json"
        with open(meta_file, 'w') as f:
            json.dump(metadata.to_dict(), f, indent=2)

    def _update_index(self, metadata: PageMetadata):
        """Update the page index with new/updated page."""
        index = self.get_index()

        index["pages"][metadata.page_id] = {
            "title": metadata.title,
            "local_path": metadata.local_path,
            "version": metadata.version,
            "content_hash": metadata.content_hash,
            "last_synced": datetime.now().isoformat(),
        }

        self.save_index(index)

    def save_macro_store(self, page_id: str, macro_store: Dict[str, str]):
        """Save macro store for a page (used when saving as Markdown)."""
        self.macros_dir.mkdir(parents=True, exist_ok=True)
        macro_file = self.macros_dir / f"{page_id}.json"
        with open(macro_file, 'w') as f:
            json.dump(macro_store, f, indent=2)

    def get_macro_store(self, page_id: str) -> Dict[str, str]:
        """Get macro store for a page."""
        macro_file = self.macros_dir / f"{page_id}.json"
        if macro_file.exists():
            with open(macro_file) as f:
                return json.load(f)
        return {}

    def get_content_format(self) -> str:
        """Get the content format from config."""
        config = self.get_config()
        if config:
            return config.get("content_format", self.FORMAT_MARKDOWN)
        return self.content_format

    def get_page_metadata(self, page_id: str) -> Optional[PageMetadata]:
        """Get metadata for a specific page."""
        meta_file = self.pages_dir / f"{page_id}.json"
        if meta_file.exists():
            with open(meta_file) as f:
                return PageMetadata.from_dict(json.load(f))
        return None

    def get_page_by_path(self, local_path: str) -> Optional[PageMetadata]:
        """Get page metadata by local file path."""
        index = self.get_index()

        # Normalize path
        if not local_path.startswith('/'):
            check_path = local_path
        else:
            check_path = str(Path(local_path).relative_to(self.root))

        for page_id, info in index.get("pages", {}).items():
            if info.get("local_path") == check_path:
                return self.get_page_metadata(page_id)

        return None

    def read_local_content(self, local_path: str) -> Optional[str]:
        """Read content from a local file."""
        full_path = self.root / local_path
        if full_path.exists():
            with open(full_path, 'r', encoding='utf-8') as f:
                return f.read()
        return None

    def get_local_content_hash(self, local_path: str) -> Optional[str]:
        """Calculate hash of local file content."""
        content = self.read_local_content(local_path)
        if content:
            return hashlib.sha256(content.encode('utf-8')).hexdigest()
        return None

    def list_tracked_pages(self) -> List[Dict[str, Any]]:
        """List all tracked pages with their info."""
        index = self.get_index()
        pages = []

        for page_id, info in index.get("pages", {}).items():
            metadata = self.get_page_metadata(page_id)
            if metadata:
                pages.append({
                    "page_id": page_id,
                    "title": metadata.title,
                    "local_path": metadata.local_path,
                    "version": metadata.version,
                    "exists_locally": (self.root / metadata.local_path).exists() if metadata.local_path else False,
                })

        return pages

    def save_folder(self, folder_id: str, title: str, space_key: str, relative_path: str = ""):
        """Save folder metadata and create directory."""
        safe_title = self._sanitize_filename(title)

        if relative_path:
            folder_path = self.root / relative_path / safe_title
        else:
            folder_path = self.root / safe_title

        folder_path.mkdir(parents=True, exist_ok=True)

        # Update index
        index = self.get_index()
        index["folders"][folder_id] = {
            "title": title,
            "local_path": str(folder_path.relative_to(self.root)),
            "space_key": space_key,
        }
        self.save_index(index)

        return str(folder_path.relative_to(self.root))

    def find_all_content_files(self) -> List[str]:
        """Find all content files in the storage."""
        files = []
        for path in self.root.rglob(f"*{self.CONTENT_EXTENSION}"):
            if self.METADATA_DIR not in str(path):
                files.append(str(path.relative_to(self.root)))
        return files

    def delete_page(self, page_id: str):
        """Delete a page's content and metadata."""
        metadata = self.get_page_metadata(page_id)

        if metadata and metadata.local_path:
            content_file = self.root / metadata.local_path
            if content_file.exists():
                content_file.unlink()

        # Remove metadata
        meta_file = self.pages_dir / f"{page_id}.json"
        if meta_file.exists():
            meta_file.unlink()

        # Update index
        index = self.get_index()
        if page_id in index.get("pages", {}):
            del index["pages"][page_id]
            self.save_index(index)

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """Sanitize a string for use as a filename."""
        # Remove or replace invalid characters
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, '_')
        # Collapse multiple underscores
        while '__' in name:
            name = name.replace('__', '_')
        # Strip leading/trailing spaces and dots
        name = name.strip(' .')
        # Limit length
        if len(name) > 200:
            name = name[:200]
        return name

    def create_backup(self, page_id: str) -> Optional[str]:
        """Create a backup of a page before overwriting."""
        metadata = self.get_page_metadata(page_id)
        if not metadata or not metadata.local_path:
            return None

        content_file = self.root / metadata.local_path
        if not content_file.exists():
            return None

        backup_dir = self.metadata_dir / "backups" / page_id
        backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = backup_dir / \
            f"v{metadata.version}_{timestamp}{self.CONTENT_EXTENSION}"

        import shutil
        shutil.copy2(content_file, backup_file)

        return str(backup_file)
