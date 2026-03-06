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
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

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


def cmd_init(args):
    """Initialize a new docinator repository."""
    config = load_config()
    client = ConfluenceClient(config)

    # Test connection
    print("Testing Confluence connection...")
    success, message = client.test_connection()
    if not success:
        print(color(f"Error: {message}", Colors.RED))
        sys.exit(1)
    print(color("Connection successful!", Colors.GREEN))

    # Parse URL
    space_key, content_id, content_type = client.parse_confluence_url(args.url)
    if not content_id:
        print(color(f"Error: Could not parse URL: {args.url}", Colors.RED))
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
    storage.initialize(config, args.url, content_id)

    print(
        color(f"\nInitialized docinator repository in: {output_dir}", Colors.GREEN))
    print(f"Target: {info['title']} ({content_type})")
    print(f"Space: {space_key}")
    print(f"\nNext steps:")
    print(f"  cd {output_dir}")
    print(f"  docinator pull {args.url}")


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
        # Check if we're in an initialized repo
        storage = StorageManager(Path.cwd(), content_format=content_format)
        if storage.is_initialized():
            output_dir = Path.cwd()
            # Use the format from existing config
            existing_config = storage.get_config()
            if existing_config:
                content_format = existing_config.get("content_format", "md")
        else:
            output_dir = Path.cwd() / "confluence_pages"

    storage = StorageManager(output_dir, content_format=content_format)
    sync = SyncManager(client, storage)

    print(f"Pulling from: {args.url}")
    print(f"Output: {output_dir}")
    print(f"Format: {content_format}")
    print()

    def progress(msg):
        print(msg)

    try:
        result = sync.pull(args.url, force=args.force,
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

    # Create storage and get format from config
    storage = StorageManager(Path.cwd())
    content_format = storage.get_content_format()
    storage = StorageManager(Path.cwd(), content_format=content_format)

    if not storage.is_initialized():
        print(color(
            "Error: Not a docinator repository. Run 'docinator init' first.", Colors.RED))
        sys.exit(1)

    sync = SyncManager(client, storage)

    print(f"Pushing: {args.path}")
    if args.message:
        print(f"Message: {args.message}")
    print()

    def progress(msg):
        print(msg)

    try:
        result = sync.push(
            args.path,
            message=args.message,
            force=args.force,
            progress_callback=progress,
        )

        print()
        print(color("=" * 50, Colors.CYAN))
        print(f"Pushed: {color(str(result['pushed']), Colors.GREEN)}")
        print(f"Skipped: {result['skipped']}")

        if result['conflicts']:
            print(
                color(f"\nConflicts: {len(result['conflicts'])}", Colors.YELLOW))
            for conflict in result['conflicts']:
                print(f"  - {conflict['local_path']}")
                print(
                    f"    Remote v{conflict['remote_version']} by {conflict['remote_modified_by']}")
            print("\nUse 'docinator diff <path>' to see details")
            print("Use 'docinator resolve <path> --strategy <local|remote>' to resolve")
            print("Or use 'docinator push <path> --force' to override")

        if result['errors']:
            print(color(f"\nErrors: {len(result['errors'])}", Colors.RED))
            for err in result['errors']:
                print(f"  - {err['local_path']}: {err['error']}")

        print(color("=" * 50, Colors.CYAN))

    except Exception as e:
        print(color(f"Error: {e}", Colors.RED))
        sys.exit(1)


def cmd_diff(args):
    """Show differences between local and remote."""
    config = load_config()
    client = ConfluenceClient(config)

    # Create storage and get format from config
    storage = StorageManager(Path.cwd())
    content_format = storage.get_content_format()
    storage = StorageManager(Path.cwd(), content_format=content_format)

    if not storage.is_initialized():
        print(color(
            "Error: Not a docinator repository. Run 'docinator init' first.", Colors.RED))
        sys.exit(1)

    sync = SyncManager(client, storage)

    path = args.path if args.path else None
    recursive = args.recursive

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
                    print(f"\n  {color(r.local_path, Colors.CYAN)}")
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
                print(f"  {color(r.local_path, Colors.MAGENTA)}")

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
    storage = StorageManager(Path.cwd())
    content_format = storage.get_content_format()
    storage = StorageManager(Path.cwd(), content_format=content_format)

    if not storage.is_initialized():
        print(color("Not a docinator repository.", Colors.YELLOW))
        print("Run 'docinator init <url>' to initialize.")
        return

    sync = SyncManager(client, storage)
    status = sync.status()

    print(color("Docinator Status", Colors.BOLD))
    print(color("=" * 40, Colors.CYAN))
    print(f"Repository: {status['root_path']}")
    print(f"Target: {status['target_url']}")
    print(f"Space: {status['space_key']}")
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
    if status['remote_modified']:
        print(
            f"  {color('●', Colors.CYAN)} Remote changes: {status['remote_modified']}")
    if status['conflicts']:
        print(f"  {color('●', Colors.RED)} Conflicts: {status['conflicts']}")
    if status['untracked']:
        print(
            f"  {color('●', Colors.MAGENTA)} Untracked: {status['untracked']}")

    print()

    if status['local_modified']:
        print("Use 'docinator push <path>' to upload local changes")
    if status['remote_modified']:
        print("Use 'docinator pull <url>' to download remote changes")
    if status['conflicts']:
        print("Use 'docinator diff' to see conflicts")
        print("Use 'docinator resolve <path> --strategy <local|remote>' to resolve")


def cmd_resolve(args):
    """Resolve conflicts."""
    config = load_config()
    client = ConfluenceClient(config)

    # Create storage and get format from config
    storage = StorageManager(Path.cwd())
    content_format = storage.get_content_format()
    storage = StorageManager(Path.cwd(), content_format=content_format)

    if not storage.is_initialized():
        print(color("Error: Not a docinator repository.", Colors.RED))
        sys.exit(1)

    sync = SyncManager(client, storage)

    print(f"Resolving: {args.path}")
    print(f"Strategy: {args.strategy}")

    success, message = sync.resolve_conflict(args.path, args.strategy)

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


def cmd_setup(args):
    """Create example.env and README.md in the current directory."""
    cwd = Path.cwd()
    files = {
        "example.env": _SETUP_EXAMPLE_ENV,
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
    print("Next steps:")
    print("  1. Copy example.env → .env and fill in your credentials")
    print("  2. Run: docinator test")
    print("  3. Run: docinator pull <your-confluence-folder-url>")


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


def main():
    parser = argparse.ArgumentParser(
        description="Confluence Docinator - Sync Confluence pages with local files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  docinator setup
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
    init_parser.add_argument("url", help="Confluence folder URL to sync")
    init_parser.add_argument(
        "-o", "--output", help="Output directory (default: ./confluence_pages)")
    init_parser.add_argument(
        "--format", choices=["md", "xhtml"], default="md",
        help="Content format: md (Markdown, default) or xhtml (native Confluence)")
    init_parser.set_defaults(func=cmd_init)

    # pull command
    pull_parser = subparsers.add_parser(
        "pull", help="Pull pages from Confluence")
    pull_parser.add_argument("url", help="Confluence folder URL to pull from")
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
    push_parser.add_argument("path", help="File or directory to push")
    push_parser.add_argument("-m", "--message", help="Version message")
    push_parser.add_argument(
        "-f", "--force", action="store_true", help="Force push even with conflicts")
    push_parser.set_defaults(func=cmd_push)

    # diff command
    diff_parser = subparsers.add_parser(
        "diff", help="Show differences between local and remote")
    diff_parser.add_argument(
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
    resolve_parser.add_argument("path", help="File to resolve")
    resolve_parser.add_argument("-s", "--strategy", required=True,
                                choices=["local", "remote", "merge"],
                                help="Resolution strategy: local (keep yours), remote (use theirs), merge (manual)")
    resolve_parser.set_defaults(func=cmd_resolve)

    # setup command
    setup_parser = subparsers.add_parser(
        "setup",
        help="Create example.env and README.md in the current directory")
    setup_parser.set_defaults(func=cmd_setup)

    # test command
    test_parser = subparsers.add_parser(
        "test", help="Test Confluence connection")
    test_parser.set_defaults(func=cmd_test)

    args = parser.parse_args()

    if args.command is None:
        print(color("Tip: run 'docinator setup' in any directory to create a starter example.env and README.", Colors.CYAN))
        print()
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
