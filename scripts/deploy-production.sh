#!/bin/bash
# Deploy to production
set -e

echo "Running tests before production deploy..."
cd "$(dirname "$0")/.."
python3 -m pytest tests/ -v --tb=short

if [ $? -ne 0 ]; then
    echo "Tests failed. Aborting production deploy."
    exit 1
fi

echo "About to deploy to PRODUCTION."
read -p "Are you sure? (y/N) " confirm
if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
    echo "Aborted."
    exit 0
fi

echo "Deploying to production..."
vercel --prod

echo "Production deployed!"
