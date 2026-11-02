#!/bin/bash

source envsubtrans/bin/activate
python scripts/sync_version.py
pip install --upgrade -e ".[openai,gemini,claude]"

python scripts/update_translations.py

echo "Building distribution package..."
python -m build

echo "Distribution package built successfully in dist/ directory"
