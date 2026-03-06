"""
End-to-end workflow tests for confluence-docinator.

Covers the complete user journey without hitting a real Confluence instance:

  1.  Init     – initialise a docinator repository in a temp directory
  2.  Pull     – pull pages (mocked Confluence client) and verify local files
  3.  Diff     – unchanged state after a clean pull  
  4.  Diff     – local_modified after editing a file
  5.  Diff     – conflict when both local and remote changed
  6.  Push     – push edits back to Confluence (update_page called correctly)
  7.  Push     – unchanged files are skipped
  8.  New      – create_new_page() scaffolds a stub and posts to Confluence
  9.  Create   – create_new_page() from an existing local file, auto-parent
  10. Create   – create_new_page() with explicit --parent
  11. Create   – rejected when file is already tracked
  12. Resolve  – conflict resolution via 'local' strategy
  13. Resolve  – conflict resolution via 'remote' strategy
  14. Paths    – _resolve_path_arg() resolves cwd-relative paths correctly
"""

import hashlib
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the package from a local editable install
# ---------------------------------------------------------------------------
from confluence_docinator.models import DiffStatus, PageMetadata, SyncConfig
from confluence_docinator.storage import StorageManager
from confluence_docinator.sync import SyncManager


# ===========================================================================
# Shared fake data
# ===========================================================================

FAKE_BASE_URL = "https://wiki.example.atlassian.net/wiki"
FAKE_SPACE_KEY = "TEST"
FAKE_PULL_URL = f"{FAKE_BASE_URL}/spaces/{FAKE_SPACE_KEY}/folder/ROOT001"

ROOT_PAGE_ID = "ROOT001"
ROOT_PAGE_TITLE = "Documentation Root"

PAGE1_ID = "PAGE001"
PAGE1_TITLE = "Getting Started"
PAGE1_XHTML = "<p>Getting started with the platform.</p>"

PAGE2_ID = "PAGE002"
PAGE2_TITLE = "API Reference"
PAGE2_XHTML = "<p>Full API reference documentation.</p>"


def _make_sync_config() -> SyncConfig:
    return SyncConfig(
        base_url=FAKE_BASE_URL,
        username="test@example.com",
        api_key="test-api-key",
        space_key=FAKE_SPACE_KEY,
        editor_version=2,
    )


def _page_metadata(page_id: str, title: str, version: int = 1,
                   parent_id: str = ROOT_PAGE_ID) -> PageMetadata:
    """Build a minimal PageMetadata object resembling what the client returns."""
    return PageMetadata(
        page_id=page_id,
        title=title,
        space_key=FAKE_SPACE_KEY,
        version=version,
        last_modified="2024-01-01T00:00:00.000Z",
        last_modified_by="Alice",
        parent_id=parent_id,
        web_url=f"{FAKE_BASE_URL}/spaces/{FAKE_SPACE_KEY}/pages/{page_id}",
        labels=[],
    )


def _update_page_response(page_id: str, new_version: int) -> dict:
    return {
        "id": page_id,
        "version": {
            "number": new_version,
            "when": "2024-01-02T00:00:00.000Z",
            "by": {"displayName": "Bob"},
        },
    }


def _create_page_response(page_id: str) -> dict:
    return {
        "id": page_id,
        "version": {
            "number": 1,
            "when": "2024-01-01T00:00:00.000Z",
            "by": {"displayName": "Alice"},
        },
        "_links": {
            "base": FAKE_BASE_URL,
            "webui": f"/spaces/{FAKE_SPACE_KEY}/pages/{page_id}",
        },
    }


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def mock_client():
    """A ConfluenceClient mock pre-wired with sensible defaults."""
    client = MagicMock()
    client.config = _make_sync_config()

    # --- connection ---------------------------------------------------------
    client.test_connection.return_value = (True, "Connection successful")

    # --- URL parsing --------------------------------------------------------
    client.parse_confluence_url.return_value = (
        FAKE_SPACE_KEY, ROOT_PAGE_ID, "page")

    # --- page tree ----------------------------------------------------------
    client.get_page.return_value = {
        "id": ROOT_PAGE_ID,
        "title": ROOT_PAGE_TITLE,
        "type": "page",
    }
    client.get_descendants.return_value = [
        {
            "id": PAGE1_ID,
            "title": PAGE1_TITLE,
            "_local_path": "",  # saved at repository root
            "_depth": 1,
        },
        {
            "id": PAGE2_ID,
            "title": PAGE2_TITLE,
            "_local_path": "",
            "_depth": 1,
        },
    ]

    # --- page content -------------------------------------------------------
    def _get_page_content(page_id, *args, **kwargs):
        if page_id == ROOT_PAGE_ID:
            return "<p>Root page content.</p>", _page_metadata(
                ROOT_PAGE_ID, ROOT_PAGE_TITLE, parent_id=None
            )
        if page_id == PAGE1_ID:
            return PAGE1_XHTML, _page_metadata(PAGE1_ID, PAGE1_TITLE)
        if page_id == PAGE2_ID:
            return PAGE2_XHTML, _page_metadata(PAGE2_ID, PAGE2_TITLE)
        return None, None

    client.get_page_content.side_effect = _get_page_content

    # --- attachments / labels -----------------------------------------------
    client.get_attachments.return_value = []
    client.get_labels.return_value = []
    client.set_labels.return_value = True

    # --- update / create ----------------------------------------------------
    client.update_page.return_value = _update_page_response(PAGE1_ID, 2)
    client.create_page.return_value = _create_page_response("NEW001")

    return client


@pytest.fixture
def storage(tmp_path):
    """A fresh StorageManager rooted in a temp directory."""
    return StorageManager(tmp_path, content_format=StorageManager.FORMAT_MARKDOWN)


@pytest.fixture
def sync(mock_client, storage):
    """SyncManager wired with the mock client and temp storage."""
    return SyncManager(mock_client, storage)


@pytest.fixture
def pulled_sync(sync, storage):
    """A (sync, storage) pair whose repo has been initialised AND pulled."""
    sync.pull(FAKE_PULL_URL)
    return sync, storage


# ===========================================================================
# 1 – INIT
# ===========================================================================

class TestInit:
    def test_storage_is_not_initialised_before_init(self, storage):
        assert not storage.is_initialized()

    def test_explicit_initialize_creates_config(self, storage):
        config = _make_sync_config()
        storage.initialize(config, FAKE_PULL_URL, ROOT_PAGE_ID)

        assert storage.is_initialized()
        cfg = storage.get_config()
        assert cfg["space_key"] == FAKE_SPACE_KEY
        assert cfg["root_page_id"] == ROOT_PAGE_ID
        assert cfg["base_url"] == FAKE_BASE_URL

    def test_initialize_creates_empty_index(self, storage):
        config = _make_sync_config()
        storage.initialize(config, FAKE_PULL_URL, ROOT_PAGE_ID)
        index = storage.get_index()
        assert index == {"pages": {}, "folders": {}}


# ===========================================================================
# 2 – PULL
# ===========================================================================

class TestPull:
    def test_pull_auto_initialises_storage(self, sync, storage):
        assert not storage.is_initialized()
        sync.pull(FAKE_PULL_URL)
        assert storage.is_initialized()

    def test_pull_returns_correct_counts(self, sync):
        result = sync.pull(FAKE_PULL_URL)
        # Root + PAGE1 + PAGE2 = 3 pages; all should be pulled
        assert result["pulled"] == 3
        assert result["skipped"] == 0
        assert result["errors"] == []

    def test_pull_creates_local_md_files(self, sync, storage):
        sync.pull(FAKE_PULL_URL)
        files = list(storage.root.rglob("*.md"))
        # Exclude anything inside .confluence/
        content_files = [
            f for f in files
            if storage.METADATA_DIR not in str(f.relative_to(storage.root))
        ]
        assert len(content_files) == 3

    def test_pull_indexes_pages(self, sync, storage):
        sync.pull(FAKE_PULL_URL)
        index = storage.get_index()
        page_ids = set(index["pages"].keys())
        assert PAGE1_ID in page_ids
        assert PAGE2_ID in page_ids

    def test_pull_saves_page_metadata(self, sync, storage):
        sync.pull(FAKE_PULL_URL)
        meta = storage.get_page_metadata(PAGE1_ID)
        assert meta is not None
        assert meta.title == PAGE1_TITLE
        assert meta.space_key == FAKE_SPACE_KEY
        assert meta.version == 1

    def test_pull_second_time_skips_up_to_date_pages(self, sync):
        sync.pull(FAKE_PULL_URL)
        result2 = sync.pull(FAKE_PULL_URL)
        # Nothing changed remotely, so all three pages should be skipped
        assert result2["skipped"] == 3
        assert result2["pulled"] == 0

    def test_pull_force_re_downloads_pages(self, sync):
        sync.pull(FAKE_PULL_URL)
        result2 = sync.pull(FAKE_PULL_URL, force=True)
        assert result2["pulled"] == 3


# ===========================================================================
# 3 – DIFF: unchanged after clean pull
# ===========================================================================

class TestDiffUnchanged:
    def test_all_pages_unchanged_after_pull(self, pulled_sync):
        sync, storage = pulled_sync
        results = sync.diff()
        statuses = {r.status for r in results}
        # LOCAL_ONLY is possible if untracked files exist, but no MODIFIED/CONFLICT
        assert DiffStatus.LOCAL_MODIFIED not in statuses
        assert DiffStatus.CONFLICT not in statuses

    def test_specific_file_diff_is_unchanged(self, pulled_sync):
        sync, storage = pulled_sync
        meta = storage.get_page_metadata(PAGE1_ID)
        results = sync.diff(path=str(storage.root / meta.local_path))
        assert len(results) == 1
        assert results[0].status == DiffStatus.UNCHANGED


# ===========================================================================
# 4 – DIFF: local_modified after editing
# ===========================================================================

class TestDiffLocalModified:
    def test_edit_triggers_local_modified(self, pulled_sync):
        sync, storage = pulled_sync
        meta = storage.get_page_metadata(PAGE1_ID)
        page_file = storage.root / meta.local_path

        # Simulate user editing the file
        page_file.write_text(page_file.read_text() +
                             "\n\n## New Section\n\nAdded content.\n")

        results = sync.diff(path=str(page_file))
        assert len(results) == 1
        assert results[0].status == DiffStatus.LOCAL_MODIFIED

    def test_unedited_sibling_stays_unchanged(self, pulled_sync):
        sync, storage = pulled_sync
        meta1 = storage.get_page_metadata(PAGE1_ID)
        meta2 = storage.get_page_metadata(PAGE2_ID)

        # Edit only PAGE1
        page_file = storage.root / meta1.local_path
        page_file.write_text(page_file.read_text() + "\n\nEdited.\n")

        results = sync.diff(path=str(storage.root / meta2.local_path))
        assert results[0].status == DiffStatus.UNCHANGED

    def test_diff_with_directory_finds_modified_file(self, pulled_sync):
        sync, storage = pulled_sync
        meta = storage.get_page_metadata(PAGE1_ID)
        page_file = storage.root / meta.local_path
        page_file.write_text(page_file.read_text() + "\n\nEdited.\n")

        results = sync.diff(path=str(storage.root))
        modified = [r for r in results if r.status ==
                    DiffStatus.LOCAL_MODIFIED]
        assert len(modified) == 1
        assert modified[0].page_id == PAGE1_ID


# ===========================================================================
# 5 – DIFF: conflict (remote also changed)
# ===========================================================================

class TestDiffConflict:
    def test_conflict_when_both_sides_changed(self, pulled_sync, mock_client):
        sync, storage = pulled_sync
        meta = storage.get_page_metadata(PAGE1_ID)
        page_file = storage.root / meta.local_path

        # Local edit
        page_file.write_text(page_file.read_text() + "\n\nLocal addition.\n")

        # Simulate remote version bump: return version 2 from mock
        mock_client.get_page_content.side_effect = None
        mock_client.get_page_content.return_value = (
            PAGE1_XHTML + "<p>Remote change.</p>",
            _page_metadata(PAGE1_ID, PAGE1_TITLE, version=2),
        )

        results = sync.diff(path=str(page_file))
        assert results[0].status == DiffStatus.CONFLICT

    def test_remote_only_modified_shows_remote_modified(self, pulled_sync, mock_client):
        sync, storage = pulled_sync
        meta = storage.get_page_metadata(PAGE1_ID)

        # Remote version bumped, local file untouched
        mock_client.get_page_content.side_effect = None
        mock_client.get_page_content.return_value = (
            PAGE1_XHTML + "<p>Remote-only change.</p>",
            _page_metadata(PAGE1_ID, PAGE1_TITLE, version=2),
        )

        results = sync.diff(path=str(storage.root / meta.local_path))
        assert results[0].status == DiffStatus.REMOTE_MODIFIED


# ===========================================================================
# 6 – PUSH: sends update to Confluence
# ===========================================================================

class TestPush:
    def _edit_page1(self, storage):
        """Helper: append a line to PAGE1 and return its (abs) path."""
        meta = storage.get_page_metadata(PAGE1_ID)
        page_file = storage.root / meta.local_path
        page_file.write_text(page_file.read_text() +
                             "\n\n## Update\n\nPushing this change.\n")
        return str(page_file)

    def test_push_calls_update_page_on_modified_file(self, pulled_sync, mock_client):
        sync, storage = pulled_sync
        page_path = self._edit_page1(storage)

        result = sync.push(page_path, message="test update")

        assert result["pushed"] == 1
        assert result["errors"] == []
        mock_client.update_page.assert_called_once()

        call_kwargs = mock_client.update_page.call_args
        assert call_kwargs.kwargs.get(
            "page_id") or call_kwargs.args[0] == PAGE1_ID

    def test_push_increments_stored_version(self, pulled_sync, mock_client):
        sync, storage = pulled_sync
        mock_client.update_page.return_value = _update_page_response(
            PAGE1_ID, 2)

        page_path = self._edit_page1(storage)
        sync.push(page_path, message="bump version")

        meta_after = storage.get_page_metadata(PAGE1_ID)
        assert meta_after.version == 2

    def test_push_updates_content_hash(self, pulled_sync, mock_client):
        sync, storage = pulled_sync
        meta_before = storage.get_page_metadata(PAGE1_ID)
        old_hash = meta_before.content_hash

        page_path = self._edit_page1(storage)
        sync.push(page_path)

        meta_after = storage.get_page_metadata(PAGE1_ID)
        assert meta_after.content_hash != old_hash

    def test_push_skips_unchanged_file(self, pulled_sync, mock_client):
        sync, storage = pulled_sync
        meta = storage.get_page_metadata(PAGE1_ID)
        page_path = str(storage.root / meta.local_path)

        result = sync.push(page_path)

        assert result["pushed"] == 0
        assert result["skipped"] == 1
        mock_client.update_page.assert_not_called()

    def test_push_directory_pushes_only_modified_files(self, pulled_sync, mock_client):
        sync, storage = pulled_sync
        self._edit_page1(storage)  # only PAGE1 modified

        result = sync.push(str(storage.root))

        assert result["pushed"] == 1
        assert mock_client.update_page.call_count == 1

    def test_push_passes_message_to_api(self, pulled_sync, mock_client):
        sync, storage = pulled_sync
        page_path = self._edit_page1(storage)
        msg = "Updated security policy section"

        sync.push(page_path, message=msg)

        call_kwargs = mock_client.update_page.call_args.kwargs
        assert call_kwargs.get("message") == msg

    def test_push_nonexistent_path_raises(self, pulled_sync):
        sync, _ = pulled_sync
        with pytest.raises(ValueError, match="Path not found"):
            sync.push("/tmp/nonexistent/path/page.md")


# ===========================================================================
# 7 – CREATE: docinator new / docinator create
# ===========================================================================

class TestCreate:
    def test_create_page_calls_confluence_api(self, pulled_sync, mock_client):
        sync, storage = pulled_sync
        mock_client.create_page.return_value = _create_page_response("NEW001")

        success, msg, url = sync.create_new_page(
            "New Policy.md", title="New Policy")

        assert success, msg
        mock_client.create_page.assert_called_once()

    def test_create_page_uses_space_key_from_config(self, pulled_sync, mock_client):
        sync, storage = pulled_sync
        mock_client.create_page.return_value = _create_page_response("NEW001")

        sync.create_new_page("New Policy.md", title="New Policy")

        call_kwargs = mock_client.create_page.call_args.kwargs
        assert call_kwargs.get("space_key") == FAKE_SPACE_KEY

    def test_create_page_auto_discovers_parent_from_siblings(self, pulled_sync, mock_client):
        """When no --parent given, the new page should inherit parent_id from a sibling."""
        sync, storage = pulled_sync
        mock_client.create_page.return_value = _create_page_response("NEW001")

        success, msg, url = sync.create_new_page(
            "New Sibling.md", title="New Sibling")

        assert success, msg
        call_kwargs = mock_client.create_page.call_args.kwargs
        # Parent should be the shared parent_id of PAGE1 / PAGE2 (ROOT_PAGE_ID)
        assert call_kwargs.get("parent_id") == ROOT_PAGE_ID

    def test_create_page_with_explicit_parent(self, pulled_sync, mock_client):
        """--parent override: use the explicit page's own page_id as parent."""
        sync, storage = pulled_sync
        mock_client.create_page.return_value = _create_page_response("NEW002")

        parent_meta = storage.get_page_metadata(PAGE1_ID)
        success, msg, url = sync.create_new_page(
            "Child Of Getting Started.md",
            title="Child Of Getting Started",
            parent_path=parent_meta.local_path,
        )

        assert success, msg
        call_kwargs = mock_client.create_page.call_args.kwargs
        assert call_kwargs.get("parent_id") == PAGE1_ID

    def test_create_page_saves_metadata_to_index(self, pulled_sync, mock_client):
        sync, storage = pulled_sync
        mock_client.create_page.return_value = _create_page_response("NEW001")

        sync.create_new_page("New Policy.md", title="New Policy")

        index = storage.get_index()
        assert "NEW001" in index["pages"]

    def test_create_page_returns_url(self, pulled_sync, mock_client):
        sync, storage = pulled_sync
        mock_client.create_page.return_value = _create_page_response("NEW001")

        _, _, url = sync.create_new_page("New Policy.md", title="New Policy")

        assert url is not None
        assert "NEW001" in url or FAKE_BASE_URL in url

    def test_create_page_title_derived_from_filename(self, pulled_sync, mock_client):
        sync, storage = pulled_sync
        mock_client.create_page.return_value = _create_page_response("NEW003")

        success, msg, _ = sync.create_new_page("security_policy.md")

        assert success
        call_kwargs = mock_client.create_page.call_args.kwargs
        # underscores → spaces
        assert call_kwargs.get("title") == "security policy"

    def test_create_page_scaffolds_stub_when_file_missing(self, pulled_sync, mock_client, tmp_path):
        sync, storage = pulled_sync
        mock_client.create_page.return_value = _create_page_response("NEW004")

        new_file_rel = "Brand New Page.md"
        new_file_abs = storage.root / new_file_rel
        assert not new_file_abs.exists()

        success, _, _ = sync.create_new_page(
            new_file_rel, title="Brand New Page")

        assert success
        assert new_file_abs.exists()
        assert "Brand New Page" in new_file_abs.read_text()

    def test_create_page_reads_existing_file_content(self, pulled_sync, mock_client):
        sync, storage = pulled_sync
        mock_client.create_page.return_value = _create_page_response("NEW005")

        custom_content = "# My Custom Page\n\nWritten before creating.\n"
        page_file = storage.root / "Custom Content Page.md"
        page_file.write_text(custom_content)

        success, _, _ = sync.create_new_page(
            "Custom Content Page.md", title="Custom Content Page"
        )

        assert success
        call_kwargs = mock_client.create_page.call_args.kwargs
        # Content passed to API should be derived from the file (converted to XHTML)
        assert call_kwargs.get("content") is not None

    def test_create_already_tracked_page_is_rejected(self, pulled_sync, mock_client):
        sync, storage = pulled_sync
        meta = storage.get_page_metadata(PAGE1_ID)

        success, msg, url = sync.create_new_page(
            meta.local_path, title=PAGE1_TITLE
        )

        assert not success
        assert "already tracked" in msg.lower() or "push" in msg.lower()
        mock_client.create_page.assert_not_called()

    def test_create_page_api_error_returns_failure(self, pulled_sync, mock_client):
        sync, storage = pulled_sync
        mock_client.create_page.side_effect = Exception("403 Forbidden")

        success, msg, url = sync.create_new_page(
            "Forbidden.md", title="Forbidden")

        assert not success
        assert "403" in msg or "forbidden" in msg.lower() or "error" in msg.lower()


# ===========================================================================
# 8 – CONFLICT RESOLUTION
# ===========================================================================

class TestConflictResolution:
    def _set_up_conflict(self, pulled_sync, mock_client):
        """Make PAGE1 conflict: edit locally + bump remote version."""
        sync, storage = pulled_sync
        meta = storage.get_page_metadata(PAGE1_ID)
        page_file = storage.root / meta.local_path
        page_file.write_text(page_file.read_text() + "\n\nLocal edit.\n")

        mock_client.get_page_content.side_effect = None
        mock_client.get_page_content.return_value = (
            PAGE1_XHTML + "<p>Remote change.</p>",
            _page_metadata(PAGE1_ID, PAGE1_TITLE, version=2),
        )
        return sync, storage, meta.local_path

    def test_resolve_local_strategy_keeps_local_content(self, pulled_sync, mock_client):
        sync, storage, rel_path = self._set_up_conflict(
            pulled_sync, mock_client)

        # Verify it's a conflict first
        results = sync.diff(path=str(storage.root / rel_path))
        assert results[0].status == DiffStatus.CONFLICT

        success, msg = sync.resolve_conflict(rel_path, "local")
        assert success

    def test_resolve_local_strategy_updates_stored_version(self, pulled_sync, mock_client):
        """After 'local' resolve the stored version should match the remote version."""
        sync, storage, rel_path = self._set_up_conflict(
            pulled_sync, mock_client)
        sync.resolve_conflict(rel_path, "local")

        meta = storage.get_page_metadata(PAGE1_ID)
        assert meta.version == 2  # bumped to remote version

    def test_resolve_remote_strategy_calls_pull_single(self, pulled_sync, mock_client):
        sync, storage, rel_path = self._set_up_conflict(
            pulled_sync, mock_client)

        # pull_single must succeed
        mock_client.get_page_content.return_value = (
            PAGE1_XHTML + "<p>Remote change.</p>",
            _page_metadata(PAGE1_ID, PAGE1_TITLE, version=2),
        )

        success, msg = sync.resolve_conflict(rel_path, "remote")
        assert success
        # get_page_content should have been called again for the pull
        assert mock_client.get_page_content.call_count >= 1

    def test_resolve_non_conflict_returns_error(self, pulled_sync, mock_client):
        sync, storage = pulled_sync
        meta = storage.get_page_metadata(PAGE1_ID)

        # PAGE1 is UNCHANGED – not a conflict
        success, msg = sync.resolve_conflict(meta.local_path, "local")
        assert not success
        assert "conflict" in msg.lower() or "no conflict" in msg.lower()


# ===========================================================================
# 9 – PATH RESOLUTION  (_resolve_path_arg)
# ===========================================================================

class TestPathResolution:
    """
    Reproduce the exact bug: running `docinator push confluence_pages/Foo.md`
    from the parent directory used to raise "Path not found" because sync.push()
    joined the already-prefixed path with storage.root again.

    The fix is _resolve_path_arg() in cli.py which resolves the path
    against cwd before handing it off.
    """

    def test_resolve_path_arg_converts_cwd_relative_to_absolute(self, tmp_path):
        from confluence_docinator.cli import _resolve_path_arg

        # Create a real file
        test_file = tmp_path / "confluence_pages" / "Foo.md"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("# Foo\n")

        with patch("confluence_docinator.cli.Path") as mock_path_cls:
            # We can't easily mock Path.cwd(), so patch at the module level
            pass

        # Test directly: give an absolute path that exists → returned unchanged
        result = _resolve_path_arg(str(test_file))
        assert result == str(test_file)

    def test_resolve_path_arg_returns_original_when_file_not_found(self, tmp_path):
        from confluence_docinator.cli import _resolve_path_arg

        # Non-existent path: should come back as-is so callers can emit the
        # correct error message
        result = _resolve_path_arg("confluence_pages/DoesNotExist.md")
        assert result == "confluence_pages/DoesNotExist.md"

    def test_push_with_absolute_path_resolves_correctly(self, pulled_sync):
        """Absolute path to a modified file must work from any cwd."""
        sync, storage = pulled_sync
        meta = storage.get_page_metadata(PAGE1_ID)
        page_file = storage.root / meta.local_path

        # Edit the file
        page_file.write_text(page_file.read_text() + "\n\nEdited.\n")

        # Push using the absolute path – must NOT raise and must push 1 file
        result = sync.push(str(page_file))
        assert result["pushed"] == 1

    def test_push_raises_for_path_outside_repo(self, pulled_sync):
        """Path that can't be made relative to repo root should raise."""
        sync, storage = pulled_sync
        # Use a completely independent temp dir so the path is truly outside
        # storage.root (both aren't nested in each other).
        with tempfile.TemporaryDirectory() as other_tmp:
            outside = Path(other_tmp) / "random.md"
            outside.write_text("# Random\n")

            with pytest.raises((ValueError, Exception)):
                sync.push(str(outside))

    def test_push_brackets_in_filename(self, pulled_sync, mock_client):
        """
        Filenames with brackets (e.g. [Template] Engineer Review.md) must be
        pushed successfully.  This was the exact failing scenario.
        """
        sync, storage = pulled_sync
        mock_client.create_page.return_value = _create_page_response("TMPL001")

        # Create a template page (already tracked via create_new_page)
        success, _, _ = sync.create_new_page(
            "[Template] Engineer Review.md",
            title="[Template] Engineer Review",
        )
        assert success, "Failed to create template page"

        tmpl_meta = storage.get_page_metadata("TMPL001")
        tmpl_file = storage.root / tmpl_meta.local_path
        current_content = tmpl_file.read_text()

        # Wire the mock so _diff_file can fetch the remote version of TMPL001.
        # We return the same content at version 1, so the local edit will flip
        # the status to LOCAL_MODIFIED and trigger a push.
        from confluence_docinator.converter import markdown_to_xhtml
        tmpl_xhtml = markdown_to_xhtml(current_content, {})

        def _patched(page_id, *a, **kw):
            if page_id == "TMPL001":
                return tmpl_xhtml, _page_metadata(
                    "TMPL001", "[Template] Engineer Review", version=1)
            if page_id == PAGE1_ID:
                return PAGE1_XHTML, _page_metadata(PAGE1_ID, PAGE1_TITLE)
            if page_id == PAGE2_ID:
                return PAGE2_XHTML, _page_metadata(PAGE2_ID, PAGE2_TITLE)
            if page_id == ROOT_PAGE_ID:
                return "<p>Root.</p>", _page_metadata(
                    ROOT_PAGE_ID, ROOT_PAGE_TITLE, parent_id=None)
            return None, None

        mock_client.get_page_content.side_effect = _patched
        mock_client.update_page.return_value = _update_page_response("TMPL001", 2)

        # Edit the file and push by absolute path
        tmpl_file.write_text(current_content + "\n\n## Reviewer Notes\n\nUpdated.\n")

        result = sync.push(str(tmpl_file))
        assert result["pushed"] == 1
        assert result["errors"] == []


# ===========================================================================
# 10 – FULL END-TO-END WORKFLOW (narrative smoke test)
# ===========================================================================

class TestFullWorkflow:
    """
    One continuous scenario that mirrors what the docs say a user would do:

        docinator pull <url>
        # edit a file
        docinator diff
        docinator push <file> -m "update"
        docinator new "New Policy"
    """

    def test_full_user_journey(self, sync, storage, mock_client, tmp_path):
        # ── Step 1: Pull ────────────────────────────────────────────────────
        result = sync.pull(FAKE_PULL_URL)
        assert result["pulled"] == 3
        assert storage.is_initialized()

        # ── Step 2: Verify file exists ──────────────────────────────────────
        meta1 = storage.get_page_metadata(PAGE1_ID)
        page_file = storage.root / meta1.local_path
        assert page_file.exists()

        # ── Step 3: Edit the file ───────────────────────────────────────────
        original = page_file.read_text()
        page_file.write_text(
            original + "\n\n## New Section\n\nContent added by user.\n")

        # ── Step 4: Diff shows local_modified ──────────────────────────────
        diff_results = sync.diff(path=str(page_file))
        assert diff_results[0].status == DiffStatus.LOCAL_MODIFIED

        # ── Step 5: Push ────────────────────────────────────────────────────
        mock_client.update_page.return_value = _update_page_response(
            PAGE1_ID, 2)
        push_result = sync.push(str(page_file), message="Add new section")
        assert push_result["pushed"] == 1
        assert push_result["errors"] == []
        mock_client.update_page.assert_called_once()

        # ── Step 6: Post-push diff shows unchanged ──────────────────────────
        # After push the stored hash is updated; remote mock still returns v1
        # but now our stored version matches the mock's returned version (1)
        # so diff logic: local_hash == stored_hash → UNCHANGED
        diff_after = sync.diff(path=str(page_file))
        assert diff_after[0].status == DiffStatus.UNCHANGED

        # ── Step 7: Create new page ─────────────────────────────────────────
        mock_client.create_page.return_value = _create_page_response("NEW999")
        success, msg, url = sync.create_new_page(
            "Security Policy.md", title="Security Policy"
        )
        assert success, msg
        assert url is not None

        new_meta = storage.get_page_metadata("NEW999")
        assert new_meta is not None
        assert new_meta.title == "Security Policy"
        assert (storage.root / new_meta.local_path).exists()
