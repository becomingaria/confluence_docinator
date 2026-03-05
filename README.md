# Confluence Docinator

A CLI tool for syncing Confluence pages with local files, enabling a git-like workflow for documentation management.

## Features

- **Pull**: Download all pages from a Confluence folder, maintaining folder structure
- **Push**: Upload local changes back to Confluence
- **Diff**: Compare local files with Confluence (detect conflicts before pushing)
- **Resolve**: Handle conflicts between local and remote changes
- **Status**: View sync status at a glance

## Installation

### From Source

```bash
# Clone the repository
cd confluence_docinator

# Install in development mode
pip install -e .

# Or install dependencies directly
pip install -r requirements.txt
```

### Using pip (once published)

```bash
pip install confluence-docinator
```

## Configuration

1. Copy `example.env` to `.env`:

```bash
cp example.env .env
```

2. Edit `.env` with your Confluence credentials:

```env
CONFLUENCE_BASE_URL=https://your-domain.atlassian.net/wiki
CONFLUENCE_USERNAME=your.email@company.com
CONFLUENCE_API_KEY=your-api-token-here
CONFLUENCE_SPACE_KEY=YOUR_SPACE
CONFLUENCE_EDITOR_VERSION=2
```

### Getting an API Token

1. Go to https://id.atlassian.com/manage-profile/security/api-tokens
2. Click "Create API token"
3. Give it a label (e.g., "Docinator")
4. Copy the token to your `.env` file

## Usage

### Initialize a Repository

```bash
docinator init "https://wiki.inside.milvian.group/wiki/spaces/Aqueduct/folder/731349007"
```

This creates a local directory with the folder structure from Confluence.

### Pull Pages

Download all pages from a Confluence folder:

```bash
docinator pull "https://wiki.inside.milvian.group/wiki/spaces/Aqueduct/folder/731349007"
```

Options:

- `-o, --output <dir>`: Specify output directory (default: `./confluence_pages`)
- `-f, --force`: Overwrite local changes without prompting

### Check Status

See what's changed locally vs remotely:

```bash
docinator status
```

Output example:

```
Docinator Status
========================================
Repository: /path/to/confluence_pages
Target: https://wiki.inside.milvian.group/...
Space: Aqueduct

Tracked pages: 15

  ● Unchanged: 12
  ● Local changes: 2
  ● Remote changes: 1
  ● Conflicts: 0
```

### View Differences

Check what's different between local and remote:

```bash
# Check all files
docinator diff

# Check specific file
docinator diff path/to/Page_Name.xhtml

# Check specific folder
docinator diff path/to/folder/

# Show actual diff content
docinator diff path/to/file.xhtml --show-diff

# Use git diff for better visualization
docinator diff path/to/file.xhtml --git
```

### Push Changes

Upload your local changes to Confluence:

```bash
# Push a single file
docinator push path/to/Page_Name.xhtml

# Push with a version message
docinator push path/to/Page_Name.xhtml -m "Updated API documentation"

# Push all files in a folder
docinator push path/to/folder/

# Force push (override conflicts)
docinator push path/to/file.xhtml --force
```

### Resolve Conflicts

When both local and remote have changes:

```bash
# Keep your local version
docinator resolve path/to/file.xhtml --strategy local

# Use the remote version
docinator resolve path/to/file.xhtml --strategy remote

# Create merge file for manual resolution
docinator resolve path/to/file.xhtml --strategy merge
```

### Test Connection

Verify your Confluence credentials are working:

```bash
docinator test
```

## Workflow Example

Here's a typical workflow:

```bash
# 1. Initialize and pull the documentation
docinator pull "https://wiki.inside.milvian.group/wiki/spaces/Aqueduct/folder/731349007"

# 2. Edit files locally with your favorite editor
code confluence_pages/

# 3. Check status
docinator status

# 4. View what you've changed
docinator diff path/to/edited_file.xhtml --show-diff

# 5. Check for remote changes before pushing
docinator diff

# 6. If there are conflicts, resolve them
docinator resolve path/to/file.xhtml --strategy local

# 7. Push your changes
docinator push path/to/edited_file.xhtml -m "Updated documentation"
```

## File Format

Pages are stored in Confluence Storage Format (XHTML) with `.xhtml` extension. This is the native format Confluence uses, ensuring lossless round-tripping.

Example structure:

```
confluence_pages/
├── .confluence/
│   ├── config.json       # Sync configuration
│   ├── index.json        # Page tracking index
│   └── pages/
│       └── {page_id}.json # Individual page metadata
├── Parent_Page.xhtml
├── Subfolder/
│   ├── Child_Page_1.xhtml
│   └── Child_Page_2.xhtml
└── Another_Folder/
    └── Another_Page.xhtml
```

## Best Practices

1. **Always check diff before pushing** to avoid overwriting others' changes
2. **Pull regularly** to stay in sync with remote changes
3. **Use meaningful version messages** when pushing (`-m "Description"`)
4. **Commit your changes to git** alongside the Confluence sync for version history
5. **Don't edit `.confluence/` directory** manually

## Troubleshooting

### "Not a docinator repository"

Run `docinator init <url>` first, or navigate to a directory that was previously initialized.

### "Authentication failed"

1. Check your `CONFLUENCE_USERNAME` (should be your email)
2. Verify your API token is valid
3. Ensure `CONFLUENCE_BASE_URL` ends with `/wiki`

### "Could not parse URL"

Make sure the URL is a Confluence page or folder URL. Supported formats:

- `https://domain.atlassian.net/wiki/spaces/SPACE/folder/123456`
- `https://domain.atlassian.net/wiki/spaces/SPACE/pages/123456/Page+Title`

### Conflicts

If you get conflicts, use `docinator diff <file> --show-diff` to see what's different, then:

- `--strategy local`: Keep your local version
- `--strategy remote`: Accept the remote version
- `--strategy merge`: Create a merge file with conflict markers

## Contributing

Contributions welcome! Please feel free to submit issues and pull requests.

## License

MIT License
