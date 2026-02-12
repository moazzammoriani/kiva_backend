#!/bin/bash
# Start TinaCMS content API + FastAPI together for local CMS editing.
#
# TinaCMS content API runs at localhost:9000 (GraphQL at localhost:4001)
# FastAPI runs at localhost:8000 (serves site + admin UI from dist/)
#
# Workflow:
#   1. Edit content at localhost:8000/admin/
#   2. Save in TinaCMS → updates JSON on disk
#   3. POST localhost:8000/api/rebuild → rebuilds static site
#   4. Refresh the page to see changes

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
KIVA_DIR="$SCRIPT_DIR/../kiva"

cd "$KIVA_DIR" && npx tinacms dev -c "npx astro build && cd '$SCRIPT_DIR' && uv run fastapi dev main.py"
