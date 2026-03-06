#!/usr/bin/env python3
"""Explore Confluence space to find all content."""

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

# Use CQL to search for all pages with the parent
parent_id = "731349007"
space_key = "Aqueduct"

print(f"Searching for pages in space {space_key}...")
print()

# Method 1: CQL search for pages with this ancestor
url = client._api_v1("content/search")
cql = f"ancestor={parent_id}"
params = {
    "cql": cql,
    "limit": 100,
    "expand": "ancestors,version",
}

print(f"CQL: {cql}")
response = client.session.get(url, params=params)
print(f"Status: {response.status_code}")

if response.status_code == 200:
    data = response.json()
    results = data.get("results", [])
    print(f"Found {len(results)} pages with ancestor {parent_id}")
    for r in results:
        ancestors = " > ".join([a.get("title", "")
                               for a in r.get("ancestors", [])])
        print(f"  - {r.get('title')} (id: {r.get('id')}, type: {r.get('type')})")
        print(f"    Ancestors: {ancestors}")
else:
    print(f"Error: {response.text}")

print()
print("=" * 60)
print()

# Method 2: Search for all pages in the space
print(f"Searching for all pages in space {space_key}...")
cql2 = f"space={space_key} and type=page"
params2 = {
    "cql": cql2,
    "limit": 100,
    "expand": "ancestors",
}

response2 = client.session.get(url, params=params2)
print(f"Status: {response2.status_code}")

if response2.status_code == 200:
    data2 = response2.json()
    results2 = data2.get("results", [])
    print(f"Found {len(results2)} pages in space {space_key}")
    for r in results2:
        ancestors = " > ".join([a.get("title", "")
                               for a in r.get("ancestors", [])])
        print(f"  - {r.get('title')} (id: {r.get('id')})")
        if ancestors:
            print(f"    Path: {ancestors}")
else:
    print(f"Error: {response2.text}")

print()
print("=" * 60)
print()

# Method 3: Try v2 API for child pages (different structure)
print("Trying Confluence API v2...")
url_v2 = client._api_v2(f"pages/{parent_id}/children")
response3 = client.session.get(url_v2)
print(f"Status: {response3.status_code}")
if response3.status_code == 200:
    data3 = response3.json()
    results3 = data3.get("results", [])
    print(f"Found {len(results3)} children via v2 API")
    for r in results3:
        print(
            f"  - {r.get('title')} (id: {r.get('id')}, status: {r.get('status')})")
else:
    print(f"Error: {response3.text[:500]}")
