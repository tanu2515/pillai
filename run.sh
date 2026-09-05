#!/usr/bin/env bash
cd "$(dirname "$0")/backend"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001
