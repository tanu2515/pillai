# VYAVASTHA — PS-8 Mega-Event Hospitality Orchestration

Working prototype backend + dashboards. See `WINNING_PLAN.md` for the full strategy, Q&A prep, and remaining checklist.

## Run it

```
cd backend
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001
```

- Operator Command Center: http://localhost:8001/
- Attendee view: http://localhost:8001/attendee.html
- From a phone on the same WiFi: `http://<this-machine's-LAN-IP>:8001/` (find the IP with `ipconfig`)

Data lives in `backend/vyavastha.db` (SQLite, created automatically, seeded with the SRS's own Appendix scenario — Mumbai Global Festival).

## What's implemented

- **Risk Engine** (SRS Section 8.2 formula) — capacity pressure, arrival surge, flow instability, resource pressure, time-to-criticality, computed live per zone.
- **Deterministic session-release scenario** — `Trigger Session Release` in the dashboard reproduces the SRS Appendix numbers (Gate 2 → ~115%, Corridor B → ~108%, Hotel A → ~97% after 11 simulated minutes).
- **What-If Simulator** — genuinely recomputes from arbitrary input (redirect count, open Gate 3, add buses, move staff); rejects/caps a redirect that would overload the receiving gate.
- **Orchestration/Action Optimizer** (Section 16 formula) — ranks candidate actions and hard-filters any action whose required resources (buses/staff) aren't actually available.
- **Registration & Check-In** (Section 7.2 of the plan) — register, get a code, check in; check-in enforces capacity and suggests an alternate gate when full.
- **Two frontends**: `index.html` (operator, desktop-first but responsive) and `attendee.html` (mobile-first).

## Not yet wired up

- LLM explanation layer (Section 8's grounded copilot).

## Optional live YOLO crowd detection

The main app runs without computer-vision packages. To enable a local IP
camera/video feed for a venue zone, install the optional worker dependencies:

```
cd backend
python -m pip install -r requirements-yolo.txt
```

Start a feed with `POST /api/cameras`, for example:

```json
{"zone_id": 1, "stream_url": "http://192.168.1.42:8080/video", "model_path": "yolov8n.pt", "sample_seconds": 1}
```

The worker counts COCO's `person` class, writes each observation to
`crowd_snapshots`, and updates that zone's existing risk/alert pipeline.
Use `GET /api/cameras` to inspect it, `DELETE /api/cameras/1` to stop it, and
`GET /api/zones/1/crowd-history` to inspect the source-tagged count history.
Without a feed, existing check-ins, manual count posts, and scenarios continue
to work normally.
