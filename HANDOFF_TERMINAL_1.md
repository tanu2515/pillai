# HANDOFF — TERMINAL 1 (Government / Command Centre)

Written at the point of an urgent stop-development request, mid-way through a
**read-only verification pass** (no product files were changed during that
pass — only this document was created). All numbers below are real, pulled
live from the running backend at the time of writing.

---

## 1. Current Vyavastha/Kairo state

- Backend: FastAPI, running locally on port 8001 (`python -m app.main` from
  `backend/`), PostgreSQL `kairo` DB via `DATABASE_URL`. Confirmed running
  and responding cleanly at the time of this handoff.
- Frontend: static HTML served by the same FastAPI process
  (`StaticFiles` mounted at `/` from `frontend/`). No build step.
- Live event at time of writing: **Event 13, "Suraj Complex IPL"**, status
  `live`.
- Three roles: Attendee, Event Command Operator (UI label "Event Organiser"),
  Service Provider (added by Terminal 2). Do not rename these.
- Two terminals have been working concurrently on the same repo:
  - **Terminal 1 (this one)**: Government/City Intelligence dashboard +
    Event Organiser Command Centre.
  - **Terminal 2**: Attendee experience (My Events, hotel/rideshare partner
    flows, Service Provider role, login).

## 2. Files Terminal 1 (this session) modified

- `backend/app/engine.py`
- `backend/app/main.py`
- `backend/app/models.py` (only via an earlier migration-guard fix; no schema
  redesign by this terminal)
- `frontend/command-center.html`
- `frontend/government.html` (created earlier this engagement, extended this
  session)

**Not touched by Terminal 1** (seen changed in `git status` but authored by
Terminal 2 or unknown — do not assume these are safe to overwrite):
`frontend/event-detail.html`, `frontend/events.html`, `frontend/login.html`,
`frontend/my-events.html`, `mobile/app/(tabs)/my-events.tsx`,
`mobile/app/event/[id].tsx`, and new files `frontend/ai-architecture.html`,
`frontend/rideshare-partner-feed.html`, `frontend/service-provider.html`.

## 3. Exact Command Centre changes (`frontend/command-center.html`)

- **Risk Register** (`renderRiskRegister(rows)` / `riskRowHtml(r, i, forecast)`,
  ~line 887 onward):
  - Now `async`. Renders immediately with real `trend` (already present on
    each row from the backend) and a `"Forecast: loading…"` placeholder, then
    fetches `GET /api/ai/forecast/{zone_id}` in parallel for every row that
    has a `zone_id` and re-renders with the real forecast.
  - Each row shows: risk type + zone name, severity badge, current
    label/threshold (the existing occupancy-vs-threshold numbers), the
    recommended action (labeled `Action:` — **note:** not literally the
    string "Recommended Action", see Known Limitations §11), a `Trend:` line,
    and a forecast line (`+15m X% · +30m Y% · +60m Z%`, or `Unavailable` with
    the real reason, or `—` for rows with no `zone_id`, e.g. the venue-wide
    EMERGENCY row).
  - Verified in this pass: exactly one `#risk-register-list` element, exactly
    one `renderRiskRegister`/`riskRowHtml` definition each — no duplicate or
    overlapping markup.
- **Camera → Risk → Action pipeline strip** (new section, `#pipeline-strip`,
  `renderPipelineStrip(zones, recs, cameras, executionLog)`):
  - Placed between the Risk Register and the Venue Flow Map sections.
  - One card per zone (sorted by risk score descending), each showing:
    Camera (from `GET /api/cameras`, joined by `zone_id` — says
    `"No live camera feed"` when the zone has no camera entry, `"Error — …"`,
    `"Stopped (source_type)"`, or `"Live · source_type · last read Xs/m/h ago"`),
    Crowd Snapshot (current_count/capacity/%), Risk Engine (level + score,
    from the same `zone_risk()`/`state.zones` data everywhere else uses),
    Suggested Action (first recommendation whose `target_zones` includes this
    zone's name, or "No feasible action for this zone right now"), Operator
    Approval (`"Approved & executed · <clock>"` if a matching
    `action_executed` log entry names this zone, else `"Not yet approved"`),
    and State Change (the real log message text verbatim, or "No state
    change recorded for this zone yet").
  - Zone-name-agnostic by construction: every join is by `zone_id` or
    `zone.name` looked up at render time, nothing hardcoded.
- **AI Forecast Advisory** (`renderAiForecastAdvisory`, pre-existing from
  earlier this engagement, **not modified in this session** beyond what was
  already in place): Temporal (LSTM) section always shown when available;
  Spatial (GNN) section only shown when `GET /api/ai/network-pressure`
  reports `available: true` — for the current Event 13 zone layout it is
  `false` ("this event's zones don't match the layout the network model was
  trained on"), so the Spatial section is correctly and honestly omitted,
  not fabricated. This is pre-existing, expected behavior, not a regression.

## 4. Exact Government dashboard changes (`frontend/government.html`)

- `renderMap(events, risk, emergencyServices)` (single definition, confirmed
  in this pass — no duplicate):
  - Event venue markers: unchanged, blue circle markers from
    `GET /api/government/events`' `venue_lat/venue_lng`, only when set.
  - **NEW — transport layer**: any zone in `GET /api/government/risk`'s
    `zones` array with `domain === "transport"` and real `lat/lng` is now
    drawn as a 🚌 emoji marker (`TRANSPORT_ICON`, a module-level `const`
    built via `emojiDivIcon("🚌")`), regardless of risk level (previously the
    map only ever drew HIGH/CRITICAL risk dots).
  - **NEW — emergency/hospital/medical layer**: sourced from a new endpoint,
    `GET /api/government/emergency-services` →
    `engine.government_emergency_services(db)`, which aggregates
    `models.ServicePOI` rows with `group == "emergency"` across all
    `status == "live"` events (reusing the exact same `_poi_out()` /
    `_ensure_service_pois_seeded()` / `_poi_venue_point()` helpers the
    attendee-facing emergency directory already used — no new data model,
    no new formula). Rendered with a per-category emoji
    (`EMERGENCY_ICON = {hospital: 🏥, ambulance: 🚑, police: 👮,
    fire_station: 🚒, first_aid: ⛑️, emergency_help_desk: ℹ️}`, unknown
    category falls back to 🚨 rather than being silently dropped).
  - Coordinates are never invented: rows with `lat`/`lng` null are simply
    not plotted; the map-note text under the map reports how many
    events/services were omitted for missing coordinates.
  - Popups carry each POI's real `data_label` ("Demo/reference data" vs
    "Operator-entered") so the map itself doesn't overstate data quality.

## 5. Camera → YOLO → CrowdSnapshot → Risk Engine → Recommendation → Approval → state-mutation chain

(All pre-existing logic — described here for handoff clarity, not changed
this session except where noted.)

1. **Camera**: `backend/app/detector.py`'s `CrowdDetector` runs one YOLOv8
   worker thread per zone (`Feed`), fed by webcam / RTSP / uploaded video /
   manual IP-camera URL (`source_type` field). `GET /api/cameras` exposes
   live status (`running`, `last_count`, `last_updated`, `error`) per zone.
2. **CrowdSnapshot**: `engine.ingest_crowd_count(db, zone_id, count, source)`
   writes a `models.CrowdSnapshot` row (audit trail, any source: yolo |
   checkin | manual | simulation) and updates `Zone.current_count` /
   `Zone.last_count` / `Zone.prev_delta`.
3. **Risk Engine**: `engine.zone_risk(zone, db)` → `risk_factors(...)`, a
   5-factor weighted deterministic score (capacity_pressure, arrival_surge,
   flow_instability, resource_pressure, time_to_criticality), levels
   LOW/MODERATE/HIGH/CRITICAL. This is the **one and only** authoritative
   risk computation in the app.
4. **Recommendation**: `engine.generate_recommendations(db)` resolves
   semantic zone roles (`_resolve_action_context` /
   `_candidate_actions_for_context`) — no hardcoded zone names — and scores
   candidate actions.
5. **Approval**: `engine.approve_actions(db, action_ids)` →
   `_execute_action_effect(db, action, ctx)` mutates real zone state
   (`current_count`/`capacity` changes) and logs a real
   `action_executed` LogEntry whose message contains the actual before→after
   effect text (e.g. "Moved 100 visitors from Front Gate 1 to Back Gate 2.").
6. This exact chain is what the new Camera → Risk → Action strip (§3) reads
   at each of its stages — it introduces no shortcut and no second
   computation path.

## 6. Risk Register Trend + LSTM Forecast implementation

- **Trend** (`engine._zone_occupancy_trend(db, zone)`, in
  `backend/app/engine.py`, feeding a new `"trend"` key on every
  `risk_register()` row): reuses the existing `zone.current_count -
  zone.last_count` delta already used by `zone_risk()`'s arrival_surge
  factor — **no new formula**. Returns `RISING` / `FALLING` / `STABLE` based
  on that delta as a % of capacity (>±1% threshold to avoid noise), or
  `"N/A"` if the zone has fewer than 2 real `CrowdSnapshot` rows yet or has
  no capacity set.
- **Forecast**: deliberately **not** computed in `engine.py` — kept out so
  that module gains no ML dependency. The frontend calls the existing
  `GET /api/ai/forecast/{zone_id}` (→ `ml_advisory.zone_forecast`, the
  already-trained LSTM) per Risk Register row, independently, the same
  pattern the AI Forecast Advisory panel already used. Response is
  `{available, occupancy_pct_15m, occupancy_pct_30m, occupancy_pct_60m, ...}`
  or `{available: false, reason: "..."}` — reason is always the model's real
  stated reason (e.g. insufficient history), never fabricated.
- Verified live against Event 13: e.g. Back Gate 2 (100/100, CRITICAL on the
  occupancy-register scale, MODERATE/score 50 on the composite Risk Engine
  scale — this divergence is intentional and pre-existing, documented via
  the Risk Register's own ⓘ tooltip) returned Trend=STABLE, Forecast
  +15m 21% / +30m 60% / +60m 44%.

## 7. Government transport/emergency map layers

Covered in detail in §4. Backend addition: one new engine function
`government_emergency_services(db)` and one new route
`GET /api/government/emergency-services`, both purely additive/read-only
aggregations of existing tables (`Zone`, `ServicePOI`) — no schema change, no
new risk formula.

## 8. AI architecture truth

- **LSTM** = temporal forecast only ("what will happen to this zone over the
  next 15/30/60 minutes"). Endpoint: `GET /api/ai/forecast/{zone_id}` →
  `ml_advisory.zone_forecast`. Real trained model
  (`backend/ml/lstm_model.py` + `weights/`), 39 input features → 64 hidden →
  3 horizon heads.
- **GNN** = spatial network-pressure view only ("given current telemetry,
  what does the graph expect next, zone-to-zone"). Endpoint:
  `GET /api/ai/network-pressure` → `ml_advisory.event_network_pressure`.
  Hand-rolled inductive 2-layer message-passing model, lag-aware, no
  zone-identity embeddings. Only works for events whose zones match the
  exact 9-zone trained layout (Main Hall/Gate 1-3/Corridor A-B/Transport
  Hub/Hotel A-B) — Event 13 does **not** match this layout, so
  `available: false` is the correct, honest response for it right now.
- **These are two independently trained models. There is no fusion model.**
  Neither one ever writes to or overrides `zone_risk()`/`risk_factors()` —
  the deterministic Risk Engine is always authoritative. This is stated
  explicitly in the UI (`#mlArchModal`, "How Vyavastha AI Works") and in
  code comments in `ml_advisory.py`. Do not let any future change blur this
  line (e.g. do not let a GNN result overwrite an LSTM forecast's number, do
  not let either write to `Zone`/`CrowdSnapshot`).

## 9. Current Event 13 demo proof and numbers (live at time of writing)

- Clock: 15:37. Overall health score: **77.5** (venue 82.8, transport 64.5,
  hospitality 85.0), safety 75, open_alerts 1 (1 critical).
- Zones (`id | name | domain | level | score | count/capacity`):
  - 102 Back Gate 2 | venue | MODERATE | 50.0 | 100/100
  - 108 Metro Station Point | transport | MODERATE | 40.0 | 176/220
  - 107 Local Bus Hub | transport | MODERATE | 31.0 | 186/300
  - 104 Landmark Hotel | hospitality | LOW | 30.0 | 120/200
  - 100 Front Gate 2 | venue | LOW | 20.0 | 100/250
  - 99 Front Gate 1 | venue | LOW | 16.0 | 48/150
  - 103 Hotel Three Star | hospitality | LOW | 0 | 0/20
  - 105 Main Hall | venue | LOW | 0 | 0/600
  - 101 Back Gate 1 | venue | LOW | 0 | 0/100
- Risk Register (occupancy-threshold view) currently surfaces 2 rows: Back
  Gate 2 (CROWD CONGESTION, CRITICAL, 100% density) and Landmark Hotel
  (HOTEL CAPACITY, MEDIUM, 60% occupancy).
- `GET /api/cameras` currently returns `[]` — no camera feed is configured
  for this event in this session's data, so the pipeline strip's Camera
  stage honestly reads "No live camera feed" for every zone. This is real,
  not a bug.
- **Correction (added during final reconciliation audit)**: zones 107
  ("Local Bus Hub") and 108 ("Metro Station Point") were **not** pre-existing
  simulation data as originally assumed when this document was first
  written — per `HANDOFF_TERMINAL_2.md` §G, Terminal 2 added both via the
  pre-existing `engine.create_transport_zone()` operator function and
  backfilled 12 synthetic `CrowdSnapshot` rows each (source="simulation")
  so the LSTM would have enough history. This doesn't change anything about
  how Terminal 1's Risk Register/pipeline-strip/Government-map code reads
  them (all real DB rows via the normal APIs either way), but the
  provenance is corrected here for accuracy.

## 10. Tests and Playwright results

- Backend: `python -c "import app.main"` clean; fresh `uvicorn` boot clean,
  no startup errors.
- All new/changed endpoints curl-verified directly against live data:
  `/api/risks/register` (trend field present), `/api/ai/forecast/{id}`
  (matches frontend's expected key names), `/api/cameras`,
  `/api/execution-status`, `/api/government/risk` (transport zone with
  lat/lng present), `/api/government/emergency-services` (real seeded
  emergency POIs with lat/lng and `data_label`), `/api/attendee/hotels`
  (`live` field now reflects reality — see §11).
- Playwright regression (headless Chromium, `command-center.html` +
  `government.html`): **14/14 checks passed**, zero console errors on either
  page — covering KPI row, Risk Register trend/forecast, pipeline strip
  (including the honest no-camera case), action-approval button presence,
  Risk Detail modal open, Hotels/Transport tab rendering, Government
  dashboard load, map markers (8 divIcons: 2× 🚌 transport + 6 emergency
  categories, confirmed by DOM inspection of `.leaflet-marker-icon`
  `innerHTML`), and Event Impact modal open.
- Follow-up read-only verification pass (this pass, screenshots taken):
  confirmed exactly one `renderMap` and one `#risk-register-list` /
  `renderRiskRegister` / `riskRowHtml` each (no duplication), zero console
  errors on both pages, Risk Register screenshot shows clean non-overlapping
  Current-Risk/Trend/Forecast/Action layout, pipeline-strip screenshot shows
  5+ real per-zone cards (strip is horizontally scrollable —
  `overflow-x-auto` — so a fixed-width screenshot only captures the visible
  slice, not a bug), city map screenshot at zoom 16-17 shows the 🚌/🏥/🚑/👮/
  🚒/⛑️/ℹ️ markers all present in the DOM at their real coordinates near
  Panvel/Navi Mumbai (small at default zoom; distinguishable when zoomed in,
  see §11).

## 11. Known limitations

- Risk Register's action column is labeled `Action:` in the rendered UI, not
  literally `Recommended Action:` — same underlying `recommended_action`
  data, just shorter label text. Flagged, not changed, per the "do not
  modify further" instruction.
- Emoji divIcon markers on the Government map are `font-size:16px` with no
  outline/halo — visible but small against a busy OpenStreetMap tile
  background at low zoom; easiest to see at zoom ≥15. Cosmetic only, not a
  data-correctness issue. A future pass could add a white circular backing
  behind each emoji for contrast if the demo needs it more legible at the
  default zoom (10).
- `#pipeline-strip` uses `overflow-x: auto`; a full-page screenshot or a
  narrow viewport will visually clip later cards. It scrolls correctly in a
  real browser — just be aware when reviewing static screenshots.
- The AI Forecast Advisory's Spatial (GNN) section does not render at all
  for Event 13 because its zone layout doesn't match the trained 9-zone
  graph — this is pre-existing (not introduced this session) and is the
  correct, honest behavior, but it means a live demo of Event 13 alone will
  never show the GNN panel. If the demo needs to show GNN output, it needs
  an event whose zones are literally named Main Hall/Gate 1-3/Corridor
  A-B/Transport Hub/Hotel A-B.
- No camera is currently wired to Event 13, so the pipeline strip's
  "Live · webcam · last read Xs ago" success-path rendering has not been
  visually exercised this session (code path only, not visually confirmed).
- `government_emergency_services()` scopes to `status == "live"` events
  only (mirrors `government_risk()`'s existing scope) — upcoming/completed
  events' emergency services are not shown on the map, by design consistency
  with the rest of the Government risk view, not an oversight.
- **Auth is demo-only** (pre-existing, not introduced by Terminal 1):
  `frontend/login.html` itself honestly says "Demo login only — matches
  your email + role against our records, no password yet," and the session
  is just an email/role pair in `sessionStorage`. Verified present at
  handoff time (re-confirmed during the final reconciliation audit).

## 12. ⚠️ Reconciliation warning — Terminal 2 has modified other files concurrently

**Updated during the final reconciliation audit — no longer presumed, now
cross-confirmed against `HANDOFF_TERMINAL_2.md` (which Terminal 2 wrote
independently, also read this pass):**

- `git status --short` shows changes in files this terminal did **not**
  touch: `frontend/event-detail.html`, `frontend/events.html`,
  `frontend/login.html`, `frontend/my-events.html`,
  `mobile/app/(tabs)/my-events.tsx`, `mobile/app/event/[id].tsx`, plus new
  untracked files `frontend/ai-architecture.html`,
  `frontend/rideshare-partner-feed.html`, `frontend/service-provider.html`.
  **`HANDOFF_TERMINAL_2.md` §B confirms these are all Terminal 2's work**
  (attendee Food/Essentials/Emergency/Venue-Map features, mobile parity, the
  `ai-architecture.html` ML explainer page, Service Provider portal polish).
  `frontend/login.html` was reportedly already at spec before Terminal 2's
  session started (not edited by either terminal this pass).
- **`frontend/command-center.html` and `frontend/government.html` are
  confirmed, by Terminal 2's own handoff, to have been "never opened for
  writing" by Terminal 2** — both files' `git diff` are Terminal 1's changes
  only. No further reconciliation is needed for these two files specifically
  (they are not shared/co-edited).
- `backend/app/engine.py` and `backend/app/main.py` **are** genuinely
  shared/co-edited — both terminals independently confirm this in their
  respective handoffs. Terminal 1 added `government_emergency_services()`
  and `GET /api/government/emergency-services`, building on top of Terminal
  2's `ServicePOI` model and `_ensure_service_pois_seeded`/
  `_poi_venue_point`/`_poi_out` helpers (not duplicating them). Verified
  this pass: `python -m py_compile app/models.py app/engine.py app/main.py`
  is clean and `from app.main import app` registers 133 routes with no
  startup errors — both terminals' additions coexist correctly right now.
  Still, **re-read these two files fresh before any further edit** — do not
  assume the content described in this document is still current if more
  time has passed or another terminal has run since.
- `backend/app/models.py` — Terminal 1 only added a migration guard for
  `user_accounts.managed_zone_id` (columns, not tables); Terminal 2 added
  the wholly-new `ServicePOI` table. Additive on both sides, confirmed no
  conflict.
- Do not run any DB reset/reseed/migrate — no destructive schema operation
  has been requested or performed by either terminal this engagement.

## 13. Explicit next steps after reset

1. Reconcile: pull/read the current state of every file listed in §2 and
   §12 fresh (do not trust this document's content byte-for-byte if time has
   passed) before making any further edit — assume Terminal 2 may have kept
   working.
2. Re-run the full Playwright regression pass on both `command-center.html`
   and `government.html` against the reconciled code, confirm still 0
   console errors and the same feature set holds.
3. Do a final cross-terminal smoke test: log in as each of the three roles
   (Attendee, Event Organiser, Service Provider) and click through every tab
   once, since Terminal 2's changes to `login.html`/`my-events.html`/mobile
   files are unverified by this terminal.
4. Only after 1-3 pass cleanly: prepare the PPT/demo video. Suggested demo
   beats, in order: Command Centre overview (KPI row + Event Health Score) →
   Risk Register with Trend/Forecast → Camera→Risk→Action strip → approve an
   action live → Government dashboard overview → City Risk table → City Map
   (zoom to show transport/emergency layers) → City Trends charts → (if an
   event with the trained zone layout is available) AI Forecast Advisory
   showing both LSTM and GNN panels together with the "How Vyavastha AI
   Works" modal to explain the no-fusion architecture.
5. Do not add any new risk formula, do not claim real Uber/Ola/government
   CCTV integration, do not claim the LSTM/GNN are fused, at any point in
   the deck or the live demo narration.

---

## 14. DEMO READINESS (final reconciliation audit)

**DEMO READINESS: GREEN** for Terminal 1's scope (Command Centre + Government
dashboard). No blockers found. All 6 Government API routes respond correctly
with real data; Command Centre and Government both load with zero console
errors; backend compiles/imports cleanly with both terminals' changes
present (133 routes); mobile TypeScript compiles cleanly (`npx tsc --noEmit`,
exit 0, independently re-verified this pass); Event 13's live state is
stable and matches what was recorded earlier in this document (no
unexpected drift since the last check).

**YELLOW items (not blockers, need awareness during the demo):**
- GNN/Spatial panel will not appear for Event 13 (zone-layout mismatch,
  pre-existing, honestly surfaced — see §5/§11). If the demo script calls
  for showing the GNN panel, it needs a different event or a narration
  adjustment ("here's what it looks like when the layout matches").
- The Camera→Risk→Action strip's "live camera" success path has not been
  visually exercised (no camera is wired to Event 13) — only the honest
  "No live camera feed" path has been seen live.
- Full cross-terminal smoke test (logging in as all three roles and
  clicking every tab) has **not** been performed by Terminal 1 — Terminal
  2's own files are Terminal 2's responsibility to verify (their handoff
  states browser/visual testing was not performed on their side either, only
  HTTP/syntax-level checks).
- `frontend/command-center.html` and `frontend/government.html` are
  confirmed NOT shared with Terminal 2 (see §12), so no reconciliation risk
  there — but `engine.py`/`main.py` remain genuinely shared files and must
  be diffed before any further edit by anyone.

**No RED blockers identified in this audit.**

## 15. NEXT CLAUDE SESSION — START HERE

1. Read `HANDOFF_TERMINAL_1.md` (this file).
2. Inspect `HANDOFF_TERMINAL_2.md`.
3. Run `git status`.
4. Reconcile Terminal 1 and Terminal 2 changes.
5. Inspect `command-center.html` carefully because it had concurrent edits.
6. Run final regression.
7. Verify Event 13 demo state.
8. Do NOT add new features unless a blocker is discovered.
9. Prepare PPT.
10. Record demo video.
11. Final commit.
12. Push only after verification.

**DO NOT RESET, RESEED, MIGRATE, OR DROP THE DATABASE.**

---

## Appendix: `git status --short` — FINAL, run at the end of the reconciliation audit

```
 M backend/app/engine.py
 M backend/app/main.py
 M backend/app/models.py
 M frontend/command-center.html
 M frontend/event-detail.html
 M frontend/events.html
 M frontend/government.html
 M frontend/login.html
 M frontend/my-events.html
 M mobile/app/(tabs)/my-events.tsx
 M mobile/app/event/[id].tsx
?? HANDOFF_TERMINAL_1.md
?? HANDOFF_TERMINAL_2.md
?? frontend/ai-architecture.html
?? frontend/rideshare-partner-feed.html
?? frontend/service-provider.html
```

Note: `HANDOFF_TERMINAL_2.md` now exists (Terminal 2 wrote its own handoff
concurrently, and this audit read it — see §9/§12 corrections above). Nothing
else changed since the earlier snapshot. Still uncommitted, unpushed, and no
destructive DB operation has been performed by either terminal.
