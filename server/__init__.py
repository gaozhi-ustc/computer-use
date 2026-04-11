"""Workflow Recorder collection server.

FastAPI + SQLite service that receives per-frame analysis results from
clients (one per employee) and stores them for later inspection or export.
Run with:

    uvicorn server.app:app --host 0.0.0.0 --port 8000
"""
