# VYAVASTHA / KAIRO — Full System Reference

Team Byte Alchemy · Pillai Hackathon PS-8 · Theme: Hospitality & Travel
Repo: `C:\Users\Admin\Documents\kairo`

---

## 0. Overview

VYAVASTHA consolidates an event's venue, transport and hospitality data into one live view, scores crowd risk per zone every few seconds, lets an operator test an intervention before committing to it, and separately runs a full BookMyShow-style browse/book/check-in flow for attendees. It's one FastAPI backend serving a JSON API + a folder of plain HTML dashboards, plus a standalone Expo mobile app hitting the same API.

**Run it:**

```
cd backend
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --port 8001
```

Command Centre → `localhost:8001/` · Attendee Home → `/events.html`. Data auto-created/seeded in `backend/vyavastha.db` (SQLite).

---

## 1. Architecture

- `backend/app/main.py` — ~90 FastAPI routes, request models, static file mount
- `backend/app/engine.py` — 2,740 lines, ~100 functions: risk engine, simulator, optimizer, chatbot, hotel ranking, evacuation routing, analytics — **all** business logic lives here
- `backend/app/models.py` — 17 SQLAlchemy tables
- `backend/app/regions.py` — real data for 28 states + 8 UTs
- `backend/app/seed.py` — demo data on first boot
- `backend/app/detector.py` — optional YOLOv8 camera-worker thread
- `frontend/` — 15 static HTML dashboards, no build step, Tailwind/Leaflet/qrcodejs from CDN
- `mobile/` — Expo Router React Native app, attendee-only
- No websockets — plain `fetch()` polling every 4–15s per screen
- No auth — session is just an email+role in `sessionStorage`/`AsyncStorage`; role enforcement is an optional `X-User-Role` header, absent = full access
- CORS wide open (`allow_origins=["*"]`)
- No DB migrations tool — new columns are patched in via guarded `ALTER TABLE` at startup

---

## 2. Database — 17 tables

| Table | Purpose |
|---|---|
| `events` | Event catalog — many rows, only one ever `status="live"`. Fields: name, description, event_date/time, category, city, venue_name/address, banner_emoji, is_featured, region, expected_attendance, safe_capacity, status (upcoming/live/paused/completed), owner_email, venue_lat/lng |
| `event_tiers` | Bookable price tier (General/VIP/…): event_id, name, price, capacity, booked_count, uses_seats (true if capacity≥10,000), gate_name (label, resolved to a real Zone at check-in) |
| `event_seats` | Individually numbered seats — only a bounded block (200) within a large seated tier, not one row per physical seat |
| `zones` | The gate/corridor/hub/hotel the risk engine scores: event_id, name, type, domain (venue/transport/hospitality), lat/lng/location_note, capacity/current_count/last_count/prev_delta, peak_count/peak_tick, price_tier (hotels), staff_assigned (gates), contact/amenities, manual_recommended (hotel override), is_accessible, linked_transport_zone_id/linked_hospitality_zone_id (drives resource_pressure), ack_status |
| `crowd_snapshots` | Every crowd-count reading: zone_id, count, source (yolo/checkin/manual/simulation), captured_at — one shared table, four producers |
| `resources` | bus/staff/medical pools: quantity_total, quantity_available — an action needing more than available is dropped, never scored |
| `visitor_profiles` | A booking (direct-gate or catalog): name, email, gate_zone_id OR event_id/tier_id/seat_id, quantity, hotel_zone_id/wants_transport/budget_tier (add-ons), code (8-hex, the QR/check-in code), checked_in, walk_in |
| `hotel_inventory_snapshots` | Latest room count from a hotel partner/PMS feed: hotel_id (unique), occupied_rooms, available_rooms, source, updated_at |
| `user_accounts` | Demo login: email+role (unique together), current_event_id |
| `scenarios` | Authored disruption an operator can trigger: name, description, duration_ticks, effects_json (`{"Gate 2": 273, ...}` = count added per zone per tick) |
| `sim_state` | Singleton — tick, scenario_active/active_scenario_id/trigger_tick, emergency_active/zone_id/message |
| `alerts` | Lifecycle open→acknowledged→resolved: event_id, zone_id, alert_type (crowd/emergency), severity (HIGH/CRITICAL), impact_score (reuses the zone's risk score), status, created_at/resolved_at |
| `notifications` | Targeted messages: event_id, alert_id, audience_role (null=all operators), zone_domain, title/message/priority, is_read |
| `attendees` | One row per signed-in attendee: user_account_id, name |
| `event_attendees` | Attendee↔event many-to-many: attendee_id, event_id, event_name/event_date (snapshotted), registration_status, registration_time |
| `transit_routes` | Train/bus/flight reference data: region, mode, name, destination, description, base_arrival_min |
| `log_entries` | Incident Timeline: tick, category (scenario/zone_critical/acknowledged/escalated/action_executed), message, zone_domain |

---

## 3. Engine & Algorithms

### Risk Engine (recomputed live per zone, every request)

```
score = 0.35×capacity_pressure + 0.25×arrival_surge + 0.15×flow_instability
       + 0.15×resource_pressure + 0.10×time_to_criticality
       // each term clipped to 0–100 before weighting

capacity_pressure   = current_count / capacity × 100
arrival_surge       = (delta / (capacity × 0.05)) × 100
flow_instability    = (|delta − prev_delta| / (capacity × 0.03)) × 100
resource_pressure   = avg occupancy% of the zone's linked transport/hospitality zone(s), or its own if none linked
time_to_criticality = 100 − (minutes_to_full × 100/30)   // minutes_to_full = remaining÷delta, capped at 999
```

Levels: LOW ≤30 · MODERATE ≤55 · HIGH ≤75 · CRITICAL >75. Crossing into HIGH/CRITICAL opens an Alert + Notification; dropping back resolves it.

### Action Optimizer — 5 hardcoded candidate actions, hard-filtered by real resource inventory

```
action_score = 0.28×risk_reduction + 0.16×capacity_balance + 0.12×visitor_experience
             + 0.12×feasibility + 0.08×time_to_impact + 0.04×cost_efficiency + 0.20×urgency
```

Candidates: redirect 2,000→Gate 3, open Gate 3 lane (needs 2 staff), dispatch 4 buses to Corridor B (needs 4 buses), move 6 staff Gate 2/3, recommend Hotel B. Anything needing more resources than `quantity_available` is dropped, never scored. A zone left with no feasible action shows up on Escalations, not silently dropped.

### What-If Simulator
Genuinely recomputes from arbitrary input (redirect count, open-Gate-3 toggle, buses added, staff moved); rejects/caps a redirect that would overload the receiving gate; never mutates DB (pure preview). `compare_plans()` runs 4 named plans side by side and flags the best.

### Deterministic scenarios
Author a `Scenario` (zone→count/tick), trigger it, `advance_tick()` ramps those zones each tick. Seeded scenario reproduces the SRS numbers exactly (Gate 2 ~115%, Corridor B ~108%, Hotel A ~97% after 11 ticks).

### Other engine features
- Preventive alerts — 15-min lookahead before a zone hits HIGH/CRITICAL
- Escalations — zones with zero feasible action
- Event Health Score — `0.85×weighted domain health + 0.15×safety`, domains weighted venue 0.4/transport 0.3/hospitality 0.3
- Causal chains — gate→corridor→hotel narration
- Hotel recommendation ranking — `0.5×availability + 0.3×proximity + 0.2×price tier`, operator manual-override always wins
- Evacuation routing — ranks gates by risk+distance, accessible-only filter, GPS "nearest to me"
- Staff allocation — moves 2 staff from quietest zone to hottest
- Emergency mode, post-event analytics (peak occupancy, most-problematic-zone, incident counts), multi-event attendee registration

### Where the "AI" actually is — and isn't

> "✨ AI Advisor," "Explain with AI," and the "Ask Vyavastha" chatbot are all **template narration** over the same structured risk/state data — a function picks the dominant risk factor and fills a sentence template, or fuzzy-matches (`difflib`) a question to a zone/hotel/transport lookup. `requirements.txt` has no `anthropic`/`openai` package — **no call ever reaches a real LLM.** Every response carries a `GROUNDED IN:` tag naming the exact zone/state it came from — honest framing for what it is (deterministic, not generative). The one genuinely-ML piece is the **optional YOLOv8 person-counter** (`detector.py`) — real `ultralytics`/`cv2` inference, threaded per zone, throttled to one inference per `sample_seconds`, feeding the exact same risk pipeline as manual/check-in/simulated counts. LSTM forecasting / GNN flow-tracking are referenced in planning docs as reused from a prior hackathon — **neither exists in this codebase.**

---

## 4. API Reference (~90 routes, grouped)

- **Auth & live event**: `/api/auth/roles`, `/api/auth/login`, `/api/event`, `/api/regions`
- **Live state, risk & zones**: `/api/state`, `/api/zones`, `/api/risks`, `/api/risks/causal-chain[/{id}]`, `/api/risks/preventive`, `/api/risks/escalations`, `/api/risks/register`, `/api/offpeak`, `/api/advisory`, `POST /api/zones/{id}/ack`, `POST/PATCH/DELETE /api/zones[/{id}]`, `/api/zones/{id}/capacity`, `/crowd-history`
- **Sim clock, scenarios, crowd ingestion**: `/api/scenarios`, `POST/PATCH/DELETE /api/admin/scenarios[/{id}]`, `POST /api/scenario/trigger`, `POST /api/tick`, `POST /api/reset`, `POST /api/zones/{id}/crowd-count`, `GET/POST/DELETE /api/cameras[/{zone_id}]`
- **What-if, actions, AI narration**: `POST /api/simulate/whatif`, `/simulate/compare`, `GET /api/recommendations`, `POST /api/action-plans/approve`, `GET /api/ai/explain/{id}`, `/ai/advisor`, `/ai/attendee-advisory`, `POST /api/chatbot/ask`, `GET /api/health-score`, `/health-breakdown`
- **Registration, check-in, event catalog**: `POST /api/register`, `/checkin`, `GET /api/event-categories`, `/events`, `/events/{id}`, `POST /api/events`, `GET /api/events/{id}/tiers/{id}/seats`, `POST .../book`, `GET /api/my-bookings`, `/my-plan`
- **Event Setup**: `GET /api/event-setup`, `PATCH /api/event-setup/gates/{id}`, `POST/PATCH/DELETE /api/event-setup/hotels[/{id}]`, `.../transport[/{id}]`, `PATCH /api/event-setup/resources/{type}`
- **Hotels/transport discovery**: `GET /api/hotels/recommendations`, `POST /api/hotels/geocode` (Nominatim), `/api/hotels/discover-nearby` (live Overpass/OSM), `/api/transport/discover-nearby`, `GET /api/transport/flights`, `/local`, `/demand-prediction`, `GET/PATCH /api/partner/hotels[/{id}]`, `POST /api/integrations/hotel/webhook`, `GET /api/attendee/hotels`, `/transport`
- **Evacuation, emergency, staff**: `GET /api/evacuation-routes`, `POST /api/attendee/accessibility-request`, `POST /api/emergency/trigger`, `/clear`, `GET /api/emergency/status`, `GET /api/staff/suggestions`
- **Back office, multi-event, analytics**: `POST/PATCH/DELETE /api/admin/event[s/{id}]`, `GET /api/admin/my-events`, `/all-events`, `POST /api/admin/switch-event`, `/region`, `PATCH /api/admin/zones/{id}`, `GET /api/admin/users`, `/raw-tables`, `DELETE /api/admin/users/{id}`, `GET /api/analytics/post-event`, `POST /api/attendee/register-event`, `/current-event`, `GET /api/attendee/my-events`, `/current-event-context`
- **Alerts & notifications**: `GET /api/alerts`, `PATCH /api/alerts/{id}`, `GET /api/notifications`, `POST /api/notifications/{id}/read`

---

## 5. Web Screens (15 live + 2 unused drafts)

| Screen | File | Role | Purpose |
|---|---|---|---|
| Splash | splash.html | universal | Logo animation, auto-redirect to Home or Sign In |
| Sign In | login.html | universal | Email+role, no password; auto-creates account |
| Role landing | index.html | universal | Root `/` — Staff vs Guest sign-in |
| Home/Explore | events.html | attendee | Search, category chips, 5 rails, floating chatbot |
| Event Detail | event-detail.html | attendee | Banner, schedule, live gates/hotels/transport, map, tier picker→booking sheet→QR |
| Event Setup | event-setup.html | operator | Create event + map picker; Gates&Staff / Hotels / Transport tabs |
| **Command Centre** | command-center.html (1,625 lines) | operator | **Flagship, 8 tabs**: Command Centre (health score, risk register, flow map), What-If Simulator (before/after, 4-plan compare, draggable map), Suggested Actions (ranked + "Why?"), Alerts, Hotels (live OSM discovery), Transport (demand projection), Analytics, Incident Timeline — plus Emergency/Health-breakdown/Risk-detail modals |
| Live Event Status | attendee.html | attendee | Venue/crowd/gate tiles, live alerts, multi-event switcher, evacuation, live hotel rooms, register/check-in |
| My Events | my-events.html | attendee | Upcoming/Active/Past, QR modal |
| Profile | profile.html | attendee | Avatar/name/phone (localStorage only) |
| Notifications | notifications.html | attendee | Flat feed, severity dot, mark-read |
| Back Office | admin.html | operator | Event config, region re-anchor, scenario authoring, zone capacity table, user list — not a live-ops surface |
| Database Tables | db-tables.html | debug | Raw dump of 15 tables — inspection only |
| Hotel Discovery | hotel-discovery.html | standalone | Public superset of the Hotels tab, not linked from nav |
| Hotel Partner Portal | hotel-partner.html | external | Simulates a hotel's own PMS view, pushes updates via the same webhook path a real PMS would use |

(`event-detail-gpt-preview.html`, `events-gpt-preview.html` are unused alternate mockups.)

---

## 6. Mobile App (Expo Router, React Native, TypeScript, Expo SDK 57)

Attendee-only, mirrors the web flow against the same backend:

- **Splash** (`index.tsx`) → **Sign In** (`login.tsx`, same `/api/auth/login`)
- **Live Event Status** (`live-status.tsx`, shared with operator role) — polls every 5s, alert banner, accessibility/evacuation card w/ GPS, gate occupancy list, off-peak tips
- Tab bar: **Home** (rails, pull-to-refresh), **Explore** (search+categories), **My Events** (Upcoming/Active/Past, QR codes, "Your Plan" w/ map navigation), **Alerts** (polls 8s), **Profile** (local-only edits; "Preferences"/"Help" are explicit stubs)
- **Event Detail & Booking** (`event/[id].tsx`) — full booking sheet, seat picker, add-ons, QR confirmation
- `src/api.ts` — API base URL is user-editable (AsyncStorage), since a phone can't resolve `localhost` to your laptop (Android emulator needs `10.0.2.2`, real phone needs your LAN IP)

---

## 7. Seed Data

- **Live event**: "Panvel Mega Fest — Navi Mumbai" (50,000 expected / 60,000 safe capacity), Maharashtra region. 10 zones around D Y Patil Stadium/Nerul: Main Hall (50k/41k), VIP Zone (2k/900), Corridor A (6k/3.2k), Corridor B (6k/5.7k), Transport Hub (5k/2.1k), Hotel A (2k/1.84k, tier 4), Hotel B (2k/1.12k, tier 3), Gate 1 (10k/4.5k→Corridor A), Gate 2 (10k/8.8k→Corridor B+Hotel A, the canonical chain), Gate 3 (10k/4.1k→Transport Hub). Resources: 20 buses/40 staff/10 medical.
- **1 scenario**: "Session Release" — 11 ticks, `{Gate 2: +273/tick, Corridor B: +76/tick, Hotel A: +9/tick}`.
- **36 regions** (28 states + 8 UTs) with real venue/transport-hub/airport/hotel data; unverifiable fields left `null`.
- **Real Maharashtra transit data**: flights (IndiGo/Air India Express/Akasa), Harbour/Trans-Harbour suburban lines, long-distance trains (Solapur Vande Bharat, Deccan Express, Konkan Railway Express…), NMMT city buses, MSRTC village buses out of Panvel — real names, illustrative pseudo-random timing.
- **4 upcoming catalog events**: Chennai Music Fest (18 Oct, General ₹999/Premium ₹2,499/VIP ₹4,999), Bengaluru Tech Conclave (5 Nov, Standard ₹1,499/VIP ₹5,999), Mumbai Freshers Party 2026 (14 Sep, General ₹199, not featured), Goa Beach Festival (20 Dec, General ₹799/VIP ₹2,999).
- **1 completed event**: "Mumbai Monsoon Music Night" (9 Aug 2026, NSCI Dome Worli), own "(Past)"-suffixed zones with real peaks, 2 resolved alerts, 11 sample bookings — **one of them hardcoded to `sales@onlydairy.in` (VIP×1, checked-in)** so "My Events → Past" always has a real booking under this account.

---

## 8. Roles & Access

Only 2 roles today: **Attendee** and **Event Command Operator** (the SRS's original Venue/Transport/Hospitality Operator + Administrator were all folded into this one). No passwords — `/api/auth/login` matches or creates on email+role. Role is enforced only via an optional `X-User-Role` header sent by command-center.html/admin.html; any client omitting it keeps full access. **Demo-level gate, not a real security boundary.**

---

## 9. Honest Limitations & Rough Edges

- No real LLM — everything "AI" is template narration (see §3)
- No LSTM/GNN in this repo (referenced in planning docs as reused from elsewhere, not present in code)
- No payments/real ticketing (explicit non-goal) — just a code + QR
- No real auth, no DB migrations tool
- Camera detection not stress-tested at high density/extreme angles
- `_execute_action_effect` (what actually runs on approval) is hardcoded to literal zone names "Gate 2"/"Gate 3"/"Hotel A"/"Hotel B" — why those + "Main Hall" can never be deleted
- Switching back to a paused event loses its custom zones (only a fresh Main Hall is rebuilt)
- Attendee event registration dedups by event **name string**, not id
- Likely field bug: `register_attendee_for_event` stores `event.region` into the `event_date` column
- "Popular" and "Recommended" Home rails use the identical filter — no real distinction yet
- "Most effective action" in analytics is a crude tally, not a real effectiveness measurement
