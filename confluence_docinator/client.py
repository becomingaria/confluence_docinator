"""
Confluence API client for interacting with Confluence Cloud REST API.
"""

import re
import requests
from requests.auth import HTTPBasicAuth
from typing import Optional, List, Dict, Any, Tuple
from urllib.parse import urlparse, parse_qs, unquote
import json

from .models import PageMetadata, FolderMetadata, SyncConfig


class ConfluenceClient:
    """
    Client for Confluence Cloud REST API v2.

    Handles authentication, page retrieval, content updates, and folder navigation.
    """

    def __init__(self, config: SyncConfig):
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self.auth = HTTPBasicAuth(config.username, config.api_key)
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def _api_v1(self, endpoint: str) -> str:
        """Get full URL for API v1 endpoint."""
        return f"{self.base_url}/rest/api/{endpoint}"

    def _api_v2(self, endpoint: str) -> str:
        """Get full URL for API v2 endpoint."""
        return f"{self.base_url}/api/v2/{endpoint}"

    def parse_confluence_url(self, url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Parse a Confluence URL to extract space key and page/folder ID.

        Returns:
            Tuple of (space_key, content_id, content_type)
            content_type is 'page' or 'folder'
        """
        parsed = urlparse(url)
        path = parsed.path

        # Pattern: /wiki/spaces/{spaceKey}/folder/{folderId}
        folder_match = re.search(r'/wiki/spaces/([^/]+)/folder/(\d+)', path)
        if folder_match:
            return folder_match.group(1), folder_match.group(2), "folder"

        # Pattern: /wiki/spaces/{spaceKey}/pages/{pageId}
        page_match = re.search(r'/wiki/spaces/([^/]+)/pages/(\d+)', path)
        if page_match:
            return page_match.group(1), page_match.group(2), "page"

        # Pattern: /wiki/spaces/{spaceKey}/pages/{pageId}/{title}
        page_title_match = re.search(
            r'/wiki/spaces/([^/]+)/pages/(\d+)/([^?]+)', path)
        if page_title_match:
            return page_title_match.group(1), page_title_match.group(2), "page"

        # Pattern: /wiki/display/{spaceKey}/{title}
        display_match = re.search(r'/wiki/display/([^/]+)/(.+)', path)
        if display_match:
            space_key = display_match.group(1)
            title = unquote(display_match.group(2).replace('+', ' '))
            # Need to look up page by title
            page = self.get_page_by_title(space_key, title)
            if page:
                return space_key, page["id"], "page"

        return None, None, None

    def get_page(self, page_id: str, expand: List[str] = None) -> Optional[Dict[str, Any]]:
        """
        Get a page by ID.

        Args:
            page_id: The Confluence page ID
            expand: List of fields to expand (e.g., ['body.storage', 'version', 'ancestors'])
        """
        if expand is None:
            expand = ["body.storage", "version", "history", "ancestors"]

        url = self._api_v1(f"content/{page_id}")
        params = {"expand": ",".join(expand)}

        response = self.session.get(url, params=params)
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            return None
        else:
            response.raise_for_status()

    def get_page_by_title(self, space_key: str, title: str) -> Optional[Dict[str, Any]]:
        """Get a page by title in a space."""
        url = self._api_v1("content")
        params = {
            "spaceKey": space_key,
            "title": title,
            "expand": "body.storage,version,history,ancestors",
        }

        response = self.session.get(url, params=params)
        response.raise_for_status()

        results = response.json().get("results", [])
        return results[0] if results else None

    def get_page_content(self, page_id: str) -> Tuple[Optional[str], Optional[PageMetadata]]:
        """
        Get page content in storage format with metadata.

        Returns:
            Tuple of (content_html, metadata)
        """
        page = self.get_page(page_id)
        if not page:
            return None, None

        content = page.get("body", {}).get("storage", {}).get("value", "")

        metadata = PageMetadata(
            page_id=page["id"],
            title=page["title"],
            space_key=page.get("space", {}).get("key", self.config.space_key),
            version=page.get("version", {}).get("number", 1),
            last_modified=page.get("version", {}).get("when", ""),
            last_modified_by=page.get("version", {}).get(
                "by", {}).get("displayName", ""),
            parent_id=page.get(
                "ancestors", [{}])[-1].get("id") if page.get("ancestors") else None,
            web_url=f"{self.base_url}{page.get('_links', {}).get('webui', '')}",
        )

        return content, metadata

    def get_child_pages(self, parent_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get all child pages of a parent page/folder."""
        all_children = []
        start = 0

        while True:
            # Try direct child endpoint first (works for pages)
            url = self._api_v1(f"content/{parent_id}/child/page")
            params = {
                "expand": "body.storage,version,history",
                "start": start,
                "limit": limit,
            }

            response = self.session.get(url, params=params)
            
            # If 404, the parent might be a folder - use CQL search instead
            if response.status_code == 404:
                return self._get_child_pages_cql(parent_id, limit)
            
            response.raise_for_status()

            data = response.json()
            results = data.get("results", [])
            all_children.extend(results)

            if len(results) < limit:
                break
            start += limit

        return all_children

    def _get_child_pages_cql(self, parent_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get child pages using CQL search (works for folders)."""
        all_children = []
        start = 0

        while True:
            url = self._api_v1("content/search")
            cql = f"parent={parent_id} and type=page"
            params = {
                "cql": cql,
                "start": start,
                "limit": limit,
                "expand": "body.storage,version,history",
            }

            response = self.session.get(url, params=params)
            response.raise_for_status()

            data = response.json()
            results = data.get("results", [])
            all_children.extend(results)

            if len(results) < limit:
                break
            start += limit

        return all_children

    def get_child_folders(self, parent_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get all child folders of a parent page/folder."""
        all_folders = []
        start = 0

        while True:
            # Use CQL to find child folders
            url = self._api_v1("content/search")
            cql = f"parent={parent_id} and type=folder"
            params = {
                "cql": cql,
                "start": start,
                "limit": limit,
                "expand": "version,history",
            }

            response = self.session.get(url, params=params)
            response.raise_for_status()

            data = response.json()
            results = data.get("results", [])
            all_folders.extend(results)

            if len(results) < limit:
                break
            start += limit

        return all_folders

    def get_all_children(self, parent_id: str, limit: int = 100) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Get all child pages AND folders of a parent.

        Returns:
            Tuple of (pages, folders)
        """
        pages = self.get_child_pages(parent_id, limit)
        folders = self.get_child_folders(parent_id, limit)
        return pages, folders

    def get_descendants(self, parent_id: str, depth: int = -1) -> List[Dict[str, Any]]:
        """
        Recursively get all descendant pages and folders.

        Args:
            parent_id: The parent page/folder ID
            depth: Maximum depth to traverse (-1 for unlimited)

        Returns:
            List of all descendant pages with their hierarchy info
        """
        descendants = []

        def _fetch_recursive(current_id: str, current_depth: int, path: str):
            if depth != -1 and current_depth > depth:
                return

            # Get both pages and folders
            pages, folders = self.get_all_children(current_id)

            # Helper to build paths without leading slashes
            def join_path(base: str, name: str) -> str:
                safe_name = self._sanitize_filename(name)
                if base:
                    return f"{base}/{safe_name}"
                return safe_name

            # Process pages
            for page in pages:
                page["_local_path"] = path
                page["_depth"] = current_depth
                page["_is_folder"] = False
                descendants.append(page)

                # Pages can also have children
                page_path = join_path(path, page['title'])
                _fetch_recursive(page["id"], current_depth + 1, page_path)

            # Process folders - add them and recurse into them
            for folder in folders:
                folder["_local_path"] = path
                folder["_depth"] = current_depth
                folder["_is_folder"] = True
                descendants.append(folder)

                # Recurse into folder
                folder_path = join_path(path, folder['title'])
                _fetch_recursive(folder["id"], current_depth + 1, folder_path)

        # Start recursive fetch
        _fetch_recursive(parent_id, 0, "")

        return descendants

    def get_folder_info(self, folder_id: str) -> Optional[Dict[str, Any]]:
        """Get information about a folder (which is actually a page in Confluence)."""
        return self.get_page(folder_id)

    def update_page(
        self,
        page_id: str,
        title: str,
        content: str,
        version: int,
        message: str = None,
    ) -> Dict[str, Any]:
        """
        Update a page with new content.

        Args:
            page_id: The page ID to update
            title: The page title
            content: The new content in storage format
            version: The current version number (will be incremented)
            message: Optional version message

        Returns:
            The updated page data
        """
        url = self._api_v1(f"content/{page_id}")

        payload = {
            "id": page_id,
            "type": "page",
            "title": title,
            "body": {
                "storage": {
                    "value": content,
                    "representation": "storage",
                }
            },
            "version": {
                "number": version + 1,
            }
        }

        if message:
            payload["version"]["message"] = message

        response = self.session.put(url, json=payload)
        response.raise_for_status()

        return response.json()

    def create_page(
        self,
        space_key: str,
        title: str,
        content: str,
        parent_id: str = None,
    ) -> Dict[str, Any]:
        """
        Create a new page.

        Args:
            space_key: The space key
            title: The page title
            content: The content in storage format
            parent_id: Optional parent page ID

        Returns:
            The created page data
        """
        url = self._api_v1("content")

        payload = {
            "type": "page",
            "title": title,
            "space": {"key": space_key},
            "body": {
                "storage": {
                    "value": content,
                    "representation": "storage",
                }
            },
        }

        if parent_id:
            payload["ancestors"] = [{"id": parent_id}]

        response = self.session.post(url, json=payload)
        response.raise_for_status()

        return response.json()

    def get_page_history(self, page_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get version history of a page."""
        url = self._api_v1(f"content/{page_id}/version")
        params = {"limit": limit}

        response = self.session.get(url, params=params)
        response.raise_for_status()

        return response.json().get("results", [])

    def get_page_version(self, page_id: str, version: int) -> Optional[Dict[str, Any]]:
        """Get a specific version of a page."""
        url = self._api_v1(f"content/{page_id}")
        params = {
            "expand": "body.storage,version",
            "version": version,
        }

        response = self.session.get(url, params=params)
        if response.status_code == 200:
            return response.json()
        return None

    def test_connection(self) -> Tuple[bool, str]:
        """Test the connection to Confluence."""
        try:
            url = self._api_v1("space")
            params = {"limit": 1}
            response = self.session.get(url, params=params)

            if response.status_code == 200:
                return True, "Connection successful"
            elif response.status_code == 401:
                return False, "Authentication failed - check username and API key"
            else:
                return False, f"Connection failed with status {response.status_code}"
        except requests.exceptions.RequestException as e:
            return False, f"Connection error: {str(e)}"

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
        return name
