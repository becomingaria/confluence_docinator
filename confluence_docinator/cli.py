#!/usr/bin/env python3
"""
Confluence Docinator CLI - Sync Confluence pages with local files.

Usage:
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
  docinator init https://wiki.example.com/wiki/spaces/SPACE/folder/123
  docinator pull https://wiki.example.com/wiki/spaces/SPACE/folder/123
  docinator status
  docinator diff
  docinator diff path/to/file.md --show-diff
  docinator push path/to/file.md -m "Updated documentation"
  docinator resolve path/to/file.md --strategy local
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

    # test command
    test_parser = subparsers.add_parser(
        "test", help="Test Confluence connection")
    test_parser.set_defaults(func=cmd_test)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
