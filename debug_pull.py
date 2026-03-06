#!/usr/bin/env python3
"""Debug script to test Confluence API calls."""

from confluence_docinator.client import ConfluenceClient
from confluence_docinator.models import SyncConfig
import os
from dotenv import load_dotenv

load_dotenv()

config = SyncConfig(
    base_url=os.getenv('CONFLUENCE_BASE_URL'),
    username=os.getenv('CONFLUENCE_USERNAME'),
    api_key=os.getenv('CONFLUENCE_API_KEY'),
    space_key=os.getenv('CONFLUENCE_SPACE_KEY'),
)

client = ConfluenceClient(config)

# Test parsing the URL
url = 'https://wiki.inside.milvian.group/wiki/spaces/Aqueduct/folder/731349007'
space_key, content_id, content_type = client.parse_confluence_url(url)
print(f'Parsed URL: space={space_key}, id={content_id}, type={content_type}')

# Try to get the page/folder
print(f'\nUsing base_url: {config.base_url}')
print(f'Trying to get content {content_id}...')

info = client.get_page(content_id)
if info:
    print(f'Got page: {info.get("title", "Unknown")}')
    print(f'Type: {info.get("type", "Unknown")}')
else:
    print('Page not found!')

# Try getting children
print('\nGetting children...')
children = client.get_child_pages(content_id)
print(f'Found {len(children)} children')
for c in children[:10]:
    print(f'  - {c.get("title", "Unknown")} (id: {c.get("id")})')

# Try getting descendants
print('\nGetting descendants...')
descendants = client.get_descendants(content_id)
print(f'Found {len(descendants)} descendants')
for d in descendants[:10]:
    print(
        f'  - {d.get("title", "Unknown")} (id: {d.get("id")}, path: {d.get("_local_path", "")})')

# Check the first child for nested content
if children:
    child_id = children[0].get("id")
    print(f'\nChecking children of first child ({child_id})...')
    sub_children = client.get_child_pages(child_id)
    print(f'Found {len(sub_children)} sub-children')
    for sc in sub_children[:10]:
        print(f'  - {sc.get("title", "Unknown")} (id: {sc.get("id")})')

# Also try to get the page body content
print(f'\nGetting content of first child...')
content, metadata = client.get_page_content(child_id)
if content:
    print(f'Content length: {len(content)} characters')
    print(f'Content preview: {content[:500]}...')
else:
    print('No content found!')
