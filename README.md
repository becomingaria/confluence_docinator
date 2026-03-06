# Confluence Docinator

A CLI tool for syncing Confluence pages with local files, enabling a git-like
pull / diff / push workflow for documentation management.

## Features

- **Pull** – Download all pages from a Confluence folder, maintaining hierarchy
- **Push** – Upload local changes back to Confluence (with version messages)
- **Diff** – Compare local files against Confluence before pushing
- **Resolve** – Handle conflicts between local and remote changes
- **Status** – See a summary of local vs. remote state at a glance
- **Labels** – Pull and sync Confluence page labels automatically
- **Macros** – Preserve Confluence-specific macro content through round-trips

## Installation

### From Source

```bash
git clone https://github.com/becomingaria/confluence_docinator.git
cd confluence_docinator
pip install -e .
```

### Using pip (once published)

```bash
pip install confluence-docinator
```

## Getting Started

Run `docinator setup` in any directory to create a template `example.env` and
usage `README.md` for that workspace:

```bash
mkdir my-docs && cd my-docs
docinator setup
```

Then configure your credentials:

```bash
cp example.env .env
# edit .env with your Confluence URL, username, and API token
```

### Getting an API Token

1. Go to https://id.atlassian.com/manage-profile/security/api-tokens
2. Click **Create API token**, give it a name, and copy the value into `.env`.

---

## Commands

### `docinator setup`

Bootstrap a new workspace — creates `example.env` and `README.md` in the
current directory. Prompts before overwriting existing files.

```bash
docinator setup
```

### `docinator test`

Verify your credentials and Confluence connection:

```bash
docinator test
```

### `docinator pull`

Download all pages from a Confluence folder:

```bash
docinator pull "https://your-domain.atlassian.net/wiki/spaces/SPACE/folder/123456789"
```

Options:

- `-o, --output <dir>` – output directory (default: `./confluence_pages`)
- `-f, --force` – overwrite local changes without prompting
- `--format md|xhtml` – file format (default: `md`)

### `docinator status`

See what has changed locally vs. remotely:

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

### `docinator diff`

Check what is different between local and remote:

```bash
docinator diff                              # all files
docinator diff path/to/Page.md             # single file
docinator diff path/to/folder/             # folder
docinator diff path/to/Page.md --show-diff # inline diff output
docinator diff path/to/Page.md --git       # git-style diff
```

### `docinator push`

Upload local changes to Confluence:

```bash
docinator push path/to/Page.md
docinator push path/to/Page.md -m "Updated API documentation"
docinator push path/to/folder/
docinator push path/to/Page.md --force     # override conflicts
```

### `docinator resolve`

When both local and remote have changed:

```bash
docinator resolve path/to/Page.md --strategy local   # keep your version
docinator resolve path/to/Page.md --strategy remote  # use Confluence version
docinator resolve path/to/Page.md --strategy merge   # manual merge file
```

---

## Typical Workflow

```bash
# 1. Set up a new workspace
mkdir my-docs && cd my-docs
docinator setup
cp example.env .env   # fill in credentials

# 2. Pull documentation from Confluence
docinator pull "https://your-domain.atlassian.net/wiki/spaces/SPACE/folder/123456789"

# 3. Edit files locally
code confluence_pages/

# 4. Review changes
docinator status
docinator diff path/to/Page.md --show-diff

# 5. Check for remote changes before pushing
docinator diff

# 6. Resolve any conflicts
docinator resolve path/to/Page.md --strategy local

# 7. Push changes back
docinator push path/to/Page.md -m "Updated documentation"
```

---

## Repository Structure

```
confluence_pages/
├── .confluence/
│   ├── config.json             # sync configuration
│   ├── index.json              # page tracking index
│   ├── pages/{id}.json         # per-page metadata & labels
│   ├── macros/{id}.json        # preserved Confluence macros
│   └── attachments/            # attachment metadata
├── Parent_Page.md
├── Subfolder/
│   ├── Child_Page_1.md
│   └── Child_Page_2.md
└── _images/
    └── some_diagram.png
```

---

## File Format

Pages default to **Markdown** (`.md`). Native Confluence XHTML (`.xhtml`) is
also supported via `--format xhtml`.

Macro-rich content (layouts, ADF extensions, inline comments, etc.) is
preserved as `<!-- CONFLUENCE_MACRO_N: name -->` placeholders and restored on
push from the macro store in `.confluence/macros/`.

---

## Best Practices

1. **Always diff before pushing** to avoid overwriting others' changes.
2. **Pull regularly** to stay in sync with remote changes.
3. **Use meaningful version messages** (`-m "Description"`).
4. **Commit pulled pages to git** alongside source code for version history.
5. **Never edit `.confluence/` manually.**

---

## Troubleshooting

### "Not a docinator repository"

Navigate to a directory initialized with `docinator pull` or `docinator init`.

### "Authentication failed"

- `CONFLUENCE_USERNAME` should be your email address.
- Verify your API token is current.
- Ensure `CONFLUENCE_BASE_URL` ends with `/wiki`.

### "Could not parse URL"

Supported URL formats:

- `https://domain.atlassian.net/wiki/spaces/SPACE/folder/123456789`
- `https://domain.atlassian.net/wiki/spaces/SPACE/pages/123456789/Page+Title`

### Conflicts

Use `docinator diff <file> --show-diff` to inspect differences, then:

- `--strategy local` – keep your local version
- `--strategy remote` – accept the remote version
- `--strategy merge` – create a merge file with conflict markers

---

## Contributing

Contributions welcome! Please open an issue or submit a pull request.

## License

MIT License
