# Confluence Docinator

A CLI tool for syncing Confluence pages with local files, enabling a git-like
pull / diff / push workflow for documentation management.

## Features

- **Pull** – Download all pages from a Confluence folder, maintaining hierarchy
- **Push** – Upload local changes back to Confluence (with version messages)
- **Push all** – `--all` to push every locally-modified page in one command
- **Dry run** – `--dry-run` on any push to preview changes before sending
- **Diff** – Compare local files against Confluence before pushing
- **Resolve** – Handle conflicts between local and remote changes
- **Status** – See every changed file by name with ready-to-copy commands
- **New** – Scaffold a new `.md` file locally, then publish with `create`
- **Create** – Push a local file to Confluence as a brand-new page
- **Labels** – Pull and sync Confluence page labels automatically
- **Macros** – Preserve Confluence-specific macro content through round-trips
- **Tab completion** – Shell completion for all commands and file paths

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

### Environment Variables

| Variable                    | Required | Description                                       |
| --------------------------- | -------- | ------------------------------------------------- |
| `CONFLUENCE_BASE_URL`       | ✅       | Your instance URL, ending with `/wiki`            |
| `CONFLUENCE_USERNAME`       | ✅       | Your Confluence email address                     |
| `CONFLUENCE_API_KEY`        | ✅       | API token from the link above                     |
| `CONFLUENCE_SPACE_KEY`      | ✓        | Space key — extracted from URL automatically      |
| `CONFLUENCE_TARGET_URL`     | —        | Default folder URL used when no URL arg is passed |
| `CONFLUENCE_EDITOR_VERSION` | —        | Confluence editor version (default: `2`)          |

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
docinator push --all                # push every locally-modified page
docinator push path/to/Page.md --dry-run   # preview without sending
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

### `docinator new`

Scaffold a new `.md` file locally. Does not touch Confluence until you run
`create`.

```bash
docinator new "Security Policy"               # creates Security Policy.md in cwd
docinator new "Access Review" --dir Security/ # place in a specific subfolder
docinator new "Draft" --publish               # scaffold AND publish immediately
```

### `docinator create`

Publish an existing local `.md` file as a brand-new Confluence page:

```bash
docinator create Security/Security_Policy.md
docinator create orphan.md --parent Security/Overview.md
docinator create my_file.md --title "Human Readable Title"
```

Parent page is auto-discovered from sibling pages in the same directory.
Override with `--parent path/to/Parent.md`.

### `docinator completion`

Print shell tab-completion setup instructions:

```bash
docinator completion       # shows both zsh and bash instructions
docinator completion zsh   # zsh only
docinator completion bash  # bash only
```

For zsh, add this line once to `~/.zshrc`:

```bash
eval "$(register-python-argcomplete docinator)"
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
# After the first pull, re-sync with just:
docinator pull

# 3. Edit files locally
code confluence_pages/

# 4. Review changes
docinator status                                  # see every changed file by name
docinator diff path/to/Page.md --show-diff        # inline diff

# 5. Preview before pushing
docinator push --all --dry-run

# 6. Resolve any conflicts
docinator resolve path/to/Page.md --strategy local

# 7. Push changes back
docinator push --all -m "Sprint 42 doc updates"  # push everything modified
docinator push path/to/Page.md -m "Single page"  # or just one file

# 8. Add a new page
docinator new "Runbook: Deployment"
# edit the scaffolded file, then:
docinator create confluence_pages/Runbook_Deployment.md
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
