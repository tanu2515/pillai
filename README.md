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

- Real YOLO camera detection (from `crowd-prediction-ai`) — intentionally held off until the teammate's remaining KAIRO code (the prior hackathon build this project is built on) arrives, per the integration point noted in `WINNING_PLAN.md` Section 7. The phone-as-IP-camera setup (Section 7.1) is documented and ready to wire into `engine.py` once that code lands.
- LLM explanation layer (Section 8's grounded copilot).
