#!/usr/bin/env python3
"""
Confluence Docinator CLI - Sync Confluence pages with local files.

Usage:
    docinator setup
    docinator pull <url> [--force] [--output <dir>]
    docinator push <path> [--message <msg>] [--force]
    docinator diff [<path>] [--recursive] [--git]
    docinator status
    docinator resolve <path> --strategy <local|remote|merge>
    docinator init <url> [--output <dir>]
"""

import os
import sys
import argparse
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlunparse
from dotenv import load_dotenv
try:
    import argcomplete
    from argcomplete.completers import FilesCompleter
    _ARGCOMPLETE = True
except ImportError:
    _ARGCOMPLETE = False

from .models import SyncConfig, DiffStatus
from .client import ConfluenceClient
from .storage import StorageManager
from .sync import SyncManager


# ANSI color codes
class Colors:
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    BOLD = '\033[1m'
    RESET = '\033[0m'


def color(text: str, color_code: str) -> str:
    """Apply color to text if terminal supports it."""
    if sys.stdout.isatty():
        return f"{color_code}{text}{Colors.RESET}"
    return text


def load_config() -> SyncConfig:
    """Load configuration from environment."""
    # Try to load .env from current directory or parent directories
    env_path = Path.cwd()
    for _ in range(5):  # Check up to 5 parent directories
        if (env_path / '.env').exists():
            load_dotenv(env_path / '.env')
            break
        if (env_path / 'example.env').exists() and not (env_path / '.env').exists():
            print(color(
                f"Warning: Found example.env but no .env file. Copy example.env to .env and configure it.", Colors.YELLOW))
        env_path = env_path.parent

    config = SyncConfig(
        base_url=os.getenv("CONFLUENCE_BASE_URL", ""),
        username=os.getenv("CONFLUENCE_USERNAME", ""),
        api_key=os.getenv("CONFLUENCE_API_KEY", ""),
        space_key=os.getenv("CONFLUENCE_SPACE_KEY", ""),
        editor_version=int(os.getenv("CONFLUENCE_EDITOR_VERSION", "2")),
    )

    if not config.base_url or not config.username or not config.api_key:
        print(color("Error: Missing Confluence configuration.", Colors.RED))
        print("Please set the following environment variables or create a .env file:")
        print("  CONFLUENCE_BASE_URL")
        print("  CONFLUENCE_USERNAME")
        print("  CONFLUENCE_API_KEY")
        sys.exit(1)

    return config


def _find_repo_root(start: Path = None) -> Path:
    """Find the docinator repo root (.confluence/config.json) from the given start dir.

    Search order:
      1. start dir itself
      2. confluence_pages/ subdirectory (common convention)
      3. Any other direct subdirectory containing .confluence/
      4. Parent directories (up to 5 levels)

    Returns the repo root Path if found, otherwise the start dir (caller handles
    the "not initialized" error via is_initialized()).
    """
    if start is None:
        start = Path.cwd()

    config_file = ".confluence/config.json"

    # 1. Current directory
    if (start / config_file).exists():
        return start

    # 2. confluence_pages/ subdirectory first (most common)
    candidate = start / "confluence_pages"
    if (candidate / config_file).exists():
        return candidate

    # 3. Any other immediate subdirectory
    try:
        for child in start.iterdir():
            if child.is_dir() and child.name not in (".git", ".confluence") and (child / config_file).exists():
                return child
    except PermissionError:
        pass

    # 4. Walk up parent directories
    check = start.parent
    for _ in range(5):
        if check == check.parent:
            break
        if (check / config_file).exists():
            return check
        check = check.parent

    return start  # fallback — is_initialized() will report the error


def _resolve_url(args, storage=None) -> str:
    """Return the Confluence URL: CLI arg → stored config → CONFLUENCE_TARGET_URL env var."""
    if getattr(args, 'url', None):
        return args.url
    if storage:
        cfg = storage.get_config()
        if cfg and cfg.get('target_url'):
            return cfg['target_url']
    env_url = os.getenv('CONFLUENCE_TARGET_URL', '')
    if env_url:
        return env_url
    print(color(
        "Error: No Confluence URL provided.\n"
        "  Pass it as an argument, or set CONFLUENCE_TARGET_URL in your .env file.",
        Colors.RED))
    sys.exit(1)


def _resolve_path_arg(path: str) -> str:
    """Resolve a user-supplied path argument against cwd.

    When a user runs docinator from a parent directory and passes a path like
    ``confluence_pages/Foo.md``, this normalises it to the absolute path so
    that sync methods (which join against the repo root) find the file.

    Returns the absolute path string when it exists on disk, otherwise returns
    the original value unchanged (so existing callers that pass correct
    relative paths remain unaffected).
    """
    p = Path(path)
    if not p.is_absolute():
        p = Path.cwd() / path
    return str(p) if p.exists() else path


def cmd_init(args):
    """Initialize a new docinator repository."""
    config = load_config()
    client = ConfluenceClient(config)

    # Resolve URL
    url = _resolve_url(args)

    # Test connection
    print("Testing Confluence connection...")
    success, message = client.test_connection()
    if not success:
        print(color(f"Error: {message}", Colors.RED))
        sys.exit(1)
    print(color("Connection successful!", Colors.GREEN))

    # Parse URL
    space_key, content_id, content_type = client.parse_confluence_url(url)
    if not content_id:
        print(color(f"Error: Could not parse URL: {url}", Colors.RED))
        sys.exit(1)

    # Get content info
    info = client.get_page(content_id)
    if not info:
        print(
            color(f"Error: Could not find content with ID {content_id}", Colors.RED))
        sys.exit(1)

    # Set up storage
    output_dir = args.output if args.output else Path.cwd() / "confluence_pages"
    storage = StorageManager(output_dir)
    storage.initialize(config, url, content_id)

    print(
        color(f"\nInitialized docinator repository in: {output_dir}", Colors.GREEN))
    print(f"Target: {info['title']} ({content_type})")
    print(f"Space: {space_key}")
    print(f"\nNext steps:")
    print(f"  cd {output_dir}")
    print(f"  docinator pull")


def cmd_pull(args):
    """Pull pages from Confluence."""
    config = load_config()
    client = ConfluenceClient(config)

    # Get format preference
    content_format = getattr(args, 'format', 'md')

    # Determine output directory
    if args.output:
        output_dir = Path(args.output)
    else:
        repo_root = _find_repo_root()
        storage = StorageManager(repo_root, content_format=content_format)
        if storage.is_initialized():
            output_dir = repo_root
            # Use the format from existing config
            existing_config = storage.get_config()
            if existing_config:
                content_format = existing_config.get("content_format", "md")
        else:
            output_dir = Path.cwd() / "confluence_pages"

    storage = StorageManager(output_dir, content_format=content_format)

    # Resolve URL: CLI arg → stored config → env var
    url = _resolve_url(args, storage)

    sync = SyncManager(client, storage)

    print(f"Pulling from: {url}")
    print(f"Output: {output_dir}")
    print(f"Format: {content_format}")
    print()

    def progress(msg):
        print(msg)

    try:
        result = sync.pull(url, force=args.force,
                           progress_callback=progress)

        print()
        print(color("=" * 50, Colors.CYAN))
        print(f"Pulled: {color(str(result['pulled']), Colors.GREEN)}")
        print(f"Skipped: {result['skipped']}")
        if result['errors']:
            print(color(f"Errors: {len(result['errors'])}", Colors.RED))
            for err in result['errors']:
                print(f"  - {err['title']}: {err['error']}")
        print(color("=" * 50, Colors.CYAN))

    except Exception as e:
        print(color(f"Error: {e}", Colors.RED))
        sys.exit(1)


def cmd_push(args):
    """Push local changes to Confluence."""
    config = load_config()
    client = ConfluenceClient(config)

    repo_root = _find_repo_root()
    storage = StorageManager(repo_root)
    content_format = storage.get_content_format()
    storage = StorageManager(repo_root, content_format=content_format)

    if not storage.is_initialized():
        print(color(
            "Error: Not a docinator repository. Run 'docinator init' first.", Colors.RED))
        sys.exit(1)

    sync = SyncManager(client, storage)
    cwd = Path.cwd()

    def _cwd(p: str) -> str:
        try:
            return str((repo_root / p).relative_to(cwd))
        except ValueError:
            return p

    # ── Resolve what files to push ─────────────────────────────────────────
    push_all = getattr(args, 'all', False)
    dry_run = getattr(args, 'dry_run', False)

    if push_all:
        diffs = sync.diff()
        modified = [d for d in diffs if d.status == DiffStatus.LOCAL_MODIFIED]
        if not modified:
            print(color("Nothing to push — no locally modified pages.", Colors.GREEN))
            return
        push_targets = [str(storage.root / d.local_path) for d in modified]
        print(
            f"Pushing {color(str(len(push_targets)), Colors.YELLOW)} modified file(s):")
        for p in push_targets:
            print(f"  {_cwd(p.replace(str(storage.root) + '/', ''))}")
    elif args.path:
        push_targets = [_resolve_path_arg(args.path)]
        print(f"Pushing: {push_targets[0]}")
    else:
        print(color(
            "Error: specify a path or use --all to push all modified pages.", Colors.RED))
        sys.exit(1)

    if args.message:
        print(f"Message: {args.message}")

    # ── Dry run ────────────────────────────────────────────────────────────
    if dry_run:
        print()
        print(color("Dry run — no changes sent to Confluence.", Colors.YELLOW))
        for target in push_targets:
            print(f"  Would push: {target}")
        return

    print()

    def progress(msg):
        print(msg)

    total_pushed = 0
    total_skipped = 0
    all_conflicts = []
    all_errors = []

    try:
        for target in push_targets:
            result = sync.push(
                target,
                message=args.message,
                force=args.force,
                progress_callback=progress,
            )
            total_pushed += result['pushed']
            total_skipped += result['skipped']
            all_conflicts.extend(result.get('conflicts', []))
            all_errors.extend(result.get('errors', []))

        print()
        print(color("=" * 50, Colors.CYAN))
        print(f"Pushed:  {color(str(total_pushed), Colors.GREEN)}")
        print(f"Skipped: {total_skipped}")

        if all_conflicts:
            print(
                color(f"\nBlocked by conflicts: {len(all_conflicts)} file(s)", Colors.YELLOW))
            for conflict in all_conflicts:
                print(f"  - {conflict['local_path']}")
                print(
                    f"    Remote v{conflict['remote_version']} by {conflict['remote_modified_by']}")
            print()
            print("  docinator diff                              # inspect differences")
            print("  docinator resolve <path> --strategy local   # keep your version")
            print("  docinator resolve <path> --strategy remote  # accept remote")
            print("  docinator push <path> --force               # override")

        if all_errors:
            print(color(f"\nErrors: {len(all_errors)}", Colors.RED))
            for err in all_errors:
                print(f"  - {err['local_path']}: {err['error']}")

        print(color("=" * 50, Colors.CYAN))

        if all_conflicts or all_errors:
            sys.exit(1)

    except Exception as e:
        print(color(f"Error: {e}", Colors.RED))
        sys.exit(1)


def cmd_diff(args):
    """Show differences between local and remote."""
    config = load_config()
    client = ConfluenceClient(config)

    # Create storage and get format from config
    repo_root = _find_repo_root()
    storage = StorageManager(repo_root)
    content_format = storage.get_content_format()
    storage = StorageManager(repo_root, content_format=content_format)

    if not storage.is_initialized():
        print(color(
            "Error: Not a docinator repository. Run 'docinator init' first.", Colors.RED))
        sys.exit(1)

    sync = SyncManager(client, storage)

    # Normalise optional path arg against cwd (same fix as cmd_push)
    path = _resolve_path_arg(args.path) if args.path else None
    recursive = args.recursive

    # Helper: make a file path relative to cwd so copy-pasted commands work
    # from wherever the user ran docinator.
    cwd = Path.cwd()

    def cwd_path(local_path: str) -> str:
        abs_path = repo_root / local_path
        try:
            return str(abs_path.relative_to(cwd))
        except ValueError:
            return str(abs_path)

    print("Checking for differences...")
    print()

    try:
        results = sync.diff(path=path, recursive=recursive)

        if not results:
            print("No files to check.")
            return

        # Group by status
        by_status = {}
        for r in results:
            if r.status not in by_status:
                by_status[r.status] = []
            by_status[r.status].append(r)

        # Print summary first
        print(color("Summary:", Colors.BOLD))
        status_colors = {
            DiffStatus.UNCHANGED: Colors.GREEN,
            DiffStatus.LOCAL_MODIFIED: Colors.YELLOW,
            DiffStatus.REMOTE_MODIFIED: Colors.CYAN,
            DiffStatus.CONFLICT: Colors.RED,
            DiffStatus.LOCAL_ONLY: Colors.MAGENTA,
            DiffStatus.REMOTE_ONLY: Colors.BLUE,
        }

        for status, items in by_status.items():
            c = status_colors.get(status, Colors.WHITE)
            print(f"  {color(status.value, c)}: {len(items)}")

        # List changed/conflict files in the summary with ready-to-run commands
        changed_statuses = [
            DiffStatus.LOCAL_MODIFIED,
            DiffStatus.REMOTE_MODIFIED,
            DiffStatus.CONFLICT,
        ]
        changed = [r for r in results if r.status in changed_statuses]
        if changed and not (args.show_diff or args.git):
            print()
            print(
                color("Changed files (run with --show-diff for inline diff):", Colors.BOLD))
            for r in changed:
                p = cwd_path(r.local_path)
                c = status_colors.get(r.status, Colors.WHITE)
                print(
                    f"  {color(r.status.value[0].upper(), c)}  {color(p, Colors.CYAN)}")
                print(f"       docinator diff \"{p}\" --show-diff")

        print()

        # Show details for modified/conflict files
        show_details = [
            DiffStatus.LOCAL_MODIFIED,
            DiffStatus.REMOTE_MODIFIED,
            DiffStatus.CONFLICT,
        ]

        for status in show_details:
            if status in by_status:
                print(color(f"\n{status.value.upper()}:", Colors.BOLD))
                for r in by_status[status]:
                    p = cwd_path(r.local_path)
                    print(f"\n  {color(p, Colors.CYAN)}")
                    print(f"    Page: {r.title} (ID: {r.page_id})")
                    print(
                        f"    Local version: {r.local_version}, Remote version: {r.remote_version}")
                    if r.remote_modified_by:
                        print(
                            f"    Last remote edit by: {r.remote_modified_by}")

                    # Show diff if requested
                    if args.show_diff or args.git:
                        print()
                        if args.git:
                            diff_text = sync.show_diff_with_git(r)
                        else:
                            diff_text = sync.show_diff(r)

                        # Indent diff output
                        for line in diff_text.splitlines():
                            if line.startswith('+') and not line.startswith('+++'):
                                print(f"    {color(line, Colors.GREEN)}")
                            elif line.startswith('-') and not line.startswith('---'):
                                print(f"    {color(line, Colors.RED)}")
                            elif line.startswith('@@'):
                                print(f"    {color(line, Colors.CYAN)}")
                            else:
                                print(f"    {line}")

        # Show local-only files
        if DiffStatus.LOCAL_ONLY in by_status:
            print(color(f"\nUNTRACKED FILES:", Colors.BOLD))
            for r in by_status[DiffStatus.LOCAL_ONLY]:
                print(f"  {color(cwd_path(r.local_path), Colors.MAGENTA)}")

    except Exception as e:
        print(color(f"Error: {e}", Colors.RED))
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_status(args):
    """Show sync status."""
    config = load_config()
    client = ConfluenceClient(config)

    # Create storage and get format from config
    repo_root = _find_repo_root()
    storage = StorageManager(repo_root)
    content_format = storage.get_content_format()
    storage = StorageManager(repo_root, content_format=content_format)

    if not storage.is_initialized():
        print(color("Not a docinator repository.", Colors.YELLOW))
        print("Run 'docinator init <url>' to initialize.")
        return

    sync = SyncManager(client, storage)
    status = sync.status()

    cwd = Path.cwd()

    def _cwd(local_path: str) -> str:
        try:
            return str((repo_root / local_path).relative_to(cwd))
        except ValueError:
            return str(repo_root / local_path)

    print(color("Docinator Status", Colors.BOLD))
    print(color("=" * 40, Colors.CYAN))
    print(f"Repository: {status['root_path']}")
    print(f"Target:     {status['target_url']}")
    print(f"Space:      {status['space_key']}")
    print()
    print(f"Tracked pages: {status['tracked_pages']}")
    if status.get('tracked_attachments'):
        print(f"Tracked attachments: {status['tracked_attachments']}")
    if status.get('pages_with_labels'):
        print(
            f"Labels: {status['total_labels']} across {status['pages_with_labels']} pages")
    print()

    if status['unchanged']:
        print(f"  {color('●', Colors.GREEN)} Unchanged: {status['unchanged']}")

    if status['local_modified']:
        print(
            f"  {color('●', Colors.YELLOW)} Local changes: {status['local_modified']}")
        for f in status.get('local_modified_files', []):
            p = _cwd(f)
            print(f"      {color(p, Colors.YELLOW)}")
            print(f"      → docinator push \"{p}\"")

    if status['remote_modified']:
        print(
            f"  {color('●', Colors.CYAN)} Remote changes: {status['remote_modified']}")
        for f in status.get('remote_modified_files', []):
            p = _cwd(f)
            print(f"      {color(p, Colors.CYAN)}")
            print(f"      → docinator pull")

    if status['conflicts']:
        print(f"  {color('●', Colors.RED)} Conflicts: {status['conflicts']}")
        for f in status.get('conflict_files', []):
            p = _cwd(f)
            print(f"      {color(p, Colors.RED)}")
            print(f"      → docinator resolve \"{p}\" --strategy local|remote")

    if status['untracked']:
        print(
            f"  {color('●', Colors.MAGENTA)} Untracked (not in Confluence): {status['untracked']}")
        for f in status.get('untracked_files', []):
            p = _cwd(f)
            print(f"      {color(p, Colors.MAGENTA)}")
            print(f"      → docinator create \"{p}\"")

    print()

    if not any([status['local_modified'], status['remote_modified'],
                status['conflicts'], status['untracked']]):
        print(color("Everything is in sync.", Colors.GREEN))
    else:
        if status['remote_modified']:
            print("Run 'docinator pull' to download remote changes.")
        if status['conflicts']:
            print("Run 'docinator diff' to inspect conflicts.")


def cmd_resolve(args):
    """Resolve conflicts."""
    config = load_config()
    client = ConfluenceClient(config)

    # Create storage and get format from config
    repo_root = _find_repo_root()
    storage = StorageManager(repo_root)
    content_format = storage.get_content_format()
    storage = StorageManager(repo_root, content_format=content_format)

    if not storage.is_initialized():
        print(color("Error: Not a docinator repository.", Colors.RED))
        sys.exit(1)

    sync = SyncManager(client, storage)

    resolve_path = _resolve_path_arg(args.path)

    print(f"Resolving: {resolve_path}")
    print(f"Strategy: {args.strategy}")

    success, message = sync.resolve_conflict(resolve_path, args.strategy)

    if success:
        print(color(f"Success: {message}", Colors.GREEN))
    else:
        print(color(f"Error: {message}", Colors.RED))
        sys.exit(1)


_SETUP_EXAMPLE_ENV = """\
# Confluence connection
# Copy this file to .env and fill in your values

# Base URL for Confluence (your instance URL ending with /wiki)
CONFLUENCE_BASE_URL=https://your-domain.atlassian.net/wiki

# Your Confluence username (usually your email)
CONFLUENCE_USERNAME=your.email@yourorganization.com

# API token - get one from: https://id.atlassian.com/manage-profile/security/api-tokens
CONFLUENCE_API_KEY=your-api-token-here

# Default space key (optional, extracted from URLs if not provided)
CONFLUENCE_SPACE_KEY=YOUR_SPACE

# Target folder URL (optional) - used as default for pull/init when no URL is passed
# CONFLUENCE_TARGET_URL=https://your-domain.atlassian.net/wiki/spaces/SPACE/folder/123456789

# Editor version (optional, default: 2)
CONFLUENCE_EDITOR_VERSION=2
"""

_SETUP_README = """\
# Confluence Docinator

A CLI tool for syncing Confluence pages with local files, enabling a git-like
pull / diff / push workflow for documentation management.

## Quick Start

### 1. Configure credentials

Copy `example.env` to `.env` and fill in your values:

```bash
cp example.env .env
```

Edit `.env`:

```env
CONFLUENCE_BASE_URL=https://your-domain.atlassian.net/wiki
CONFLUENCE_USERNAME=your.email@yourorganization.com
CONFLUENCE_API_KEY=your-api-token-here
CONFLUENCE_SPACE_KEY=YOUR_SPACE
```

**Getting an API token:**
1. Go to https://id.atlassian.com/manage-profile/security/api-tokens
2. Click "Create API token", give it a name, and copy the value.

---

## Commands

### Test connection

```bash
docinator test
```

### Pull pages

Download all pages from a Confluence folder to `./confluence_pages/`:

```bash
docinator pull "https://your-domain.atlassian.net/wiki/spaces/SPACE/folder/123456789"
```

Options:
- `-o, --output <dir>` – custom output directory
- `-f, --force` – overwrite local changes without prompting
- `--format md|xhtml` – file format (default: `md`)

### Check status

```bash
docinator status
```

Example output:

```
Docinator Status
========================================
Repository: /path/to/confluence_pages
Target: https://your-domain.atlassian.net/...
Space: YOUR_SPACE

Tracked pages: 28
Labels: 38 across 16 pages

  ● Unchanged: 27
  ● Local changes: 1
  ● Remote changes: 0
  ● Conflicts: 0
```

### View differences

```bash
docinator diff                         # all files
docinator diff path/to/Page.md         # single file
docinator diff path/to/Page.md --show-diff   # with inline diff
docinator diff path/to/Page.md --git         # use git diff style
```

### Push changes

```bash
docinator push path/to/Page.md
docinator push path/to/Page.md -m "Updated API docs"
docinator push path/to/folder/
docinator push path/to/Page.md --force       # override conflicts
```

### Resolve conflicts

```bash
docinator resolve path/to/Page.md --strategy local    # keep your version
docinator resolve path/to/Page.md --strategy remote   # use Confluence version
docinator resolve path/to/Page.md --strategy merge    # manual merge file
```

---

## Typical workflow

```bash
# 1. Pull documentation
docinator pull "https://your-domain.atlassian.net/wiki/spaces/SPACE/folder/123456789"

# 2. Edit files locally
cd confluence_pages/
# ... edit .md files with any editor ...

# 3. See what changed
docinator status
docinator diff path/to/Page.md --show-diff

# 4. Push updates back to Confluence
docinator push path/to/Page.md -m "Improved installation steps"
```

---

## Repository structure

```
confluence_pages/
├── .confluence/
│   ├── config.json            # sync configuration
│   ├── index.json             # page tracking index
│   ├── pages/{id}.json        # per-page metadata & labels
│   ├── macros/{id}.json       # preserved Confluence macros
│   └── attachments/           # attachment metadata
├── Parent_Page.md
├── Subfolder/
│   ├── Child_Page_1.md
│   └── Child_Page_2.md
└── _images/
    └── some_diagram.png
```

---

## Notes

- Macro-rich content (layouts, ADF extensions, etc.) is preserved as
  `<!-- CONFLUENCE_MACRO_N: name -->` placeholders in Markdown and
  restored on push from the macro store in `.confluence/macros/`.
- Page labels are pulled into metadata and synced back on push.
- Never edit the `.confluence/` directory manually.
- Always `diff` before `push` to avoid overwriting others' changes.
"""


def _parse_setup_url(url: str) -> dict:
    """Extract base_url, space_key, and clean target_url from a Confluence URL."""
    parsed = urlparse(url)
    # Strip query string / fragment from target URL
    clean_url = urlunparse(parsed._replace(query="", fragment=""))

    # base_url = scheme + host + /wiki  (handle both /wiki/... and bare host)
    path = parsed.path
    wiki_idx = path.find("/wiki")
    if wiki_idx >= 0:
        base_path = path[:wiki_idx + len("/wiki")]
    else:
        base_path = ""
    base_url = f"{parsed.scheme}://{parsed.netloc}{base_path}"

    # space key: /spaces/<KEY>/
    m = re.search(r"/spaces/([^/]+)/", path)
    space_key = m.group(1) if m else ""

    return {"base_url": base_url, "space_key": space_key, "target_url": clean_url}


def cmd_setup(args):
    """Create example.env and README.md in the current directory."""
    cwd = Path.cwd()

    # If a URL was provided, extract values from it
    extracted = {}
    if getattr(args, 'url', None):
        extracted = _parse_setup_url(args.url)
        print(color(f"Extracted from URL:", Colors.CYAN))
        print(f"  CONFLUENCE_BASE_URL  = {extracted['base_url']}")
        print(f"  CONFLUENCE_SPACE_KEY = {extracted['space_key']}")
        print(f"  CONFLUENCE_TARGET_URL= {extracted['target_url']}")
        print()

    # Build .env content
    base_url = extracted.get(
        'base_url') or 'https://your-domain.atlassian.net/wiki'
    space_key = extracted.get('space_key') or 'YOUR_SPACE'
    target_url = extracted.get('target_url') or ''
    target_line = (
        f"CONFLUENCE_TARGET_URL={target_url}"
        if target_url
        else "# CONFLUENCE_TARGET_URL=https://your-domain.atlassian.net/wiki/spaces/SPACE/folder/123456789"
    )

    env_content = f"""\
# Confluence connection
# Rename / copy this file to .env

# Base URL (ends with /wiki)
CONFLUENCE_BASE_URL={base_url}

# Your Confluence username (email)
CONFLUENCE_USERNAME=your.email@yourorganization.com

# API token: https://id.atlassian.com/manage-profile/security/api-tokens
CONFLUENCE_API_KEY=your-api-token-here

# Space key
CONFLUENCE_SPACE_KEY={space_key}

# Target folder/page URL — used when running docinator pull with no argument
{target_line}

# Editor version (default: 2)
CONFLUENCE_EDITOR_VERSION=2
"""

    files = {
        "example.env": env_content,
        "README.md": _SETUP_README,
    }

    for filename, content in files.items():
        target = cwd / filename
        if target.exists():
            answer = input(
                f"  {filename} already exists. Overwrite? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print(f"  Skipped {filename}")
                continue

        target.write_text(content, encoding="utf-8")
        print(color(f"  Created {target}", Colors.GREEN))

    print()
    if extracted:
        print("Next steps:")
        print("  1. cp example.env .env")
        print("  2. Edit .env — fill in CONFLUENCE_USERNAME and CONFLUENCE_API_KEY")
        print("  3. Run: docinator test")
        print("  4. Run: docinator pull")
    else:
        print("Next steps:")
        print("  1. cp example.env .env")
        print("  2. Edit .env — fill in all values")
        print("  3. Run: docinator test")
        print("  4. Run: docinator pull <your-confluence-folder-url>")


def cmd_completion(args):
    """Print shell completion activation instructions."""
    shell = args.shell
    if shell == "zsh":
        print(color("Add this line to your ~/.zshrc:", Colors.BOLD))
        print()
        print('  eval "$(register-python-argcomplete docinator)"')
        print()
        print("Then reload your shell:  source ~/.zshrc")
    elif shell == "bash":
        print(color("Add this line to your ~/.bashrc or ~/.bash_profile:", Colors.BOLD))
        print()
        print('  eval "$(register-python-argcomplete docinator)"')
        print()
        print("Then reload your shell:  source ~/.bashrc")
    else:
        print(color("Quick setup (run once):", Colors.BOLD))
        print()
        print("  zsh:   echo 'eval \"$(register-python-argcomplete docinator)\"' >> ~/.zshrc && source ~/.zshrc")
        print("  bash:  echo 'eval \"$(register-python-argcomplete docinator)\"' >> ~/.bashrc && source ~/.bashrc")
        print()
        print("After activating, pressing <Tab> after 'docinator diff', 'docinator push',")
        print("or 'docinator resolve' will complete file paths in your current directory.")


def cmd_test(args):
    """Test Confluence connection."""
    config = load_config()
    client = ConfluenceClient(config)

    print("Testing connection to Confluence...")
    print(f"URL: {config.base_url}")
    print(f"User: {config.username}")

    success, message = client.test_connection()

    if success:
        print(color(f"\n{message}", Colors.GREEN))
    else:
        print(color(f"\n{message}", Colors.RED))
        sys.exit(1)


def cmd_new(args):
    """Scaffold a new local .md stub and immediately publish it to Confluence."""
    config = load_config()
    client = ConfluenceClient(config)
    repo_root = _find_repo_root()
    storage = StorageManager(repo_root)
    content_format = storage.get_content_format()
    storage = StorageManager(repo_root, content_format=content_format)

    if not storage.is_initialized():
        print(color("Error: Not a docinator repository.", Colors.RED))
        sys.exit(1)

    # Determine target directory
    if args.dir:
        target_dir = Path(args.dir)
        if not target_dir.is_absolute():
            target_dir = repo_root / args.dir
    elif args.parent:
        parent_abs = Path(args.parent)
        if not parent_abs.is_absolute():
            parent_abs = Path.cwd() / args.parent
        target_dir = parent_abs.parent
    else:
        # Default to cwd when cwd is inside the repo root, otherwise repo root itself
        try:
            Path.cwd().relative_to(repo_root)
            target_dir = Path.cwd()
        except ValueError:
            target_dir = repo_root

    # Sanitise title to a safe filename
    safe_name = re.sub(r'[<>:"/\\|?*]', "_", args.title)
    ext = ".md"
    file_path = target_dir / f"{safe_name}{ext}"

    if file_path.exists():
        print(color(f"File already exists: {file_path}", Colors.YELLOW))
        sys.exit(1)

    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(f"# {args.title}\n\n", encoding="utf-8")

    try:
        rel_path = str(file_path.relative_to(repo_root))
    except ValueError:
        rel_path = str(file_path)

    print(color(f"Created: {file_path}", Colors.GREEN))

    if getattr(args, 'publish', False):
        sync = SyncManager(client, storage)
        parent_path = args.parent if args.parent else None
        success, msg, url = sync.create_new_page(
            rel_path,
            title=args.title,
            parent_path=parent_path,
        )
        if success:
            print(color(f"✓ {msg}", Colors.GREEN))
            if url:
                print(f"  {url}")
        else:
            print(color(f"✗ {msg}", Colors.RED))
            print(f"  File saved locally. Run:")
            print(f'  docinator create "{rel_path}"')
    else:
        print(f"  Edit the file, then run:")
        print(f'  docinator create "{rel_path}"')


def cmd_create(args):
    """Publish an existing local .md file as a new Confluence page."""
    config = load_config()
    client = ConfluenceClient(config)
    repo_root = _find_repo_root()
    storage = StorageManager(repo_root)
    content_format = storage.get_content_format()
    storage = StorageManager(repo_root, content_format=content_format)

    if not storage.is_initialized():
        print(color("Error: Not a docinator repository.", Colors.RED))
        sys.exit(1)

    # Resolve path to absolute, then make relative to repo root
    abs_path = Path(args.path) if Path(
        args.path).is_absolute() else Path.cwd() / args.path
    try:
        rel_path = str(abs_path.relative_to(repo_root))
    except ValueError:
        rel_path = args.path

    if not abs_path.exists():
        print(color(f"Error: File not found: {abs_path}", Colors.RED))
        sys.exit(1)

    title = args.title if args.title else abs_path.stem.replace("_", " ")

    print(f"Creating page: {color(title, Colors.CYAN)}")
    if args.parent:
        print(f"  Parent: {args.parent}")
    print()

    sync = SyncManager(client, storage)
    success, msg, url = sync.create_new_page(
        rel_path,
        title=title,
        parent_path=args.parent if args.parent else None,
        message=args.message if hasattr(args, "message") else None,
    )

    if success:
        print(color(f"✓ {msg}", Colors.GREEN))
        if url:
            print(f"  {url}")
    else:
        print(color(f"✗ {msg}", Colors.RED))
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Confluence Docinator - Sync Confluence pages with local files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  docinator setup
  docinator setup "https://your-domain.atlassian.net/wiki/spaces/SPACE/folder/123456789"
  docinator init https://your-domain.atlassian.net/wiki/spaces/SPACE/folder/123456789
  docinator pull https://your-domain.atlassian.net/wiki/spaces/SPACE/folder/123456789
  docinator status
  docinator diff
  docinator diff path/to/Page.md --show-diff
  docinator push path/to/Page.md -m "Updated documentation"
  docinator resolve path/to/Page.md --strategy local
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # init command
    init_parser = subparsers.add_parser(
        "init", help="Initialize a docinator repository")
    init_parser.add_argument(
        "url", nargs="?",
        help="Confluence folder URL (optional if CONFLUENCE_TARGET_URL is set in .env)")
    init_parser.add_argument(
        "-o", "--output", help="Output directory (default: ./confluence_pages)")
    init_parser.add_argument(
        "--format", choices=["md", "xhtml"], default="md",
        help="Content format: md (Markdown, default) or xhtml (native Confluence)")
    init_parser.set_defaults(func=cmd_init)

    # pull command
    pull_parser = subparsers.add_parser(
        "pull", help="Pull pages from Confluence")
    pull_parser.add_argument(
        "url", nargs="?",
        help="Confluence folder URL (optional if already initialized or CONFLUENCE_TARGET_URL is set)")
    pull_parser.add_argument("-o", "--output", help="Output directory")
    pull_parser.add_argument(
        "-f", "--force", action="store_true", help="Force overwrite local changes")
    pull_parser.add_argument(
        "--format", choices=["md", "xhtml"], default="md",
        help="Content format: md (Markdown, default) or xhtml (native Confluence)")
    pull_parser.set_defaults(func=cmd_pull)

    # push command
    push_parser = subparsers.add_parser(
        "push", help="Push local changes to Confluence")
    push_path = push_parser.add_argument(
        "path", nargs="?", help="File or directory to push")
    push_parser.add_argument("-m", "--message", help="Version message")
    push_parser.add_argument(
        "-f", "--force", action="store_true", help="Force push even with conflicts")
    push_parser.add_argument(
        "--all", action="store_true",
        help="Push all locally modified pages (no path required)")
    push_parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be pushed without sending anything to Confluence")
    push_parser.set_defaults(func=cmd_push)

    # diff command
    diff_parser = subparsers.add_parser(
        "diff", help="Show differences between local and remote")
    diff_path = diff_parser.add_argument(
        "path", nargs="?", help="File or directory to diff (default: all)")
    diff_parser.add_argument("-r", "--recursive", action="store_true",
                             default=True, help="Recursive diff for directories")
    diff_parser.add_argument("--no-recursive", action="store_false",
                             dest="recursive", help="Non-recursive diff")
    diff_parser.add_argument(
        "-d", "--show-diff", action="store_true", help="Show actual diff content")
    diff_parser.add_argument("--git", action="store_true",
                             help="Use git diff for visualization")
    diff_parser.set_defaults(func=cmd_diff)

    # status command
    status_parser = subparsers.add_parser("status", help="Show sync status")
    status_parser.set_defaults(func=cmd_status)

    # resolve command
    resolve_parser = subparsers.add_parser("resolve", help="Resolve conflicts")
    resolve_path = resolve_parser.add_argument("path", help="File to resolve")
    resolve_parser.add_argument("-s", "--strategy", required=True,
                                choices=["local", "remote", "merge"],
                                help="Resolution strategy: local (keep yours), remote (use theirs), merge (manual)")
    resolve_parser.set_defaults(func=cmd_resolve)

    # new command: scaffold + publish a brand-new page
    new_parser = subparsers.add_parser(
        "new",
        help="Create a new page (scaffold a .md file and publish it to Confluence)")
    new_parser.add_argument("title", help="Page title")
    new_parser.add_argument(
        "--parent",
        metavar="PATH",
        help="Path to parent page .md file — determines where the page lives in Confluence")
    new_parser.add_argument(
        "--dir",
        metavar="DIR",
        help="Local directory to create the .md file in (overrides --parent location)")
    new_parser.add_argument(
        "--publish",
        action="store_true",
        help="Immediately publish the new page to Confluence (default: local file only)")
    new_parser.set_defaults(func=cmd_new)

    # create command: publish an existing local file as a new Confluence page
    create_parser = subparsers.add_parser(
        "create",
        help="Publish an existing local .md file as a new Confluence page")
    create_path = create_parser.add_argument(
        "path", help="Path to the local .md file to publish")
    create_parser.add_argument(
        "--title",
        help="Override the page title (default: derived from filename)")
    create_parser.add_argument(
        "--parent",
        metavar="PATH",
        help="Path to parent page .md file (overrides auto-discovery)")
    create_parser.add_argument(
        "-m", "--message",
        help="Version message")
    create_parser.set_defaults(func=cmd_create)

    # Attach file completers to path arguments (argcomplete)
    if _ARGCOMPLETE:
        _fc = FilesCompleter()
        push_path.completer = _fc
        diff_path.completer = _fc
        resolve_path.completer = _fc
        create_path.completer = _fc

    # setup command
    setup_parser = subparsers.add_parser(
        "setup",
        help="Create example.env and README.md in the current directory")
    setup_parser.add_argument(
        "url", nargs="?",
        help="Confluence URL to pre-fill BASE_URL, SPACE_KEY, and TARGET_URL in example.env")
    setup_parser.set_defaults(func=cmd_setup)

    # completion command
    completion_parser = subparsers.add_parser(
        "completion", help="Print shell tab-completion activation instructions")
    completion_parser.add_argument(
        "shell", nargs="?", choices=["zsh", "bash"],
        help="Shell type (default: shows both)")
    completion_parser.set_defaults(func=cmd_completion)

    # test command
    test_parser = subparsers.add_parser(
        "test", help="Test Confluence connection")
    test_parser.set_defaults(func=cmd_test)

    if _ARGCOMPLETE:
        argcomplete.autocomplete(parser)

    args = parser.parse_args()

    if args.command is None:
        print(color("Tip: run 'docinator setup <confluence-url>' to create a pre-filled .env and README for your workspace.", Colors.CYAN))
        print()
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
