"""
Converter module for Confluence XHTML ↔ Markdown conversion.

Handles conversion while preserving Confluence-specific macros where possible.
"""

import re
import html
from typing import Tuple, Dict, List, Optional
from html.parser import HTMLParser
from io import StringIO


class ConfluenceToMarkdownConverter:
    """
    Convert Confluence storage format (XHTML) to Markdown.

    Preserves Confluence macros as HTML comments so they can be restored on push.
    """

    # Confluence macro patterns
    MACRO_PATTERN = re.compile(
        r'<ac:structured-macro[^>]*ac:name="([^"]*)"[^>]*>(.*?)</ac:structured-macro>',
        re.DOTALL
    )

    def __init__(self):
        self.macro_store: Dict[str, str] = {}
        self.macro_counter = 0

    def convert(self, xhtml: str) -> Tuple[str, Dict[str, str]]:
        """
        Convert Confluence XHTML to Markdown.

        Returns:
            Tuple of (markdown_content, macro_store)
            macro_store maps placeholder IDs to original macro HTML
        """
        self.macro_store = {}
        self.macro_counter = 0

        if not xhtml or not xhtml.strip():
            return "", {}

        # Step 1: Extract and preserve Confluence macros
        content = self._preserve_macros(xhtml)

        # Step 2: Convert HTML to Markdown
        markdown = self._html_to_markdown(content)

        # Step 3: Clean up the markdown
        markdown = self._cleanup_markdown(markdown)

        return markdown, self.macro_store

    def _preserve_macros(self, xhtml: str) -> str:
        """Extract Confluence macros and replace with placeholders."""

        def replace_macro(match):
            macro_name = match.group(1)
            macro_content = match.group(0)

            self.macro_counter += 1
            placeholder_id = f"CONFLUENCE_MACRO_{self.macro_counter}"
            self.macro_store[placeholder_id] = macro_content

            # Return a visible placeholder in markdown
            return f"\n\n<!-- {placeholder_id}: {macro_name} -->\n\n"

        content = self.MACRO_PATTERN.sub(replace_macro, xhtml)

        # Also preserve ac:image tags
        image_pattern = re.compile(r'<ac:image[^>]*>.*?</ac:image>', re.DOTALL)

        def replace_image(match):
            self.macro_counter += 1
            placeholder_id = f"CONFLUENCE_MACRO_{self.macro_counter}"
            self.macro_store[placeholder_id] = match.group(0)

            # Try to extract alt text or filename
            alt_match = re.search(r'ac:alt="([^"]*)"', match.group(0))
            filename_match = re.search(
                r'ri:filename="([^"]*)"', match.group(0))

            desc = alt_match.group(1) if alt_match else (
                filename_match.group(1) if filename_match else "image"
            )

            return f"\n\n![{desc}](<!-- {placeholder_id} -->)\n\n"

        content = image_pattern.sub(replace_image, content)

        # Preserve ac:link tags
        link_pattern = re.compile(r'<ac:link[^>]*>.*?</ac:link>', re.DOTALL)

        def replace_link(match):
            self.macro_counter += 1
            placeholder_id = f"CONFLUENCE_MACRO_{self.macro_counter}"
            self.macro_store[placeholder_id] = match.group(0)

            # Try to extract link text
            text_match = re.search(
                r'<ac:link-body>([^<]*)</ac:link-body>', match.group(0))
            title_match = re.search(
                r'ri:content-title="([^"]*)"', match.group(0))

            text = text_match.group(1) if text_match else (
                title_match.group(1) if title_match else "link"
            )

            return f"[{text}](<!-- {placeholder_id} -->)"

        content = link_pattern.sub(replace_link, content)

        # Preserve ri:attachment tags
        attachment_pattern = re.compile(r'<ri:attachment[^>]*/>', re.DOTALL)

        def replace_attachment(match):
            self.macro_counter += 1
            placeholder_id = f"CONFLUENCE_MACRO_{self.macro_counter}"
            self.macro_store[placeholder_id] = match.group(0)

            filename_match = re.search(
                r'ri:filename="([^"]*)"', match.group(0))
            filename = filename_match.group(
                1) if filename_match else "attachment"

            return f"[📎 {filename}](<!-- {placeholder_id} -->)"

        content = attachment_pattern.sub(replace_attachment, content)

        return content

    def _html_to_markdown(self, html_content: str) -> str:
        """Convert HTML to Markdown."""

        # Handle headings
        for i in range(6, 0, -1):
            pattern = re.compile(
                f'<h{i}[^>]*>(.*?)</h{i}>', re.DOTALL | re.IGNORECASE)
            html_content = pattern.sub(
                lambda m: f"\n\n{'#' * i} {self._strip_tags(m.group(1)).strip()}\n\n", html_content)

        # Handle paragraphs
        html_content = re.sub(
            r'<p[^>]*>(.*?)</p>', lambda m: f"\n\n{m.group(1)}\n\n", html_content, flags=re.DOTALL)

        # Handle bold
        html_content = re.sub(
            r'<strong[^>]*>(.*?)</strong>', r'**\1**', html_content, flags=re.DOTALL)
        html_content = re.sub(r'<b[^>]*>(.*?)</b>',
                              r'**\1**', html_content, flags=re.DOTALL)

        # Handle italic
        html_content = re.sub(r'<em[^>]*>(.*?)</em>',
                              r'*\1*', html_content, flags=re.DOTALL)
        html_content = re.sub(r'<i[^>]*>(.*?)</i>',
                              r'*\1*', html_content, flags=re.DOTALL)

        # Handle code (inline)
        html_content = re.sub(
            r'<code[^>]*>(.*?)</code>', r'`\1`', html_content, flags=re.DOTALL)

        # Handle preformatted text
        html_content = re.sub(
            r'<pre[^>]*>(.*?)</pre>', lambda m: f"\n```\n{self._strip_tags(m.group(1))}\n```\n", html_content, flags=re.DOTALL)

        # Handle links
        def convert_link(match):
            href = re.search(r'href="([^"]*)"', match.group(0))
            text = self._strip_tags(match.group(1))
            if href:
                return f"[{text}]({href.group(1)})"
            return text

        html_content = re.sub(r'<a[^>]*>(.*?)</a>',
                              convert_link, html_content, flags=re.DOTALL)

        # Handle unordered lists
        html_content = re.sub(r'<ul[^>]*>', '\n', html_content)
        html_content = re.sub(r'</ul>', '\n', html_content)
        html_content = re.sub(
            r'<li[^>]*>(.*?)</li>', lambda m: f"- {self._strip_tags(m.group(1)).strip()}\n", html_content, flags=re.DOTALL)

        # Handle ordered lists
        html_content = re.sub(r'<ol[^>]*>', '\n', html_content)
        html_content = re.sub(r'</ol>', '\n', html_content)

        # Handle blockquotes
        html_content = re.sub(r'<blockquote[^>]*>(.*?)</blockquote>', lambda m: '\n> ' + m.group(
            1).replace('\n', '\n> ') + '\n', html_content, flags=re.DOTALL)

        # Handle horizontal rules
        html_content = re.sub(r'<hr[^>]*/?>',  '\n---\n', html_content)

        # Handle line breaks
        html_content = re.sub(r'<br[^>]*/?>',  '\n', html_content)

        # Handle tables (basic)
        html_content = self._convert_tables(html_content)

        # Strip remaining HTML tags (but keep content)
        html_content = self._strip_tags(html_content)

        # Decode HTML entities
        html_content = html.unescape(html_content)

        return html_content

    def _convert_tables(self, content: str) -> str:
        """Convert HTML tables to Markdown tables."""
        table_pattern = re.compile(
            r'<table[^>]*>(.*?)</table>', re.DOTALL | re.IGNORECASE)

        def convert_table(match):
            table_html = match.group(1)

            # Extract rows
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>',
                              table_html, re.DOTALL | re.IGNORECASE)
            if not rows:
                return match.group(0)

            md_rows = []
            for i, row in enumerate(rows):
                # Extract cells (th or td)
                cells = re.findall(
                    r'<t[hd][^>]*>(.*?)</t[hd]>', row, re.DOTALL | re.IGNORECASE)
                cells = [self._strip_tags(c).strip().replace(
                    '|', '\\|').replace('\n', ' ') for c in cells]

                if cells:
                    md_rows.append('| ' + ' | '.join(cells) + ' |')

                    # Add header separator after first row
                    if i == 0:
                        md_rows.append(
                            '| ' + ' | '.join(['---'] * len(cells)) + ' |')

            return '\n\n' + '\n'.join(md_rows) + '\n\n'

        return table_pattern.sub(convert_table, content)

    def _strip_tags(self, html_content: str) -> str:
        """Remove HTML tags but keep content and placeholders."""
        # Keep our placeholders
        placeholders = re.findall(
            r'<!-- CONFLUENCE_MACRO_\d+ -->', html_content)

        # Strip tags
        result = re.sub(r'<[^>]+>', '', html_content)

        return result

    def _cleanup_markdown(self, markdown: str) -> str:
        """Clean up the markdown output."""
        # Remove excessive newlines
        markdown = re.sub(r'\n{3,}', '\n\n', markdown)

        # Remove leading/trailing whitespace from lines
        lines = [line.rstrip() for line in markdown.splitlines()]
        markdown = '\n'.join(lines)

        # Remove leading/trailing newlines
        markdown = markdown.strip()

        return markdown


class MarkdownToConfluenceConverter:
    """
    Convert Markdown back to Confluence storage format (XHTML).

    Restores preserved macros from placeholders.
    """

    def __init__(self):
        pass

    def convert(self, markdown: str, macro_store: Dict[str, str] = None) -> str:
        """
        Convert Markdown to Confluence XHTML.

        Args:
            markdown: The markdown content
            macro_store: Dictionary mapping placeholder IDs to original macro HTML

        Returns:
            Confluence storage format XHTML
        """
        if not markdown or not markdown.strip():
            return ""

        macro_store = macro_store or {}

        # Step 1: Convert Markdown to HTML
        xhtml = self._markdown_to_html(markdown)

        # Step 2: Restore Confluence macros
        xhtml = self._restore_macros(xhtml, macro_store)

        return xhtml

    def _markdown_to_html(self, markdown: str) -> str:
        """Convert Markdown to HTML."""
        lines = markdown.split('\n')
        html_lines = []
        in_code_block = False
        in_list = False
        code_content = []

        i = 0
        while i < len(lines):
            line = lines[i]

            # Handle code blocks
            if line.startswith('```'):
                if in_code_block:
                    html_lines.append(
                        f'<pre><code>{html.escape(chr(10).join(code_content))}</code></pre>')
                    code_content = []
                    in_code_block = False
                else:
                    in_code_block = True
                i += 1
                continue

            if in_code_block:
                code_content.append(line)
                i += 1
                continue

            # Handle headers
            header_match = re.match(r'^(#{1,6})\s+(.+)$', line)
            if header_match:
                level = len(header_match.group(1))
                text = self._convert_inline(header_match.group(2))
                html_lines.append(f'<h{level}>{text}</h{level}>')
                i += 1
                continue

            # Handle horizontal rules
            if re.match(r'^(-{3,}|_{3,}|\*{3,})$', line.strip()):
                html_lines.append('<hr/>')
                i += 1
                continue

            # Handle unordered lists
            list_match = re.match(r'^[-*+]\s+(.+)$', line)
            if list_match:
                if not in_list:
                    html_lines.append('<ul>')
                    in_list = True
                html_lines.append(
                    f'<li>{self._convert_inline(list_match.group(1))}</li>')
                i += 1
                continue
            else:
                if in_list:
                    html_lines.append('</ul>')
                    in_list = False

            # Handle ordered lists
            ol_match = re.match(r'^(\d+)\.\s+(.+)$', line)
            if ol_match:
                html_lines.append(
                    f'<li>{self._convert_inline(ol_match.group(2))}</li>')
                i += 1
                continue

            # Handle blockquotes
            if line.startswith('>'):
                quote_text = line[1:].strip()
                html_lines.append(
                    f'<blockquote><p>{self._convert_inline(quote_text)}</p></blockquote>')
                i += 1
                continue

            # Handle tables
            if '|' in line and i + 1 < len(lines) and re.match(r'^\|[\s\-:|]+\|$', lines[i + 1]):
                table_lines = [line]
                i += 1
                while i < len(lines) and '|' in lines[i]:
                    table_lines.append(lines[i])
                    i += 1
                html_lines.append(self._convert_table(table_lines))
                continue

            # Handle paragraphs (non-empty lines)
            if line.strip():
                html_lines.append(f'<p>{self._convert_inline(line)}</p>')

            i += 1

        # Close any open list
        if in_list:
            html_lines.append('</ul>')

        return '\n'.join(html_lines)

    def _convert_inline(self, text: str) -> str:
        """Convert inline markdown elements."""
        # Preserve placeholders
        placeholder_pattern = re.compile(
            r'<!-- (CONFLUENCE_MACRO_\d+)[^>]* -->')

        # Bold
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'__(.+?)__', r'<strong>\1</strong>', text)

        # Italic
        text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
        text = re.sub(r'_(.+?)_', r'<em>\1</em>', text)

        # Inline code
        text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)

        # Links (but not placeholder links)
        def convert_link(match):
            link_text = match.group(1)
            link_url = match.group(2)

            # Check if this is a placeholder
            if '<!-- CONFLUENCE_MACRO_' in link_url:
                return match.group(0)  # Keep as-is, will be restored later

            return f'<a href="{link_url}">{link_text}</a>'

        text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', convert_link, text)

        # Images (but not placeholder images)
        def convert_image(match):
            alt_text = match.group(1)
            img_url = match.group(2)

            # Check if this is a placeholder
            if '<!-- CONFLUENCE_MACRO_' in img_url:
                return match.group(0)  # Keep as-is, will be restored later

            return f'<ac:image><ri:url ri:value="{img_url}"/></ac:image>'

        text = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', convert_image, text)

        return text

    def _convert_table(self, lines: List[str]) -> str:
        """Convert markdown table to HTML."""
        if len(lines) < 2:
            return ''

        html_parts = ['<table>']

        for i, line in enumerate(lines):
            if i == 1 and re.match(r'^\|[\s\-:|]+\|$', line):
                continue  # Skip separator row

            cells = [c.strip() for c in line.split(
                '|')[1:-1]]  # Remove empty first/last

            tag = 'th' if i == 0 else 'td'
            row_tag = 'thead' if i == 0 else 'tbody'

            if i == 0:
                html_parts.append('<thead>')
            elif i == 2:
                html_parts.append('<tbody>')

            html_parts.append('<tr>')
            for cell in cells:
                html_parts.append(
                    f'<{tag}>{self._convert_inline(cell)}</{tag}>')
            html_parts.append('</tr>')

            if i == 0:
                html_parts.append('</thead>')

        if len(lines) > 2:
            html_parts.append('</tbody>')

        html_parts.append('</table>')

        return ''.join(html_parts)

    def _restore_macros(self, xhtml: str, macro_store: Dict[str, str]) -> str:
        """Restore Confluence macros from placeholders."""

        for placeholder_id, original_macro in macro_store.items():
            # Pattern: <!-- PLACEHOLDER_ID: macro_name -->
            pattern = re.compile(f'<!-- {placeholder_id}[^>]* -->')
            xhtml = pattern.sub(original_macro, xhtml)

            # Also handle image/link placeholders that may be in markdown image/link syntax
            # ![alt](<!-- PLACEHOLDER_ID -->)
            img_pattern = re.compile(
                f'!\\[[^\\]]*\\]\\(<!-- {placeholder_id} -->\\)')
            xhtml = img_pattern.sub(original_macro, xhtml)

            # [text](<!-- PLACEHOLDER_ID -->)
            link_pattern = re.compile(
                f'\\[[^\\]]*\\]\\(<!-- {placeholder_id} -->\\)')
            xhtml = link_pattern.sub(original_macro, xhtml)

        return xhtml


# Convenience functions

def xhtml_to_markdown(xhtml: str) -> Tuple[str, Dict[str, str]]:
    """Convert Confluence XHTML to Markdown."""
    converter = ConfluenceToMarkdownConverter()
    return converter.convert(xhtml)


def markdown_to_xhtml(markdown: str, macro_store: Dict[str, str] = None) -> str:
    """Convert Markdown to Confluence XHTML."""
    converter = MarkdownToConfluenceConverter()
    return converter.convert(markdown, macro_store)
