#!/bin/bash

source ./envsubtrans/bin/activate
python scripts/sync_version.py
pip3 install --upgrade pip
pip install --upgrade setuptools build wheel
pip install --upgrade -e ".[openai,gemini,claude]"

./envsubtrans/bin/python scripts/update_translations.py

./envsubtrans/bin/python tests/unit_tests.py
if [ $? -ne 0 ]; then
    echo "Unit tests failed. Exiting..."
    exit $?
fi

echo "Building distribution package..."
./envsubtrans/bin/python -m build

echo "Distribution package built successfully in dist/ directory"
