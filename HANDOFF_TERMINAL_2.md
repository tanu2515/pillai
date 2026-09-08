# HANDOFF — Terminal 2 (Attendee Features / Restaurants / Essentials / Emergency / Venue Map / ML Docs)

Written at user request immediately before a context reset. This is a read-only status
handoff — no code was modified, no data was reset, nothing was committed or pushed while
writing this file.

---

## A. PROJECT STATE

- Project: **Vyavastha / Kairo** — a mega-event orchestration platform (attendee app,
  operator Command Centre, Service Provider/hotel portal, Government dashboard).
- Database: **live PostgreSQL**, `postgresql://postgres:***@localhost:5432/kairo`
  (configured in `backend/.env`). This is the real, shared demo database — not SQLite,
  not a throwaway. No resets, migrations, reseeds, or table drops were performed by
  Terminal 2 at any point.
- Demo event: **Event 13 = "Suraj Complex IPL"**, `status = "live"`,
  `venue_lat = 19.023073`, `venue_lng = 73.090847`. This is a real row already in the
  live DB (not seeded by Terminal 2) and was used as the test target throughout.
- Architecture (unchanged by this phase): FastAPI backend (`backend/app/`) with a
  deterministic Risk Engine (`engine.py: risk_factors()`/`zone_risk()`) as the sole
  authority on risk levels/alerts; a separate advisory layer (`ml_advisory.py`,
  `backend/ml/`) providing an LSTM temporal forecast and a GNN spatial-pressure signal,
  both explicitly non-authoritative; a self-contained static HTML/Tailwind/vanilla-JS
  frontend (`frontend/*.html`, no build step, no shared JS module — each page duplicates
  its own `api()`/`getEmail()`/label helpers by existing convention); an Expo Router
  mobile app (`mobile/app/`, `mobile/src/`) with no backend/DB access of its own — it
  only calls the same FastAPI JSON API. No Alembic; schema changes are either brand-new
  tables (auto-created by `Base.metadata.create_all()`) or hand-guarded `ALTER TABLE`
  blocks at the top of `main.py`.

---

## B. FILES MODIFIED BY TERMINAL 2

- `backend/app/models.py` — added `ServicePOI` model only (additive).
- `backend/app/engine.py` — added restaurant/essentials/emergency/venue-POI logic,
  transport friendly-label/source-honesty fields, and the `ending_soon` home-rail
  section. **Also now contains Terminal 1's `government_emergency_services()`**, added
  on top of Terminal 2's `ServicePOI`/`_ensure_service_pois_seeded`/`_poi_venue_point`/
  `_poi_out` helpers — see Section J, this file is now shared/co-edited.
  Verified present via `grep`/compile at handoff time.
- `backend/app/main.py` — added 4 new attendee routes. **Also now contains Terminal 1's
  `/api/government/emergency-services` route** — same sharing note as above.
- `frontend/event-detail.html` — modified (Food/Essentials/Emergency/Venue-Map sections,
  transport label fix, extended Leaflet map).
- `frontend/my-events.html` — modified (same four new cards, transport label fix, added
  Leaflet CDN include which wasn't there before).
- `frontend/events.html` — modified (home rail: "More featured events" → "Ending soon",
  now backed by a genuinely different query).
- `frontend/service-provider.html` — modified (polish only: added venue/status to nearby
  events, added peak-rooms to the report box). Core portal (claim/nearby/opt-in/
  prediction/report) pre-existed Terminal 2's session, untracked (`??`) in git the whole
  time.
- `frontend/ai-architecture.html` — **new file**, honest LSTM/GNN/fusion writeup.
- `mobile/app/event/[id].tsx` — modified (Food/Essentials/Emergency/Venue-Map-as-list
  sections, transport friendly-label/source/forecast fix).
- `mobile/app/(tabs)/my-events.tsx` — modified (same four cards in the live-info footer,
  transport crowd block added — it previously showed no crowd/LSTM data at all here).

Files Terminal 2 did **not** touch, despite appearing in `git status`:
- `frontend/command-center.html` — Terminal 1's, never opened for writing by Terminal 2.
- `frontend/government.html` — Terminal 1's (added a UI for `government_emergency_services`).
- `frontend/login.html` — was already modified before Terminal 2's session started
  (already met spec: Attendee/Event Organiser/Service Provider, Attendee default) — no
  edits made to it in this session.
- `frontend/rideshare-partner-feed.html` — pre-existing untracked file, not touched.

---

## C. BACKEND

- **`ServicePOI`** (`backend/app/models.py`): a brand-new table
  (`__tablename__ = "service_pois"`) — `id, event_id (FK events, nullable), group
  (food|essential|emergency), category, name, description, lat, lng, cuisine,
  price_level, is_vegetarian, is_vegan, capacity, current_count, opens_at, closes_at,
  is_24x7, contact, amenities, source (demo_seed|operator_entered), created_at`.
  **This is purely additive** — a wholly new table, auto-created by
  `Base.metadata.create_all()`. No `ALTER TABLE` was needed, no existing table or column
  was touched, no existing row was modified or deleted. Deliberately kept separate from
  `Zone` (which feeds the risk engine, GNN zone-role map, and command-center crowd map)
  so POIs never pollute risk scoring.
- **Restaurants API** — `GET /api/attendee/restaurants?event_id=&cuisine=&max_price=&open_now=&sort=`
  → `engine.list_restaurants()`. Filters: cuisine, price, open-now, nearest/less-crowded
  sort. Returns a reasoned `recommended` pick and a `note` explicitly labeling demo data.
- **Essentials API** — `GET /api/attendee/services?event_id=&category=&open_now=&sort=`
  → `engine.list_essential_services()`. 10 categories (general_store, pharmacy, atm,
  fuel, ev_charging, water, toilets, parking, help_desk, lost_found).
- **Emergency API** — `GET /api/attendee/emergency?event_id=` → `engine.attendee_emergency_directory()`.
  Combines: the **pre-existing, unmodified** `emergency_status()` and
  `evacuation_routes()` (extended, not replaced) with a new static services directory
  (hospital/ambulance/police/fire_station/first_aid/emergency_help_desk) plus a
  `purpose` dict explaining what each service is for. No emergency phone numbers are
  fabricated — `contact` is only ever populated from real stored data (currently null
  for all seeded demo rows).
- **Venue-POI/map API** — `GET /api/attendee/venue-pois?event_id=` → `engine.venue_pois()`.
  Returns real `Zone` rows (gates/arena/hotels/transport, with live risk level) plus
  `ServicePOI` rows, plus a `legend` dict — feeds the extended Leaflet map on web and the
  list-based map view on mobile.
- **Transport crowd + LSTM changes** — `transport_crowd_advisory()` and
  `transport_demand_prediction()` (both pre-existing) gained **additive fields only**:
  `level`, `level_label` ("Quiet"/"Moderate"/"Busy"/"Very busy"), `source`/
  `source_label` (from the zone's most recent real `CrowdSnapshot.source` — CCTV/YOLO,
  manual, check-in, or simulated; never assumed), and `forecast_15m_pct` from the real
  LSTM (`ml_advisory.zone_forecast`) where enough history exists. No existing field was
  removed or renamed.
- **Service-provider functionality** — not changed by Terminal 2 beyond the two small
  UI additions in D below; the portal's backend endpoints
  (`/api/partner/hotels*`, `/api/partner/hotels/{id}/nearby-events`, `/opt-in`,
  `/prediction`, `/report/{event_id}`) were already implemented before this session and
  were left untouched.
- **Event scoping** — every new function takes `event_id`, falls back to
  `get_live_event(db)` when omitted, and was verified (see H) to never leak another
  event's POIs. Same convention as the pre-existing `attendee_hotels`/`attendee_transport`.
- **DB/schema changes**: only the new `service_pois` table. **Confirmed additive** —
  verified at handoff time via `python -m py_compile` (clean) and a `grep` confirming
  `class ServicePOI` still present in `models.py` and the 4 new routes still present in
  `main.py`. No other table's columns were altered.

---

## D. FRONTEND

- **`event-detail.html`** — Fixed the raw `"NN% loaded"` transport line to show a
  friendly level + %, an LSTM forecast when available, and the real source label. Added
  four new sections after the existing Hotels/Announcements area: **🍽 Food & Restaurants**,
  **🏪 Essentials**, **🚑 Emergency & Safety**, and **🗺 Venue Map** (an extended version of
  the existing small Leaflet preview map — kept the original map untouched, added a new,
  larger POI map with a togglable legend, category icons/colors, and click-to-detail
  popups for gates/arena/hotels/transport/food/essentials/emergency). Booking flow
  (Explore → Tier → Seat → Book → QR → My Event) was not touched.
- **`my-events.html`** — Same transport label fix (previously hardcoded
  "📡 Live crowd % (CCTV/LSTM)" regardless of actual source — now shows the real
  per-zone source). Added the Leaflet CDN `<link>`/`<script>` tags (weren't present
  before). Added the same four new cards (Food nearby / Essential services /
  Emergency & Safety / Venue map) inside the existing `#live-info` section, so they
  stay scoped to the attendee's own live booked event exactly like the pre-existing
  hotel/transport cards there.
- **`events.html`** — Home page's "More featured events" rail renamed to **"Ending soon"**
  and now calls `section=ending_soon` (a genuinely different backend query — soonest
  upcoming/live event date — instead of the same `is_featured` list reversed).
- **`service-provider.html`** — Minor polish only: nearby-event cards now show
  venue/date-time/status in addition to distance/category/attendance; the event
  service report now also shows "Peak rooms" (previously only occupancy %/current
  availability were shown, though the backend already returned `peak_rooms`). Portal
  structure (claim → nearby events → opt-in → predicted situation → report) unchanged.
- **`ai-architecture.html`** (new) — a dedicated, honest LSTM/GNN/fusion explainer page,
  linked from the nav bar of `event-detail.html` and `my-events.html` as "ℹ️ How AI works".
  Content is drawn directly from verified code inspection (see Section F).
- **User-facing summary**: an attendee can now, from either Event Detail or My Event, see
  nearby food (with cuisine/price/open-now/veg flags and a reasoned pick), essential
  services (with distance), an emergency directory (with purpose-per-category, safest
  exit, and live emergency-status banner), and a POI map — all clearly labeled as
  demo/reference data where that's what it is.

---

## E. MOBILE

- **`mobile/app/event/[id].tsx`** — added `Poi`/`RestaurantsResp`/`ServicesResp`/
  `EmergencyResp`/`VenuePoiZone`/`VenuePoisResp` types (locally declared, matching this
  file's existing per-screen type-duplication convention). Fixed the Transport section
  to show friendly level + source + forecast instead of raw `"NN% loaded"`. Added four
  new `<Section>` blocks: Food & Restaurants, Essentials, Emergency & Safety, and Venue
  Map. Venue Map is implemented as **a categorized list with "🧭 Navigate" buttons**
  (reusing the existing `openInMaps()` helper) — **not an embedded interactive map**,
  because the mobile app has no map SDK installed (`react-native-maps` or equivalent is
  not a dependency) and adding one wasn't something I could build+verify in this
  environment.
- **`mobile/app/(tabs)/my-events.tsx`** — added the same four new footer cards inside the
  existing live-info `ListFooterComponent` (scoped by `anyLiveActive`/`liveEventId`,
  same pattern as the pre-existing hotel/transport cards). Also added a "📡 Live crowd %"
  block showing per-zone level/forecast/source — this screen previously showed **no**
  transport crowd/LSTM data at all (only citywide bus/train/flight schedules).
- **TypeScript status**: `npx tsc --noEmit` run from `mobile/` — **exit code 0, zero
  errors** at the time these changes were made.
- **Limitations**: no native map view (list + external-Maps-deep-link only); all new
  mobile types are locally duplicated per screen rather than centralized (pre-existing
  codebase convention, not a new issue introduced here).

---

## F. ML TRUTH (verified against actual code + live inference calls, not assumed)

- **LSTM = a real, trained PyTorch model.** Single shared-weight `nn.LSTM`
  (hidden_dim=64, 1 layer) across all zones/domains, with **3 separate linear output
  heads** for a direct (non-recursive) multi-horizon prediction.
  - Input window: **the last 12 ticks** of a zone's own `CrowdSnapshot` history
    (`MIN_HISTORY_TICKS = 12`).
  - Prediction horizons: **+15m, +30m, +60m** occupancy %, output simultaneously.
  - **39 real input features** per tick: occupancy%/entry-rate%/exit-rate% (3),
    event phase one-hot (3), event type one-hot (5), zone domain one-hot (3), sport
    one-hot (4), competition one-hot (9), tournament-gender one-hot (2), stage one-hot
    (4), has-ticket-data flag (1), sell-through% (1), booking velocity (1), demand
    intensity (1), log-scaled capacity (1), ticks-since-phase-start (1).
  - Trained on a **synthetic generative dataset**
    (`backend/data/synthetic_crowd_sequences.csv`), not real historical venue data.
  - Confirmed live-verified against Event 13's real zones (e.g. "Front Gate 2", "Local
    Bus Hub") — `GET /api/ai/forecast/{zone_id}` returns `available: true` with real
    numbers once 12+ CrowdSnapshot rows exist for that zone.
- **GNN = a real, hand-rolled message-passing model** (plain PyTorch tensor ops,
  explicitly NOT `torch_geometric`).
  - Nodes: a **fixed, hardcoded set of 9 illustrative zone roles** (Wankhede-Stadium-
    style names: venue_bowl, gate_vinoo_mankad, gate_polly_umrigar,
    gate_north_illustrative, concourse_north, concourse_sea_face, churchgate_station,
    hotel_intercontinental_marine_drive, hotel_bentley_marine_drive) — **not** read from
    the live event's own `Zone`/`ZoneEdge` rows.
  - Edges: 11 hardcoded walkway connections with per-edge propagation lag (up to 5
    ticks for the longest ones).
  - Node features: occupancy%/entry-rate%/exit-rate% + domain one-hot (6 dims).
  - **Confirmed at handoff time: does NOT currently work for Event 13.**
    `GET /api/ai/network-pressure?event_id=13` returns:
    `{"available": false, "reason": "this event's zones don't match the layout the
    network model was trained on (Main Hall/Gate 1-3/Corridor A-B/Transport Hub/Hotel
    A-B)"}` — because Event 13's zones are named "Front Gate 1/2", "Back Gate 1/2",
    "Landmark Hotel", etc., not the trained role names. This is the deterministic,
    honest behavior of the existing code — a real, verified limitation, not a bug
    introduced by Terminal 2.
- **NO trained fusion model exists.** The live inference path
  (`ml_advisory.event_network_pressure`) always calls the GNN with
  `lstm_forecast_15m_by_zone=None` — the only place LSTM output is ever fed into the
  GNN's input is an offline smoke-test/experiment script, never the live API. That
  offline experiment's own documented conclusion is that composing the two signals had
  **no measurable benefit**, which is why they're kept independent.
- **LSTM and GNN are two independent advisory signals.** Neither one ever writes to
  `Zone.current_count`, a risk score, or an alert. Both are surfaced as separate,
  clearly-labeled API fields.
- **The deterministic Risk Engine (`engine.risk_factors()`/`zone_risk()`) remains
  authoritative** — unchanged by anything in this phase, and every recommended
  operator action still requires explicit approval via `/api/action-plans/approve`
  before any state changes (verified live — see Section G for the one real state
  change this caused during testing).
- **Live data updates the inference input; model weights are not retrained online.**
  Training happens offline ahead of time on the synthetic dataset, producing saved
  weights (`backend/ml/weights/*.pt`) loaded once at inference time; each new
  CrowdSnapshot only slides the LSTM's 12-tick window / updates the GNN's current-tick
  telemetry for the *next* inference call.

All of the above is written up on `frontend/ai-architecture.html` in demo-friendly
language.

---

## G. DEMO DATA

- **Event 13 had zero transport zones** before this session — which would have left the
  public-transport / crowd-% / LSTM-forecast / Uber part of the attendee demo journey
  completely empty. Two transport zones were added via the **existing, already-supported**
  `engine.create_transport_zone()` operator function (not a new capability):
  - **"Local Bus Hub"** (zone id 107) — capacity 300, current_count 186 (~62%, MODERATE).
  - **"Metro Station Point"** (zone id 108) — capacity 220, current_count 176 (~80%, MODERATE).
  - Both were backfilled with **12 synthetic `CrowdSnapshot` rows each** (source=
    `"simulation"`, gently rising trend, 2-minute spacing) so the LSTM has enough history
    to produce a real forecast for them immediately, rather than returning
    `available: false`.
  - This is additive: no existing zone, snapshot, or event row was touched to do this.
- **`ServicePOI` demo seeding**: the first time any of the 4 new attendee endpoints is
  called for an event with zero `ServicePOI` rows, `_ensure_service_pois_seeded()`
  inserts a minimal, clearly-labeled set — **5 food + 10 essential + 6 emergency = 21
  rows** — at small deterministic coordinate offsets (~100-300m) from that event's real
  venue point. This is **idempotent**: re-calling never duplicates rows (verified — see
  Section H). All rows carry `source = "demo_seed"` and surface as
  `"Demo/reference data"` in every API response and every UI card.
- **Known accidental state mutation during testing**: while regression-testing the
  **pre-existing** `/api/action-plans/approve` operator endpoint (not something Terminal
  2 built), one real "redirect" action was approved against Event 13's live simulation,
  moving visitors between "Back Gate 1" and "Back Gate 2" (their `current_count` values
  changed). This is a **normal, reversible simulation-state change** — the same kind of
  change that already happens continuously as the sim clock ticks — not data loss, not a
  schema change, and not irreversible. No event, booking, or crowd-snapshot row was
  deleted. Flagging it explicitly since a peer session may be actively watching/using
  Event 13's command-center view.
- A separate test booking (`email = "regression-test@example.com"`) was created during
  the booking-flow regression test and was **deleted immediately after** (its
  `VisitorProfile` row removed, its tier's `booked_count` decremented back) — confirmed
  no test artifacts remain in `visitor_profiles` for that email.

---

## H. TEST RESULTS (all passed at last verification)

- **Backend**: `python -m py_compile app/models.py app/engine.py app/main.py` — clean.
  `from app.main import app` — imports cleanly, 132+ routes registered, no startup errors.
- **API — new endpoints**: restaurants, essentials, emergency, venue-pois all returned
  correct data for Event 13 and correctly returned `[]`/`"No event context available."`
  for a nonexistent event id (99999) — no fake data ever returned.
- **Event isolation**: Event 13's restaurant IDs (`[4,3,2,5,1]`) vs Event 8's
  (`[22,23,24,25,26]`) — confirmed disjoint, no cross-event leakage.
- **Idempotent seeding**: repeat calls to the same event's restaurant endpoint returned
  the same 5 rows, no duplicates created.
- **Booking flow**: Explore → Event 13 → Tier → Book → QR code → `/api/my-plan` →
  `/api/my-bookings` — full golden path confirmed working end-to-end; test booking
  cleaned up afterward.
- **ML endpoints**: `GET /api/ai/forecast/{zone_id}` (LSTM) returns real numbers for
  Event 13's zones; `GET /api/ai/network-pressure?event_id=13` (GNN) honestly returns
  `available: false` with a stated reason.
- **Transport crowd**: `GET /api/attendee/transport?event_id=13` returns
  `level`/`level_label`/`source`/`source_label`/`forecast_15m_pct` correctly, with a
  reasoning string naming the actual numbers.
- **Operator flow**: `/api/recommendations` and `/api/action-plans/approve` both work
  (this is what caused the state mutation noted in Section G) — confirms the
  recommend → approve → mutate pipeline, and that the Risk Engine's scores were
  otherwise unaffected by any ML/POI addition (`/api/state` zone scores checked before
  and after — deterministic formula unchanged).
- **Government dashboard**: `/api/government/overview` still returns its full expected
  key set (now also includes Terminal 1's `emergency` aggregation — see Section J).
- **Service provider**: `/api/partner/hotels/{id}/nearby-events` still returns correct
  data; portal UI changes did not alter any API contract.
- **Mobile**: `npx tsc --noEmit` from `mobile/` — exit code 0, no type errors.
- **Frontend JS syntax**: inline `<script>` blocks in `event-detail.html`,
  `my-events.html`, `events.html`, and `service-provider.html` all pass `node --check`.
- **Static file serving**: all touched/new HTML files (`event-detail.html`,
  `my-events.html`, `events.html`, `service-provider.html`, `ai-architecture.html`,
  `login.html`) return HTTP 200 from the FastAPI static mount.
- **Browser/visual testing**: **NOT performed** — no browser/simulator was available in
  this environment. All frontend verification was HTTP-response-level and JS-syntax-
  level only; a real click-through in a browser has not been done and should happen
  before the live demo.

---

## I. KNOWN LIMITATIONS (full honesty)

1. **GNN network-pressure does not work for Event 13** (or any custom-named event) — it
   only recognizes the original seeded demo zone names. This is a pre-existing model
   limitation, honestly surfaced (not silently hidden, not patched/faked).
2. **LSTM occasionally forecasts >100% occupancy** on a sharply rising trend (seen on
   both original gate zones and the newly added transport zones) — a real generalization
   limit of the trained model on this data pattern, not something introduced by this
   session's changes.
3. **Mobile has no embedded interactive map** — "Venue Map" on mobile is a categorized
   list with external-Maps "Navigate" links, not an in-app Leaflet/native map, because no
   map SDK is installed and none was added (would require an untestable native
   dependency in this environment).
4. **No shared frontend JS module** — every `.html` page still duplicates its own
   `api()`/`getEmail()`/label-formatting helpers, per pre-existing project convention;
   the new sections follow this same duplication pattern rather than introducing a
   partial refactor.
5. **`frontend/event-detail-gpt-preview.html`** exists as a separate, apparently-unused
   draft file (noted by earlier investigation, not touched or reconciled this session).
6. **No browser-based visual QA was performed** (see Section H) — recommend a manual
   click-through of Demo 1/2/3 before the actual presentation.
7. **`backend/app/engine.py` and `backend/app/main.py` are now shared/co-edited files**
   between Terminal 1 and Terminal 2 (see Section J) — the next session must treat them
   as such, not assume they're Terminal-2-exclusive.

---

## J. PARALLEL TERMINAL

**Terminal 1 has concurrent, in-progress changes.** Confirmed at handoff time:

- `frontend/command-center.html` — Terminal 1 added an operator-facing "How Vyavastha AI
  Works" modal (KPI row relabeling: Event Health / Current Footfall / High-Critical
  Zones / Open Alerts / Transport Pressure; a new `mlArchModal` with its own LSTM/GNN/
  decision-layer writeup). **Terminal 2 never opened this file for writing.**
- `frontend/government.html` — Terminal 1 modified this too (44 lines), almost certainly
  to surface the new `/api/government/emergency-services` endpoint (see below).
  **Terminal 2 never opened this file for writing either.**
- **Important — not just a frontend-only overlap**: Terminal 1 also added a new function,
  `engine.government_emergency_services(db)`, and a new route,
  `GET /api/government/emergency-services`, directly inside `backend/app/engine.py` and
  `backend/app/main.py` — **the same two files Terminal 2 edited.** Critically, Terminal
  1's addition **builds on top of Terminal 2's `ServicePOI` model and helper functions**
  (`_ensure_service_pois_seeded`, `_poi_venue_point`, `_poi_out`) rather than conflicting
  with or duplicating them — this looks like compatible, aware integration, not a blind
  overwrite. Confirmed via `grep`: Terminal 2's `ServicePOI` class, all 4 new attendee
  routes, and the transport label/source fields are all still present and the backend
  still compiles cleanly as of this handoff.

**DO NOT overwrite, revert, or "clean up" `command-center.html` or `government.html`.**
When reconciling in the next session, treat `engine.py`/`main.py` as shared files and
diff carefully before any further edit — a naive full-file rewrite of either could
silently drop one terminal's work.

---

## K. NEXT STEPS

The next session should, in order:

1. Run `git status` (see the fresh output appended below) and re-confirm the file list
   above still matches — if Terminal 1 has pushed further changes since this handoff was
   written, re-diff before touching anything.
2. **Reconcile Terminal 1 + Terminal 2 changes** in `engine.py`/`main.py` — read both
   sets of additions carefully (they currently coexist cleanly; the risk is only in a
   *future* edit made without awareness of both). Do not touch
   `command-center.html`/`government.html` without first understanding Terminal 1's
   intent there.
3. **Run a final full regression pass** (backend compile+import, the API smoke tests
   listed in Section H, `npx tsc --noEmit` in `mobile/`) to confirm nothing has drifted
   since this handoff.
4. **Do NOT add unnecessary features** — this phase's scope (restaurants, essentials,
   emergency, venue map, ML documentation, transport UX, mobile parity) is functionally
   complete; further work should be bug-fixing, reconciliation, or demo polish only.
5. **Prepare the final demo/PPT/video** using the three verified journeys: attendee
   (Explore → Event 13 → book → hotel → transport/LSTM → Uber → food/essentials/
   emergency/map → alerts), Service Provider (claim → nearby events → opt-in →
   prediction → report), and Operator→Attendee loop (camera/manual update → risk engine
   → recommendation → approval → attendee-side advisory change).
6. **Only commit/push after final verification** — nothing in this phase has been
   committed or pushed; the working tree currently holds all of both terminals'
   uncommitted work.

---

## Appendix: `git status --short` (run fresh, immediately before saving this file)

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

Note: `HANDOFF_TERMINAL_1.md` appeared in this same status check — Terminal 1 is
writing its own handoff concurrently. The next session should read **both** handoff
files before reconciling anything.
