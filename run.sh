#!/usr/bin/env bash
cd "$(dirname "$0")/backend"
echo ""
echo "  VYAVASTHA is starting..."
echo "  Open this link in your browser: http://localhost:8001"
echo ""
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001
