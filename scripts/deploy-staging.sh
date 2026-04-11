#!/bin/bash
# Deploy to staging environment
set -e

echo "Running tests before staging deploy..."
cd "$(dirname "$0")/.."
python3 -m pytest tests/ -v --tb=short

if [ $? -ne 0 ]; then
    echo "Tests failed. Aborting staging deploy."
    exit 1
fi

echo "Tests passed. Deploying to staging..."
vercel --env STAGING=true

echo "Staging deployed. Check the preview URL above."
