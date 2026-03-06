"""
Sync operations for Confluence Docinator.

Handles:
- Pull: Download pages from Confluence
- Push: Upload local changes to Confluence
- Diff: Compare local vs remote content
- Conflict resolution
"""

import os
import hashlib
import difflib
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, Callable
from datetime import datetime
import tempfile
import subprocess

from .models import PageMetadata, DiffResult, DiffStatus, SyncConfig
from .client import ConfluenceClient
from .storage import StorageManager
from .converter import xhtml_to_markdown, markdown_to_xhtml, get_referenced_attachments


class SyncManager:
    """
    Manages synchronization between local files and Confluence.
    """

    def __init__(self, client: ConfluenceClient, storage: StorageManager):
        self.client = client
        self.storage = storage

    # ========== PULL OPERATIONS ==========

    def pull(
        self,
        url: str,
        force: bool = False,
        progress_callback: Callable[[str], None] = None,
    ) -> Dict[str, Any]:
        """
        Pull all pages from a Confluence folder/page URL.

        Args:
            url: Confluence URL to pull from
            force: If True, overwrite local changes without confirmation
            progress_callback: Optional callback for progress updates

        Returns:
            Summary of pull operation
        """
        def log(msg: str):
            if progress_callback:
                progress_callback(msg)

        # Parse URL
        space_key, content_id, content_type = self.client.parse_confluence_url(
            url)

        if not content_id:
            raise ValueError(f"Could not parse Confluence URL: {url}")

        log(f"Pulling from {content_type} {content_id} in space {space_key}")

        # Get root content info
        root_info = self.client.get_page(content_id)
        if not root_info:
            raise ValueError(f"Could not find content with ID {content_id}")

        root_title = root_info["title"]
        log(f"Root: {root_title}")

        # Initialize storage if needed
        if not self.storage.is_initialized():
            self.storage.initialize(self.client.config, url, content_id)

        # Get all descendant pages
        log("Fetching page tree...")
        descendants = self.client.get_descendants(content_id)
        log(f"Found {len(descendants)} pages to sync")

        # Also include the root page itself
        root_info["_local_path"] = ""
        root_info["_depth"] = 0
        all_pages = [root_info] + descendants

        pulled = 0
        skipped = 0
        errors = []

        for page in all_pages:
            page_id = page["id"]
            title = page["title"]
            local_path = page.get("_local_path", "")

            try:
                log(f"  Pulling: {title}")

                # Get content (XHTML from Confluence)
                xhtml_content, metadata = self.client.get_page_content(page_id)

                if xhtml_content is None:
                    log(f"    Skipped (no content)")
                    skipped += 1
                    continue

                # Check if we need to pull (version comparison)
                existing = self.storage.get_page_metadata(page_id)
                if existing and not force:
                    if existing.version >= metadata.version:
                        # Check if local content matches
                        local_hash = self.storage.get_local_content_hash(
                            existing.local_path)
                        if local_hash == existing.content_hash:
                            log(f"    Skipped (up to date)")
                            skipped += 1
                            continue

                # Convert to Markdown if using markdown format
                content_format = self.storage.get_content_format()
                if content_format == self.storage.FORMAT_MARKDOWN:
                    content, macro_store = xhtml_to_markdown(xhtml_content)
                    # Save macro store for later restoration on push
                    self.storage.save_macro_store(page_id, macro_store)
                else:
                    content = xhtml_content

                # Save page
                saved_path = self.storage.save_page(
                    content, metadata, local_path)
                log(f"    Saved to: {saved_path}")
                pulled += 1

                # Download attachments (images, files)
                self._pull_attachments(page_id, saved_path, log)

            except Exception as e:
                errors.append(
                    {"page_id": page_id, "title": title, "error": str(e)})
                log(f"    Error: {e}")

        return {
            "pulled": pulled,
            "skipped": skipped,
            "errors": errors,
            "total": len(all_pages),
        }

    def pull_single(self, page_id: str, force: bool = False) -> Tuple[bool, str]:
        """Pull a single page by ID."""
        content, metadata = self.client.get_page_content(page_id)

        if content is None:
            return False, "Page not found"

        existing = self.storage.get_page_metadata(page_id)
        local_path = ""

        if existing and existing.local_path:
            # Use existing path
            local_path = str(Path(existing.local_path).parent)

        if existing and not force:
            if existing.version >= metadata.version:
                local_hash = self.storage.get_local_content_hash(
                    existing.local_path)
                if local_hash == existing.content_hash:
                    return True, "Already up to date"

        saved_path = self.storage.save_page(content, metadata, local_path)
        return True, f"Saved to {saved_path}"

    def _pull_attachments(
        self,
        page_id: str,
        page_local_path: str,
        log: Callable[[str], None] = None,
    ):
        """
        Download all attachments for a page.

        Args:
            page_id: Confluence page ID
            page_local_path: Local path of the page file
            log: Optional logging callback
        """
        def _log(msg):
            if log:
                log(msg)

        try:
            attachments = self.client.get_attachments(page_id)
            if not attachments:
                return

            for att in attachments:
                filename = att.get("title", "")
                att_id = att.get("id", "")
                download_url = att.get("_links", {}).get("download", "")

                if not filename or not download_url:
                    continue

                # Check if we need to re-download (version comparison)
                existing = self.storage.get_attachment_metadata(
                    page_id, filename)
                if existing:
                    att_version = att.get("version", {}).get("number", 1) if isinstance(
                        att.get("version"), dict) else 1
                    if existing.get("version", 0) >= att_version:
                        # Check if local file still exists
                        local_hash = self.storage.get_local_attachment_hash(
                            existing.get("local_path", ""))
                        if local_hash == existing.get("content_hash"):
                            continue  # Up to date

                # Download
                try:
                    data = self.client.download_attachment(download_url)
                    saved = self.storage.save_attachment(
                        page_id, filename, data, page_local_path, att)
                    _log(f"    📎 {filename}")
                except Exception as e:
                    _log(f"    ⚠ Attachment error ({filename}): {e}")

        except Exception as e:
            _log(f"    ⚠ Could not fetch attachments: {e}")

    def _push_attachments(
        self,
        page_id: str,
        page_local_path: str,
        log: Callable[[str], None] = None,
    ) -> Tuple[int, int]:
        """
        Push modified local attachments back to Confluence.

        Args:
            page_id: Confluence page ID
            page_local_path: Local path of the page file
            log: Optional logging callback

        Returns:
            Tuple of (pushed_count, error_count)
        """
        def _log(msg):
            if log:
                log(msg)

        pushed = 0
        errors = 0

        tracked = self.storage.get_page_attachments(page_id)
        if not tracked:
            return 0, 0

        for filename, info in tracked.items():
            local_path = info.get("local_path", "")
            stored_hash = info.get("content_hash", "")

            # Check if file was modified
            current_hash = self.storage.get_local_attachment_hash(local_path)
            if current_hash is None:
                continue  # File doesn't exist locally

            if current_hash == stored_hash:
                continue  # Not modified

            # Push updated attachment
            try:
                data = self.storage.read_local_attachment(local_path)
                if data:
                    import mimetypes
                    content_type = mimetypes.guess_type(
                        filename)[0] or "application/octet-stream"
                    self.client.upload_attachment(
                        page_id, filename, data, content_type,
                        comment="Updated via docinator")
                    _log(f"    📎 Pushed: {filename}")
                    pushed += 1

                    # Update stored hash
                    att_meta = self.storage.get_attachment_metadata(
                        page_id, filename)
                    if att_meta:
                        att_meta["content_hash"] = current_hash
                        att_meta["version"] = att_meta.get("version", 0) + 1
                        self.storage._save_attachment_metadata(
                            page_id, filename, att_meta)
            except Exception as e:
                _log(f"    ⚠ Attachment push error ({filename}): {e}")
                errors += 1

        # Check for new images in _images dir that aren't tracked
        images_dir = self.storage.get_images_dir(page_local_path)
        if images_dir.exists():
            tracked_filenames = set(tracked.keys())
            for img_file in images_dir.iterdir():
                if img_file.is_file() and img_file.name not in tracked_filenames:
                    try:
                        data = img_file.read_bytes()
                        import mimetypes
                        content_type = mimetypes.guess_type(
                            img_file.name)[0] or "application/octet-stream"
                        result = self.client.upload_attachment(
                            page_id, img_file.name, data, content_type,
                            comment="Added via docinator")
                        rel_path = str(img_file.relative_to(self.storage.root))
                        self.storage.save_attachment(
                            page_id, img_file.name, data, page_local_path, result)
                        _log(f"    📎 New attachment: {img_file.name}")
                        pushed += 1
                    except Exception as e:
                        _log(
                            f"    ⚠ New attachment error ({img_file.name}): {e}")
                        errors += 1

        return pushed, errors

    # ========== DIFF OPERATIONS ==========

    def diff(
        self,
        path: str = None,
        recursive: bool = True,
    ) -> List[DiffResult]:
        """
        Compare local files with their Confluence counterparts.

        Args:
            path: Optional path to check (file or folder). If None, checks all.
            recursive: If checking a folder, include subfolders

        Returns:
            List of diff results
        """
        results = []

        if path:
            # Check specific path
            full_path = Path(path)
            if not full_path.is_absolute():
                full_path = self.storage.root / path

            if full_path.is_file():
                # Single file
                rel_path = str(full_path.relative_to(self.storage.root))
                result = self._diff_file(rel_path)
                if result:
                    results.append(result)
            elif full_path.is_dir():
                # Directory
                results.extend(self._diff_directory(full_path, recursive))
        else:
            # Check all tracked pages
            results.extend(self._diff_all())

        return results

    def _diff_file(self, local_path: str) -> Optional[DiffResult]:
        """Diff a single file."""
        metadata = self.storage.get_page_by_path(local_path)

        if not metadata:
            # File exists locally but not tracked
            return DiffResult(
                local_path=local_path,
                page_id=None,
                title=Path(local_path).stem,
                status=DiffStatus.LOCAL_ONLY,
            )

        # Get local content
        local_content = self.storage.read_local_content(local_path)
        local_hash = hashlib.sha256(local_content.encode(
            'utf-8')).hexdigest() if local_content is not None else None

        # Get remote content (XHTML from Confluence)
        remote_xhtml, remote_metadata = self.client.get_page_content(
            metadata.page_id)

        if remote_xhtml is None:
            return DiffResult(
                local_path=local_path,
                page_id=metadata.page_id,
                title=metadata.title,
                status=DiffStatus.DELETED_REMOTE,
                local_version=metadata.version,
            )

        # Convert remote content to same format as local for comparison
        content_format = self.storage.get_content_format()
        if content_format == self.storage.FORMAT_MARKDOWN:
            remote_content, _ = xhtml_to_markdown(remote_xhtml)
        else:
            remote_content = remote_xhtml

        # Compare
        stored_hash = metadata.content_hash
        local_modified = local_hash != stored_hash
        remote_modified = remote_metadata.version > metadata.version

        if local_modified and remote_modified:
            status = DiffStatus.CONFLICT
        elif local_modified:
            status = DiffStatus.LOCAL_MODIFIED
        elif remote_modified:
            status = DiffStatus.REMOTE_MODIFIED
        else:
            status = DiffStatus.UNCHANGED

        return DiffResult(
            local_path=local_path,
            page_id=metadata.page_id,
            title=metadata.title,
            status=status,
            local_version=metadata.version,
            remote_version=remote_metadata.version,
            local_modified=datetime.now().isoformat() if local_modified else None,
            remote_modified=remote_metadata.last_modified,
            remote_modified_by=remote_metadata.last_modified_by,
            local_content=local_content,
            remote_content=remote_content,
        )

    def _diff_directory(self, dir_path: Path, recursive: bool) -> List[DiffResult]:
        """Diff all files in a directory."""
        results = []

        pattern = f"**/*{self.storage.CONTENT_EXTENSION}" if recursive else f"*{self.storage.CONTENT_EXTENSION}"

        for file_path in dir_path.glob(pattern):
            if self.storage.METADATA_DIR in str(file_path):
                continue

            rel_path = str(file_path.relative_to(self.storage.root))
            result = self._diff_file(rel_path)
            if result:
                results.append(result)

        return results

    def _diff_all(self) -> List[DiffResult]:
        """Diff all tracked pages."""
        results = []
        index = self.storage.get_index()

        for page_id, info in index.get("pages", {}).items():
            local_path = info.get("local_path")
            if local_path:
                result = self._diff_file(local_path)
                if result:
                    results.append(result)

        # Also check for untracked files
        all_files = self.storage.find_all_content_files()
        tracked_paths = {info.get("local_path")
                         for info in index.get("pages", {}).values()}

        for file_path in all_files:
            if file_path not in tracked_paths:
                results.append(DiffResult(
                    local_path=file_path,
                    page_id=None,
                    title=Path(file_path).stem,
                    status=DiffStatus.LOCAL_ONLY,
                ))

        return results

    def show_diff(self, result: DiffResult, context_lines: int = 3) -> str:
        """Generate a human-readable diff for a result."""
        if result.status == DiffStatus.UNCHANGED:
            return "No changes"

        if result.status == DiffStatus.LOCAL_ONLY:
            return "Local file not tracked in Confluence"

        if result.status == DiffStatus.REMOTE_ONLY:
            return "Remote page not downloaded locally"

        if result.status == DiffStatus.DELETED_REMOTE:
            return "Page deleted from Confluence"

        if result.status == DiffStatus.DELETED_LOCAL:
            return "Local file deleted"

        # Generate unified diff
        if result.local_content and result.remote_content:
            local_lines = result.local_content.splitlines(keepends=True)
            remote_lines = result.remote_content.splitlines(keepends=True)

            diff = difflib.unified_diff(
                remote_lines,
                local_lines,
                fromfile=f"confluence (v{result.remote_version})",
                tofile=f"local (v{result.local_version})",
                n=context_lines,
            )
            return ''.join(diff)

        return f"Status: {result.status.value}"

    def show_diff_with_git(self, result: DiffResult) -> str:
        """
        Show diff using git diff for better visualization.
        Requires git to be installed.
        """
        if not result.local_content or not result.remote_content:
            return self.show_diff(result)

        with tempfile.TemporaryDirectory() as tmpdir:
            remote_file = Path(tmpdir) / "remote.xhtml"
            local_file = Path(tmpdir) / "local.xhtml"

            remote_file.write_text(result.remote_content)
            local_file.write_text(result.local_content)

            try:
                result_proc = subprocess.run(
                    ["git", "diff", "--no-index", "--color=always",
                        str(remote_file), str(local_file)],
                    capture_output=True,
                    text=True,
                )
                return result_proc.stdout or result_proc.stderr or "No differences"
            except FileNotFoundError:
                return self.show_diff(result)

    # ========== PUSH OPERATIONS ==========

    def push(
        self,
        path: str,
        message: str = None,
        force: bool = False,
        progress_callback: Callable[[str], None] = None,
    ) -> Dict[str, Any]:
        """
        Push local changes to Confluence.

        Args:
            path: Path to file or folder to push
            message: Version message for the update
            force: Push even if there are conflicts
            progress_callback: Optional callback for progress updates

        Returns:
            Summary of push operation
        """
        def log(msg: str):
            if progress_callback:
                progress_callback(msg)

        full_path = Path(path)
        if not full_path.is_absolute():
            full_path = self.storage.root / path

        if full_path.is_file():
            files_to_push = [str(full_path.relative_to(self.storage.root))]
        elif full_path.is_dir():
            files_to_push = []
            for file_path in full_path.rglob(f"*{self.storage.CONTENT_EXTENSION}"):
                if self.storage.METADATA_DIR not in str(file_path):
                    files_to_push.append(
                        str(file_path.relative_to(self.storage.root)))
        else:
            raise ValueError(f"Path not found: {path}")

        pushed = 0
        skipped = 0
        conflicts = []
        errors = []

        for local_path in files_to_push:
            try:
                log(f"Checking: {local_path}")

                # Get diff status
                diff_result = self._diff_file(local_path)

                if not diff_result:
                    log(f"  Skipped (not tracked)")
                    skipped += 1
                    continue

                if diff_result.status == DiffStatus.UNCHANGED:
                    log(f"  Skipped (no changes)")
                    skipped += 1
                    continue

                if diff_result.status == DiffStatus.LOCAL_ONLY:
                    log(f"  Skipped (not tracked - use 'create' to add new pages)")
                    skipped += 1
                    continue

                if diff_result.status == DiffStatus.CONFLICT and not force:
                    log(f"  Conflict detected! Use --force to override or resolve first")
                    conflicts.append({
                        "local_path": local_path,
                        "page_id": diff_result.page_id,
                        "remote_version": diff_result.remote_version,
                        "remote_modified_by": diff_result.remote_modified_by,
                    })
                    continue

                if diff_result.status in [DiffStatus.LOCAL_MODIFIED, DiffStatus.CONFLICT]:
                    # Push changes
                    success, result_msg = self._push_file(
                        local_path,
                        diff_result.page_id,
                        diff_result.remote_version if force else diff_result.local_version,
                        message,
                    )

                    if success:
                        log(f"  Pushed successfully")
                        pushed += 1

                        # Also push any modified attachments
                        att_pushed, att_errors = self._push_attachments(
                            diff_result.page_id, local_path,
                            log=lambda msg: log(msg))
                        if att_pushed:
                            log(f"  Pushed {att_pushed} attachment(s)")
                    else:
                        log(f"  Error: {result_msg}")
                        errors.append(
                            {"local_path": local_path, "error": result_msg})
                else:
                    log(f"  Skipped (status: {diff_result.status.value})")
                    skipped += 1

            except Exception as e:
                errors.append({"local_path": local_path, "error": str(e)})
                log(f"  Error: {e}")

        return {
            "pushed": pushed,
            "skipped": skipped,
            "conflicts": conflicts,
            "errors": errors,
            "total": len(files_to_push),
        }

    def _push_file(
        self,
        local_path: str,
        page_id: str,
        current_version: int,
        message: str = None,
    ) -> Tuple[bool, str]:
        """Push a single file to Confluence."""
        # Read local content
        local_content = self.storage.read_local_content(local_path)
        if local_content is None:
            return False, "Could not read local file"

        # Get current page info for title
        metadata = self.storage.get_page_metadata(page_id)
        if not metadata:
            return False, "Page metadata not found"

        # Convert markdown to xhtml if using markdown format
        content_format = self.storage.get_content_format()
        if content_format == self.storage.FORMAT_MARKDOWN:
            macro_store = self.storage.get_macro_store(page_id)
            content = markdown_to_xhtml(local_content, macro_store)
        else:
            content = local_content

        # Create backup before pushing
        self.storage.create_backup(page_id)

        try:
            # Update page
            updated = self.client.update_page(
                page_id=page_id,
                title=metadata.title,
                content=content,
                version=current_version,
                message=message,
            )

            # Update local metadata
            new_version = updated.get("version", {}).get(
                "number", current_version + 1)
            metadata.version = new_version
            metadata.content_hash = hashlib.sha256(
                local_content.encode('utf-8')).hexdigest()
            metadata.last_modified = updated.get("version", {}).get("when", "")
            metadata.last_modified_by = updated.get(
                "version", {}).get("by", {}).get("displayName", "")

            self.storage.save_page(
                local_content, metadata, str(Path(local_path).parent))

            # Push labels if stored in metadata
            if metadata.labels is not None:
                self.client.set_labels(page_id, metadata.labels)

            return True, f"Updated to version {new_version}"

        except Exception as e:
            return False, str(e)

    # ========== CONFLICT RESOLUTION ==========

    def resolve_conflict(
        self,
        local_path: str,
        resolution: str,  # 'local', 'remote', 'merge'
    ) -> Tuple[bool, str]:
        """
        Resolve a conflict between local and remote versions.

        Args:
            local_path: Path to the conflicting file
            resolution: How to resolve - 'local' (keep local), 'remote' (keep remote), 'merge' (manual)

        Returns:
            Tuple of (success, message)
        """
        diff_result = self._diff_file(local_path)

        if not diff_result:
            return False, "File not found"

        if diff_result.status != DiffStatus.CONFLICT:
            return False, f"No conflict to resolve (status: {diff_result.status.value})"

        if resolution == 'local':
            # Keep local, update metadata to reflect we're overriding remote
            metadata = self.storage.get_page_metadata(diff_result.page_id)
            if metadata:
                metadata.version = diff_result.remote_version  # Update to remote version
                content = self.storage.read_local_content(local_path)
                self.storage.save_page(
                    content, metadata, str(Path(local_path).parent))
            return True, "Resolved: keeping local version (push to update Confluence)"

        elif resolution == 'remote':
            # Pull remote version
            success, msg = self.pull_single(diff_result.page_id, force=True)
            return success, f"Resolved: updated to remote version - {msg}"

        elif resolution == 'merge':
            # Create merge file for manual resolution
            if diff_result.local_content and diff_result.remote_content:
                merged = self._create_merge_file(
                    diff_result.local_content,
                    diff_result.remote_content,
                    local_path,
                )
                return True, f"Created merge file: {merged}. Edit and then use 'resolve --accept' to complete."

        return False, f"Unknown resolution strategy: {resolution}"

    def _create_merge_file(
        self,
        local_content: str,
        remote_content: str,
        local_path: str,
    ) -> str:
        """Create a file with merge conflict markers."""
        merge_content = []

        local_lines = local_content.splitlines()
        remote_lines = remote_content.splitlines()

        # Use difflib to find differences
        matcher = difflib.SequenceMatcher(None, remote_lines, local_lines)

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                merge_content.extend(remote_lines[i1:i2])
            elif tag == 'replace':
                merge_content.append("<<<<<<< LOCAL")
                merge_content.extend(local_lines[j1:j2])
                merge_content.append("=======")
                merge_content.extend(remote_lines[i1:i2])
                merge_content.append(">>>>>>> REMOTE")
            elif tag == 'delete':
                merge_content.append("<<<<<<< LOCAL")
                merge_content.append("=======")
                merge_content.extend(remote_lines[i1:i2])
                merge_content.append(">>>>>>> REMOTE")
            elif tag == 'insert':
                merge_content.append("<<<<<<< LOCAL")
                merge_content.extend(local_lines[j1:j2])
                merge_content.append("=======")
                merge_content.append(">>>>>>> REMOTE")

        # Write merge file
        merge_path = Path(local_path).with_suffix('.merge.xhtml')
        full_path = self.storage.root / merge_path

        with open(full_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(merge_content))

        return str(merge_path)

    # ========== STATUS ==========

    def status(self) -> Dict[str, Any]:
        """Get overall sync status."""
        if not self.storage.is_initialized():
            return {"initialized": False}

        config = self.storage.get_config()
        index = self.storage.get_index()

        # Quick status check
        tracked_pages = len(index.get("pages", {}))

        # Check for modifications
        diffs = self.diff()

        unchanged = sum(1 for d in diffs if d.status == DiffStatus.UNCHANGED)
        local_modified = sum(1 for d in diffs if d.status ==
                             DiffStatus.LOCAL_MODIFIED)
        remote_modified = sum(1 for d in diffs if d.status ==
                              DiffStatus.REMOTE_MODIFIED)
        conflicts = sum(1 for d in diffs if d.status == DiffStatus.CONFLICT)
        local_only = sum(1 for d in diffs if d.status == DiffStatus.LOCAL_ONLY)

        # Count tracked attachments
        tracked_attachments = sum(
            len(atts) for atts in index.get("attachments", {}).values()
        )

        # Count pages with labels
        pages_with_labels = 0
        total_labels = 0
        for page_id in index.get("pages", {}):
            meta = self.storage.get_page_metadata(page_id)
            if meta and meta.labels:
                pages_with_labels += 1
                total_labels += len(meta.labels)

        return {
            "initialized": True,
            "root_path": str(self.storage.root),
            "target_url": config.get("target_url"),
            "space_key": config.get("space_key"),
            "tracked_pages": tracked_pages,
            "tracked_attachments": tracked_attachments,
            "pages_with_labels": pages_with_labels,
            "total_labels": total_labels,
            "unchanged": unchanged,
            "local_modified": local_modified,
            "remote_modified": remote_modified,
            "conflicts": conflicts,
            "untracked": local_only,
        }

    # ===================================================================
    # CREATE OPERATIONS
    # ===================================================================

    def create_new_page(
        self,
        local_path: str,
        title: Optional[str] = None,
        parent_path: Optional[str] = None,
        message: Optional[str] = None,
    ) -> Tuple[bool, str, Optional[str]]:
        """Create a new page in Confluence from a local file.

        Args:
            local_path: Path to the local .md file (absolute or relative to storage root).
            title: Page title — defaults to filename stem with underscores as spaces.
            parent_path: Path to parent page file (absolute or relative to storage root).
                         When omitted, the parent is inferred from sibling pages in the
                         same directory, or from ``root_page_id`` in the repo config.
            message: Optional version message (currently unused by the API wrapper).

        Returns:
            A 3-tuple of ``(success, message, page_url)``.
        """
        # Normalise path to absolute
        full_path = Path(local_path)
        if not full_path.is_absolute():
            full_path = self.storage.root / local_path
        try:
            rel_path = str(full_path.relative_to(self.storage.root))
        except ValueError:
            return False, f"Path is outside the repository root: {local_path}", None

        # Guard: already tracked
        if self.storage.get_page_by_path(rel_path):
            return False, "File is already tracked in the index. Use 'push' to update it.", None

        # Derive title from filename if not supplied
        if not title:
            title = full_path.stem.replace("_", " ")

        # Read content — create a minimal stub if the file doesn't exist yet
        if not full_path.exists():
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(f"# {title}\n\n", encoding="utf-8")
        local_content = full_path.read_text(encoding="utf-8")

        # ----------------------------------------------------------------
        # Resolve parent_id
        # ----------------------------------------------------------------
        parent_id: Optional[str] = None

        if parent_path:
            # Explicit parent: look it up by path in the index
            parent_full = Path(parent_path)
            if not parent_full.is_absolute():
                parent_full = self.storage.root / parent_path
            try:
                parent_rel = str(parent_full.relative_to(self.storage.root))
            except ValueError:
                parent_rel = parent_path
            parent_meta = self.storage.get_page_by_path(parent_rel)
            if not parent_meta:
                return False, f"Parent page not found in index: {parent_path}", None
            parent_id = parent_meta.page_id
        else:
            # Discover parent by looking for a sibling (same directory) in the index
            file_dir = str(Path(rel_path).parent)
            index = self.storage.get_index()
            for pid, info in index.get("pages", {}).items():
                sibling_dir = str(Path(info["local_path"]).parent)
                if sibling_dir == file_dir:
                    meta = self.storage.get_page_metadata(pid)
                    if meta and meta.parent_id:
                        parent_id = meta.parent_id
                        break

            # Fall back to the root page of the repo
            if not parent_id:
                config = self.storage.get_config()
                parent_id = config.get("root_page_id") if config else None

        if not parent_id:
            return False, (
                "Could not determine a parent page ID. "
                "Pass --parent to specify one explicitly."
            ), None

        # ----------------------------------------------------------------
        # Gather space key
        # ----------------------------------------------------------------
        config = self.storage.get_config()
        if not config:
            return False, "Repository config not found.", None
        space_key = config.get("space_key", "")
        if not space_key:
            return False, "No space_key found in repository config.", None

        # ----------------------------------------------------------------
        # Convert content to XHTML for the Confluence API
        # ----------------------------------------------------------------
        content_format = self.storage.get_content_format()
        if content_format == self.storage.FORMAT_MARKDOWN:
            xhtml = markdown_to_xhtml(local_content, {})
        else:
            xhtml = local_content

        # ----------------------------------------------------------------
        # Call the API
        # ----------------------------------------------------------------
        try:
            result = self.client.create_page(
                space_key=space_key,
                title=title,
                content=xhtml,
                parent_id=parent_id,
            )
        except Exception as exc:
            return False, f"Confluence API error: {exc}", None

        new_page_id = result["id"]
        new_version = result.get("version", {}).get("number", 1)
        web_url = (
            result.get("_links", {}).get("base", "")
            + result.get("_links", {}).get("webui", "")
        )

        # ----------------------------------------------------------------
        # Persist metadata locally
        # ----------------------------------------------------------------
        metadata = PageMetadata(
            page_id=new_page_id,
            title=title,
            space_key=space_key,
            version=new_version,
            last_modified=result.get("version", {}).get("when", ""),
            last_modified_by=(
                result.get("version", {}).get("by", {}).get("displayName", "")
            ),
            parent_id=parent_id,
            web_url=web_url,
            labels=[],
        )

        dir_path = str(Path(rel_path).parent)
        self.storage.save_page(
            local_content,
            metadata,
            dir_path if dir_path != "." else "",
        )

        return True, f"Created page '{title}' (ID: {new_page_id})", web_url or None
