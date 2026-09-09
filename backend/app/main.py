from pathlib import Path
import json
import math
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from . import engine, ml_advisory, models
from .detector import detector
from .regions import INDIA_REGIONS
from .database import Base, SessionLocal, engine as db_engine, get_db

Base.metadata.create_all(bind=db_engine)

# create_all only creates missing tables, not missing columns on a table that
# already exists — needed once a real event's zones already live in the DB
# (no Alembic here, see database.py), so a model field added after the fact
# is patched in by hand, guarded so it's a no-op once the column is present.
with db_engine.begin() as conn:
    zone_columns = {c["name"] for c in inspect(db_engine).get_columns("zones")}
    if "is_accessible" not in zone_columns:
        conn.execute(text("ALTER TABLE zones ADD COLUMN is_accessible BOOLEAN NOT NULL DEFAULT FALSE"))
    event_columns = {c["name"] for c in inspect(db_engine).get_columns("events")}
    if "owner_email" not in event_columns:
        conn.execute(text("ALTER TABLE events ADD COLUMN owner_email VARCHAR"))
    if "venue_lat" not in event_columns:
        conn.execute(text("ALTER TABLE events ADD COLUMN venue_lat FLOAT"))
        conn.execute(text("ALTER TABLE events ADD COLUMN venue_lng FLOAT"))
    user_account_columns = {c["name"] for c in inspect(db_engine).get_columns("user_accounts")}
    if "managed_zone_id" not in user_account_columns:
        conn.execute(text("ALTER TABLE user_accounts ADD COLUMN managed_zone_id INTEGER"))

app = FastAPI(title="VYAVASTHA — PS-8 Mega-Event Orchestration")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class WhatIfRequest(BaseModel):
    redirect_count: int = 0
    open_gate3: bool = False
    add_buses: int = 0
    move_staff: int = 0
    from_zone: str = "Gate 2"
    to_zone: str = "Gate 3"


class PlanSpec(BaseModel):
    name: str
    redirect_count: int = 0
    open_gate3: bool = False
    add_buses: int = 0
    move_staff: int = 0
    from_zone: str = "Gate 2"
    to_zone: str = "Gate 3"


class ComparePlansRequest(BaseModel):
    plans: list[PlanSpec]


class ApproveRequest(BaseModel):
    action_ids: list[str]


class RegisterRequest(BaseModel):
    name: str
    gate_zone_id: int
    walk_in: bool = False


class CheckinRequest(BaseModel):
    code: str


class LoginRequest(BaseModel):
    email: str
    role: str


class AckRequest(BaseModel):
    status: str  # open | acknowledged | escalated


class ChatRequest(BaseModel):
    question: str


class ZoneCapacityUpdate(BaseModel):
    capacity: int


class CrowdCountIngest(BaseModel):
    count: int = Field(ge=0)
    source: str = "manual"
    email: str | None = None


class CameraFeedRequest(BaseModel):
    zone_id: int
    stream_url: str
    model_path: str = "yolov8n.pt"
    sample_seconds: float = Field(default=1.0, ge=0.2, le=30)


class WebcamFeedRequest(BaseModel):
    zone_id: int
    device_index: int = 0  # OpenCV camera index on the BACKEND machine -- see POST /api/cameras/webcam's docstring
    model_path: str = "yolov8n.pt"
    sample_seconds: float = Field(default=1.0, ge=0.2, le=30)


class RtspFeedRequest(BaseModel):
    zone_id: int
    rtsp_url: str  # operator-provided, authorized stream only -- see POST /api/cameras/rtsp's docstring
    model_path: str = "yolov8n.pt"
    sample_seconds: float = Field(default=1.0, ge=0.2, le=30)


class EventUpdate(BaseModel):
    name: str
    expected_attendance: int
    safe_capacity: int


class EventCreate(BaseModel):
    name: str
    region: str
    expected_attendance: int
    safe_capacity: int
    owner_email: str | None = None
    venue_lat: float
    venue_lng: float


class SwitchEventRequest(BaseModel):
    event_id: int
    email: str


class ResourceUpdate(BaseModel):
    quantity_total: int = Field(ge=0)
    email: str | None = None


class RegionUpdate(BaseModel):
    state: str


class ScenarioTrigger(BaseModel):
    scenario_id: int


class ScenarioUpsert(BaseModel):
    name: str
    description: str = ""
    duration_ticks: int
    effects: dict[str, int]  # {"<zone name>": <count added per tick>}


class ZoneCreate(BaseModel):
    name: str
    lat: float
    lng: float
    capacity: int
    domain: str = "venue"
    type: str = "gate"
    email: str | None = None


class ZoneLocationUpdate(BaseModel):
    lat: float
    lng: float


class GateSetupUpdate(BaseModel):
    capacity: int
    staff_assigned: int | None = None
    is_accessible: bool | None = None
    name: str | None = None
    email: str | None = None


class HotelCreate(BaseModel):
    name: str
    capacity: int
    lat: float
    lng: float
    price_tier: int | None = None
    contact: str | None = None
    amenities: str | None = None
    email: str | None = None


class HotelUpdate(BaseModel):
    name: str | None = None
    capacity: int | None = None
    occupied_rooms: int | None = None
    price_tier: int | None = None
    contact: str | None = None
    amenities: str | None = None
    manual_recommended: bool | None = None
    lat: float | None = None
    lng: float | None = None
    email: str | None = None


class HotelDiscoveryRequest(BaseModel):
    lat: float
    lng: float
    radius_m: int = Field(default=5000, ge=100, le=50000)


class GeocodeRequest(BaseModel):
    query: str


class TransportDiscoveryRequest(BaseModel):
    lat: float
    lng: float
    radius_m: int = Field(default=5000, ge=100, le=50000)


class HotelAvailabilityUpdate(BaseModel):
    occupied_rooms: int


class HotelWebhookUpdate(BaseModel):
    hotel_id: int
    occupied_rooms: int
    source: str = "hotel_pms"


class ClaimHotelRequest(BaseModel):
    email: str
    hotel_id: int


class HotelOptInRequest(BaseModel):
    event_id: int
    status: str = "opted_in"  # opted_in | declined


class TransportZoneCreate(BaseModel):
    name: str
    capacity: int
    lat: float
    lng: float
    type: str = "corridor"
    contact: str | None = None
    email: str | None = None


class TransportZoneUpdate(BaseModel):
    capacity: int | None = None
    current_count: int | None = None
    contact: str | None = None
    email: str | None = None


class AlertStatusUpdate(BaseModel):
    status: str  # open | acknowledged | resolved


class EmergencyTrigger(BaseModel):
    zone_id: int
    message: str


class AttendeeEmail(BaseModel):
    email: str


class AccessibilityRequest(BaseModel):
    email: str | None = None
    note: str | None = None
    lat: float | None = None
    lng: float | None = None
    zone_name: str | None = None


class SetCurrentEvent(BaseModel):
    email: str
    event_attendee_id: int


class TierSpec(BaseModel):
    name: str
    price: float
    capacity: int
    gate_name: str | None = None


class EventListingCreate(BaseModel):
    name: str
    description: str = ""
    event_date: str | None = None
    event_time: str | None = None
    category: str | None = None
    city: str | None = None
    venue_name: str | None = None
    venue_address: str | None = None
    banner_emoji: str | None = None
    is_featured: bool = False
    region: str | None = None
    expected_attendance: int
    safe_capacity: int
    tiers: list[TierSpec]
    venue_lat: float | None = None
    venue_lng: float | None = None
    owner_email: str | None = None  # Event Command Operator who created it — lets the
    # organizer's "Create New Event" wizard list events it created, without
    # touching engine.create_event()/the live-event slot at all.


class EventSportsDetailsRequest(BaseModel):
    sport: str
    competition: str | None = None
    tournament_gender: str | None = None
    stage: str | None = None
    team_home: str | None = None
    team_away: str | None = None
    owner_email: str | None = None  # checked against Event.owner_email below —
    # not a real auth boundary (same demo-auth trust as the rest of this app),
    # but keeps this write ownership-scoped rather than open to any caller.


class BookTierRequest(BaseModel):
    name: str
    email: str | None = None
    seat_id: int | None = None
    quantity: int = 1
    hotel_zone_id: int | None = None
    wants_transport: bool = False
    budget_tier: int | None = None


# --- role enforcement (backward-compatible) ---------------------------------
# Reads an optional X-User-Role header. Absent header = full access (every
# client that predates this change keeps working exactly as before). No
# operator role is domain-scoped anymore (Venue/Transport/Hospitality
# Operator were all folded into Event Command Operator), so any role other
# than that one is simply denied on these domain-gated actions.
FULL_ACCESS_ROLES = {"Event Command Operator"}


def get_role(x_user_role: str | None = Header(default=None)):
    return x_user_role


def require_domain_access(role: str | None, domain: str | None):
    if role is not None and role not in FULL_ACCESS_ROLES:
        raise HTTPException(403, f"role '{role}' cannot act on domain '{domain}'")


def require_event_owner(event, email: str | None, *, strict: bool = False):
    """Blocks editing an event that belongs to a different Event Command
    Operator. Untracked events (owner_email is None — older seed/demo data)
    are never gated, since there's no real owner to check against.
    `strict=True` also rejects a missing email — used by Event Setup's own
    endpoints, which always send one; the shared /api/zones endpoints (also
    used by Command Centre's map-click add/delete, which doesn't send an
    email yet) stay lenient when email is omitted so they keep working."""
    if not event or not event.owner_email:
        return
    if not email:
        if strict:
            raise HTTPException(403, f"This event belongs to {event.owner_email} — sign in as that operator to edit it.")
        return
    if event.owner_email.strip().lower() != email.strip().lower():
        raise HTTPException(403, f"This event belongs to {event.owner_email} — sign in as that operator to edit it.")


def require_admin(role: str | None):
    # The Administrator role was folded into Event Command Operator — this
    # back-office surface (event config, scenarios, users, raw tables) is now
    # gated the same way as the rest of that role's access.
    if role is not None and role != "Event Command Operator":
        raise HTTPException(403, "Event Command Operator role required")


@app.get("/api/event")
def get_event(db: Session = Depends(get_db)):
    event = engine.get_live_event(db)
    if not event:
        return {"configured": False}
    return {
        "configured": True, "id": event.id, "name": event.name, "region": event.region,
        "expected_attendance": event.expected_attendance,
        "safe_capacity": event.safe_capacity, "status": event.status,
        "venue_lat": event.venue_lat, "venue_lng": event.venue_lng,
        "owner_email": event.owner_email,
    }


@app.get("/api/regions")
def list_regions():
    return [{"state": k, "city": v["city"]} for k, v in INDIA_REGIONS.items()]


@app.post("/api/admin/region")
def admin_apply_region(req: RegionUpdate, db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    require_admin(role)
    result = engine.apply_region(db, req.state)
    if result is None:
        raise HTTPException(404, "unknown state/UT")
    return result


@app.get("/api/state")
def get_state(db: Session = Depends(get_db)):
    return engine.full_state(db)


@app.get("/api/zones")
def get_zones(db: Session = Depends(get_db)):
    return engine.full_state(db)["zones"]


@app.get("/api/risks")
def get_risks(db: Session = Depends(get_db)):
    return engine.full_state(db)["zones"]


@app.get("/api/risks/causal-chain")
def get_causal_chain(db: Session = Depends(get_db)):
    return {"chain": engine.causal_chain(db)}


@app.get("/api/scenarios")
def list_scenarios(db: Session = Depends(get_db)):
    return engine.list_scenarios(db)


@app.post("/api/admin/scenarios")
def create_scenario(req: ScenarioUpsert, db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    require_admin(role)
    scenario = engine.create_scenario(db, req.name, req.description, req.duration_ticks, req.effects)
    return {"id": scenario.id}


@app.patch("/api/admin/scenarios/{scenario_id}")
def update_scenario(scenario_id: int, req: ScenarioUpsert, db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    require_admin(role)
    scenario = engine.update_scenario(db, scenario_id, req.name, req.description, req.duration_ticks, req.effects)
    if scenario is None:
        raise HTTPException(404, "scenario not found")
    return {"id": scenario.id}


@app.delete("/api/admin/scenarios/{scenario_id}")
def delete_scenario(scenario_id: int, db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    require_admin(role)
    ok = engine.delete_scenario(db, scenario_id)
    if not ok:
        raise HTTPException(404, "scenario not found")
    return {"deleted": scenario_id}


@app.post("/api/scenario/trigger")
def trigger(req: ScenarioTrigger, db: Session = Depends(get_db)):
    state = engine.trigger_scenario(db, req.scenario_id)
    if state is None:
        raise HTTPException(404, "scenario not found")
    return engine.full_state(db)


@app.post("/api/tick")
def tick(db: Session = Depends(get_db)):
    engine.advance_tick(db)
    return engine.full_state(db)


@app.post("/api/zones/{zone_id}/crowd-count")
def ingest_crowd_count(zone_id: int, req: CrowdCountIngest, db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    zone = db.get(models.Zone, zone_id)
    if not zone:
        raise HTTPException(404, "zone not found")
    require_domain_access(role, zone.domain)
    require_event_owner(db.get(models.Event, zone.event_id), req.email, strict=True)
    try:
        result = engine.ingest_crowd_count(db, zone_id, req.count, req.source.strip()[:50] or "manual")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return result


@app.get("/api/cameras")
def camera_status():
    return detector.status()


@app.get("/api/zones/{zone_id}/crowd-history")
def crowd_history(zone_id: int, limit: int = 60, db: Session = Depends(get_db)):
    if not db.get(models.Zone, zone_id):
        raise HTTPException(404, "zone not found")
    rows = (
        db.query(models.CrowdSnapshot)
        .filter(models.CrowdSnapshot.zone_id == zone_id)
        .order_by(models.CrowdSnapshot.captured_at.desc())
        .limit(min(max(limit, 1), 500))
        .all()
    )
    return [{"count": row.count, "source": row.source, "captured_at": row.captured_at.isoformat()} for row in rows]


@app.post("/api/cameras")
def start_camera(req: CameraFeedRequest, db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    """Generic/original entry point -- a free-typed stream URL (the existing
    Event Setup 'Phone/IP camera stream URL' box, e.g. a phone running an IP
    Webcam app over HTTP MJPEG, not necessarily RTSP). Kept as-is for backward
    compatibility; POST /api/cameras/webcam and /rtsp below are the same
    underlying pipeline with a clearer, source-specific contract."""
    zone = db.get(models.Zone, req.zone_id)
    if not zone:
        raise HTTPException(404, "zone not found")
    require_domain_access(role, zone.domain)

    def record_count(zone_id: int, count: int):
        with SessionLocal() as worker_db:
            engine.ingest_crowd_count(worker_db, zone_id, count, "yolo")

    detector.start(req.zone_id, req.stream_url, req.model_path, req.sample_seconds, record_count, source_type="manual")
    return {"started": req.zone_id, "message": "Camera worker started; inspect GET /api/cameras for live status."}


@app.post("/api/cameras/webcam")
def start_camera_from_webcam(req: WebcamFeedRequest, db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    """Starts the BACKEND MACHINE's local webcam via OpenCV's device index
    (cv2.VideoCapture(0), same as detector.py already supports for any
    digit-string stream_url) -- NOT the operator's browser webcam. A
    browser's camera stream never reaches this backend process; capturing
    that would need a separate WebRTC/MediaRecorder upload path, which this
    endpoint does not attempt. For the hackathon this backend-machine
    webcam (e.g. a laptop's built-in camera, when the backend runs on the
    demo laptop) is the intended path."""
    zone = db.get(models.Zone, req.zone_id)
    if not zone:
        raise HTTPException(404, "zone not found")
    require_domain_access(role, zone.domain)

    def record_count(zone_id: int, count: int):
        with SessionLocal() as worker_db:
            engine.ingest_crowd_count(worker_db, zone_id, count, "yolo")

    detector.start(req.zone_id, str(req.device_index), req.model_path, req.sample_seconds, record_count, source_type="webcam")
    return {"started": req.zone_id, "message": "Webcam worker started; inspect GET /api/cameras for live status."}


@app.post("/api/cameras/rtsp")
def start_camera_from_rtsp(req: RtspFeedRequest, db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    """Starts a worker against an OPERATOR-PROVIDED, authorized CCTV/IP-camera
    stream URL (typically rtsp://, though the same OpenCV call also accepts
    an http(s):// MJPEG stream some IP cameras use -- both go through the
    identical cv2.VideoCapture(url) -> YOLO pipeline, no separate CCTV
    implementation). This does NOT connect to, or claim access to, any real
    government/municipal/venue CCTV network -- it only starts a worker
    against whatever authorized URL the operator explicitly supplies."""
    zone = db.get(models.Zone, req.zone_id)
    if not zone:
        raise HTTPException(404, "zone not found")
    require_domain_access(role, zone.domain)
    url = req.rtsp_url.strip()
    if "://" not in url:
        raise HTTPException(400, "rtsp_url must be a full stream URL, e.g. rtsp://user:pass@host:port/path")

    def record_count(zone_id: int, count: int):
        with SessionLocal() as worker_db:
            engine.ingest_crowd_count(worker_db, zone_id, count, "yolo")

    detector.start(req.zone_id, url, req.model_path, req.sample_seconds, record_count, source_type="rtsp")
    return {"started": req.zone_id, "message": "RTSP camera worker started; inspect GET /api/cameras for live status."}


UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"


@app.post("/api/cameras/upload")
def start_camera_from_upload(
    zone_id: int = Form(...), sample_seconds: float = Form(1.0), model_path: str = Form("yolov8n.pt"),
    file: UploadFile = File(...), db: Session = Depends(get_db), role: str | None = Depends(get_role),
):
    """Same detector.start() pipeline as POST /api/cameras (webcam device-index
    or CCTV/RTSP stream_url) — a video file is just a third source cv2.VideoCapture
    already accepts, so this only adds the one missing piece: getting the
    uploaded bytes onto disk first, then pointing the existing worker at that
    path. No separate perception pipeline.

    Registered BEFORE the DELETE /api/cameras/{zone_id} route below on purpose:
    Starlette matches routes in registration order, and {zone_id} (untyped in
    the path template) would otherwise swallow the literal "upload" segment."""
    zone = db.get(models.Zone, zone_id)
    if not zone:
        raise HTTPException(404, "zone not found")
    require_domain_access(role, zone.domain)

    UPLOAD_DIR.mkdir(exist_ok=True)
    suffix = Path(file.filename or "upload.mp4").suffix or ".mp4"
    saved_path = UPLOAD_DIR / f"zone{zone_id}_{uuid.uuid4().hex[:8]}{suffix}"
    with open(saved_path, "wb") as out:
        out.write(file.file.read())

    def record_count(zid: int, count: int):
        with SessionLocal() as worker_db:
            engine.ingest_crowd_count(worker_db, zid, count, "yolo")

    detector.start(zone_id, str(saved_path), model_path, sample_seconds, record_count, source_type="upload")
    return {"started": zone_id, "message": "Camera worker started from uploaded video; inspect GET /api/cameras for live status."}


@app.delete("/api/cameras/{zone_id}")
def stop_camera(zone_id: int, db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    zone = db.get(models.Zone, zone_id)
    if not zone:
        raise HTTPException(404, "zone not found")
    require_domain_access(role, zone.domain)
    if not detector.stop(zone_id):
        raise HTTPException(404, "camera feed not found")
    return {"stopped": zone_id}


@app.post("/api/reset")
def reset(db: Session = Depends(get_db)):
    engine.reset_simulation(db)
    return engine.full_state(db)


@app.post("/api/simulate/whatif")
def whatif(req: WhatIfRequest, db: Session = Depends(get_db)):
    return engine.run_whatif(
        db, req.redirect_count, req.open_gate3, req.add_buses, req.move_staff, req.from_zone, req.to_zone
    )


@app.post("/api/simulate/compare")
def simulate_compare(req: ComparePlansRequest, db: Session = Depends(get_db)):
    return engine.compare_plans(db, req.plans)


@app.get("/api/recommendations")
def recommendations(db: Session = Depends(get_db)):
    return engine.generate_recommendations(db)


@app.get("/api/risks/preventive")
def preventive_alerts(db: Session = Depends(get_db)):
    return engine.preventive_alerts(db)


@app.get("/api/offpeak")
def offpeak(db: Session = Depends(get_db)):
    return engine.offpeak_recommendations(db)


@app.get("/api/risks/escalations")
def escalations(db: Session = Depends(get_db)):
    return engine.escalations(db)


@app.get("/api/risks/register")
def risks_register(db: Session = Depends(get_db)):
    return engine.risk_register(db)


@app.get("/api/timeline")
def timeline(db: Session = Depends(get_db)):
    return engine.recent_log(db)


@app.get("/api/execution-status")
def execution_status(db: Session = Depends(get_db)):
    return engine.recent_log(db, category="action_executed")


@app.post("/api/chatbot/ask")
def chatbot_ask(req: ChatRequest, db: Session = Depends(get_db)):
    return engine.chatbot_answer(db, req.question)


@app.post("/api/action-plans/approve")
def approve(req: ApproveRequest, db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    if role is not None and role not in FULL_ACCESS_ROLES:
        action_domains = {a["id"]: a["domain"] for a in engine.CANDIDATE_ACTIONS}
        for aid in req.action_ids:
            require_domain_access(role, action_domains.get(aid))
    applied = engine.approve_actions(db, req.action_ids)
    return {"applied": applied, "state": engine.full_state(db)}


@app.post("/api/register")
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    zone = db.get(models.Zone, req.gate_zone_id)
    if not zone:
        raise HTTPException(404, "zone not found")
    visitor = engine.register_visitor(db, req.name, req.gate_zone_id, req.walk_in)
    return {"code": visitor.code, "name": visitor.name, "gate": zone.name}


@app.post("/api/checkin")
def checkin(req: CheckinRequest, db: Session = Depends(get_db)):
    return engine.checkin(db, req.code)


# --- event catalog: browse, book a tier/seat (BookMyShow-style) ------------

@app.get("/api/event-categories")
def list_categories():
    return engine.CATEGORIES


@app.get("/api/events")
def list_events(search: str | None = None, category: str | None = None, section: str | None = None, db: Session = Depends(get_db)):
    return engine.list_events(db, search=search, category=category, section=section)


@app.get("/api/events/mine")
def my_created_events(email: str, db: Session = Depends(get_db)):
    """The organizer's own 'My Events' dashboard — every event (any status)
    this email owns, server-side scoped so one organizer never sees another's
    events as manageable. Same demo-auth trust level as /api/admin/my-events
    (email is caller-supplied, not session-verified) — kept as its own
    endpoint so real auth can replace the trust boundary here later without
    touching the public catalog. Registered BEFORE /api/events/{event_id}
    below — Starlette matches routes in registration order, and "mine" would
    otherwise be swallowed by that path param and fail int-parsing."""
    return engine.list_events_owned_by(db, email)


@app.get("/api/events/{event_id}")
def get_event_detail(event_id: int, db: Session = Depends(get_db)):
    result = engine.event_detail(db, event_id)
    if result is None:
        raise HTTPException(404, "event not found")
    return result


@app.get("/api/events/{event_id}/tiers/{tier_id}/seats")
def get_tier_seats(event_id: int, tier_id: int, db: Session = Depends(get_db)):
    result = engine.tier_seats(db, tier_id)
    if result is None:
        raise HTTPException(404, "tier not found or doesn't use numbered seats")
    return result


@app.post("/api/events")
def create_event_listing(req: EventListingCreate, db: Session = Depends(get_db)):
    if not req.name.strip():
        raise HTTPException(400, "name is required")
    event = engine.create_event_listing(
        db, req.name, req.description, req.event_date, req.region,
        req.expected_attendance, req.safe_capacity, [t.model_dump() for t in req.tiers],
        event_time=req.event_time, category=req.category, city=req.city, venue_name=req.venue_name,
        venue_address=req.venue_address, banner_emoji=req.banner_emoji, is_featured=req.is_featured,
        venue_lat=req.venue_lat, venue_lng=req.venue_lng,
        owner_email=req.owner_email.strip().lower() if req.owner_email else None,
    )
    if event is None:
        raise HTTPException(409, f'An event named "{req.name.strip()}" already exists. Choose a different name.')
    return {"id": event.id, "name": event.name, "status": event.status}


@app.get("/api/events/{event_id}/sports-details")
def get_event_sports_details(event_id: int, db: Session = Depends(get_db)):
    return engine.get_event_sports_details(db, event_id)


@app.post("/api/events/{event_id}/sports-details")
def set_event_sports_details(event_id: int, req: EventSportsDetailsRequest, db: Session = Depends(get_db)):
    event = db.get(models.Event, event_id)
    if not event:
        raise HTTPException(404, "event not found")
    # Ownership check: only this event's own organizer may edit its sports/
    # competition record — one event, one primary organizer (see
    # engine.list_events_owned_by's docstring for the trust model this
    # follows). An event with no owner_email set (e.g. Event 13, created
    # before this field existed) simply can't be edited through this route.
    owner = (event.owner_email or "").strip().lower()
    caller = (req.owner_email or "").strip().lower()
    if not owner or owner != caller:
        raise HTTPException(403, "not the organizer for this event")
    try:
        ml_advisory.validate_sports_vocab(req.sport, req.competition, req.tournament_gender, req.stage)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return engine.upsert_event_sports_details(
        db, event_id, req.sport, req.competition, req.tournament_gender, req.stage, req.team_home, req.team_away,
    )


@app.delete("/api/events/{event_id}/sports-details")
def remove_event_sports_details(event_id: int, db: Session = Depends(get_db)):
    if not engine.delete_event_sports_details(db, event_id):
        raise HTTPException(404, "no sports details set for this event")
    return {"status": "deleted"}


@app.get("/api/events/{event_id}/ticket-demand")
def get_event_ticket_demand(event_id: int, db: Session = Depends(get_db)):
    event = db.get(models.Event, event_id)
    if not event:
        raise HTTPException(404, "event not found")
    return engine.event_ticket_demand(db, event)


@app.post("/api/events/{event_id}/tiers/{tier_id}/book")
def book_tier(event_id: int, tier_id: int, req: BookTierRequest, db: Session = Depends(get_db)):
    return engine.book_tier(
        db, event_id, tier_id, req.name, req.seat_id, req.email, req.quantity, req.hotel_zone_id, req.wants_transport,
        req.budget_tier,
    )


@app.get("/api/my-bookings")
def my_bookings(email: str, db: Session = Depends(get_db)):
    return engine.list_my_bookings(db, email.strip().lower())


@app.get("/api/my-plan")
def my_plan(code: str, db: Session = Depends(get_db)):
    plan = engine.attendee_plan(db, code)
    if plan is None:
        raise HTTPException(404, "booking not found")
    return plan


@app.get("/api/auth/roles")
def list_roles():
    return models.ROLES


@app.post("/api/auth/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    email = req.email.strip().lower()
    role = req.role.strip()
    if not email or role not in models.ROLES:
        raise HTTPException(400, "email and a valid role are required")

    existing = (
        db.query(models.UserAccount)
        .filter(models.UserAccount.email == email, models.UserAccount.role == role)
        .first()
    )
    if existing:
        return {"status": "login", "email": existing.email, "role": existing.role}

    account = models.UserAccount(email=email, role=role)
    db.add(account)
    db.commit()
    db.refresh(account)
    return {"status": "signup", "email": account.email, "role": account.role}


@app.get("/api/zones/{zone_id}/capacity")
def capacity(zone_id: int, db: Session = Depends(get_db)):
    result = engine.zone_capacity(db, zone_id)
    if result is None:
        raise HTTPException(404, "zone not found")
    return result


@app.post("/api/zones")
def create_zone(req: ZoneCreate, db: Session = Depends(get_db)):
    if req.domain not in models.DOMAINS:
        raise HTTPException(400, "domain must be venue, transport, or hospitality")
    if not req.name.strip():
        raise HTTPException(400, "name is required")
    require_event_owner(engine.get_live_event(db), req.email)
    zone = engine.create_zone(db, req.name.strip(), req.lat, req.lng, req.capacity, req.domain, req.type)
    if zone is None:
        raise HTTPException(404, "no event configured yet")
    return {"id": zone.id, "name": zone.name}


@app.patch("/api/zones/{zone_id}/location")
def update_zone_location(zone_id: int, req: ZoneLocationUpdate, db: Session = Depends(get_db)):
    zone = engine.update_zone_location(db, zone_id, req.lat, req.lng)
    if zone is None:
        raise HTTPException(404, "zone not found")
    return {"id": zone.id, "lat": zone.lat, "lng": zone.lng}


@app.delete("/api/zones/{zone_id}")
def delete_zone(zone_id: int, email: str | None = None, db: Session = Depends(get_db)):
    zone = db.get(models.Zone, zone_id)
    if zone:
        require_event_owner(db.get(models.Event, zone.event_id), email)
    result = engine.delete_zone(db, zone_id)
    if not result["ok"]:
        raise HTTPException(409 if "error" in result else 404, result.get("error", "not found"))
    return {"deleted": zone_id}


# --- Event Setup Form (Event Command Operator): gates, hotels, transport ---

@app.get("/api/event-setup")
def get_event_setup(db: Session = Depends(get_db)):
    return engine.event_setup_summary(db)


@app.patch("/api/event-setup/gates/{zone_id}")
def patch_gate_setup(zone_id: int, req: GateSetupUpdate, db: Session = Depends(get_db)):
    zone = db.get(models.Zone, zone_id)
    if zone:
        require_event_owner(db.get(models.Event, zone.event_id), req.email, strict=True)
    zone = engine.update_gate_setup(db, zone_id, req.capacity, req.staff_assigned, req.is_accessible, req.name)
    if zone is None:
        raise HTTPException(404, "gate not found")
    return {"id": zone.id}


@app.patch("/api/event-setup/resources/{resource_type}")
def patch_resource(resource_type: str, req: ResourceUpdate, db: Session = Depends(get_db)):
    if resource_type not in ("bus", "staff", "medical"):
        raise HTTPException(400, "resource_type must be bus, staff, or medical")
    require_event_owner(engine.get_live_event(db), req.email, strict=True)
    resource = engine.update_resource(db, resource_type, req.quantity_total)
    if resource is None:
        raise HTTPException(404, "no event configured yet")
    return {"type": resource.type, "quantity_total": resource.quantity_total, "quantity_available": resource.quantity_available}


@app.get("/api/evacuation-routes")
def get_evacuation_routes(
    accessible_only: bool = False, lat: float | None = None, lng: float | None = None,
    event_id: int | None = None, db: Session = Depends(get_db),
):
    return engine.evacuation_routes(db, accessible_only=accessible_only, lat=lat, lng=lng, event_id=event_id)


@app.post("/api/attendee/accessibility-request")
def post_accessibility_request(req: AccessibilityRequest, db: Session = Depends(get_db)):
    return engine.request_accessibility_assistance(
        db, email=req.email, note=req.note, lat=req.lat, lng=req.lng, zone_name=req.zone_name,
    )


@app.post("/api/event-setup/hotels")
def post_hotel(req: HotelCreate, db: Session = Depends(get_db)):
    require_event_owner(engine.get_live_event(db), req.email, strict=True)
    zone = engine.create_hotel(db, req.name, req.capacity, req.lat, req.lng, req.price_tier, req.contact, req.amenities)
    if zone is None:
        raise HTTPException(404, "no event configured yet")
    return {"id": zone.id}


@app.patch("/api/event-setup/hotels/{zone_id}")
def patch_hotel(zone_id: int, req: HotelUpdate, db: Session = Depends(get_db)):
    zone = db.get(models.Zone, zone_id)
    if zone:
        require_event_owner(db.get(models.Event, zone.event_id), req.email, strict=True)
    zone = engine.update_hotel(
        db, zone_id, req.capacity, req.occupied_rooms, req.price_tier, req.contact, req.amenities, req.manual_recommended,
        req.name, req.lat, req.lng,
    )
    if zone is None:
        raise HTTPException(404, "hotel not found")
    return {"id": zone.id}


@app.delete("/api/event-setup/hotels/{zone_id}")
def delete_hotel(zone_id: int, email: str | None = None, db: Session = Depends(get_db)):
    zone = db.get(models.Zone, zone_id)
    if zone:
        require_event_owner(db.get(models.Event, zone.event_id), email, strict=True)
    result = engine.delete_zone(db, zone_id)
    if not result["ok"]:
        raise HTTPException(409 if "error" in result else 404, result.get("error", "not found"))
    return {"deleted": zone_id}


@app.post("/api/event-setup/transport")
def post_transport_zone(req: TransportZoneCreate, db: Session = Depends(get_db)):
    require_event_owner(engine.get_live_event(db), req.email, strict=True)
    zone = engine.create_transport_zone(db, req.name, req.capacity, req.lat, req.lng, req.type, req.contact)
    if zone is None:
        raise HTTPException(404, "no event configured yet")
    return {"id": zone.id}


@app.patch("/api/event-setup/transport/{zone_id}")
def patch_transport_zone(zone_id: int, req: TransportZoneUpdate, db: Session = Depends(get_db)):
    zone = db.get(models.Zone, zone_id)
    if zone:
        require_event_owner(db.get(models.Event, zone.event_id), req.email, strict=True)
    zone = engine.update_transport_zone(db, zone_id, req.capacity, req.current_count, req.contact)
    if zone is None:
        raise HTTPException(404, "transport zone not found")
    return {"id": zone.id}


@app.delete("/api/event-setup/transport/{zone_id}")
def delete_transport_zone(zone_id: int, email: str | None = None, db: Session = Depends(get_db)):
    zone = db.get(models.Zone, zone_id)
    if zone:
        require_event_owner(db.get(models.Event, zone.event_id), email, strict=True)
    result = engine.delete_zone(db, zone_id)
    if not result["ok"]:
        raise HTTPException(409 if "error" in result else 404, result.get("error", "not found"))
    return {"deleted": zone_id}


@app.get("/api/risks/causal-chain/{zone_id}")
def get_zone_causal_chain(zone_id: int, db: Session = Depends(get_db)):
    return {"chain": engine.zone_causal_chain(db, zone_id)}


@app.post("/api/zones/{zone_id}/ack")
def ack_zone(zone_id: int, req: AckRequest, db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    zone = db.get(models.Zone, zone_id)
    if zone:
        require_domain_access(role, zone.domain)
    zone = engine.set_ack_status(db, zone_id, req.status)
    if zone is None:
        raise HTTPException(404, "zone not found or invalid status")
    return {"zone_id": zone.id, "ack_status": zone.ack_status}


@app.get("/api/advisory")
def advisory(db: Session = Depends(get_db)):
    return engine.gate_advisory(db)


@app.get("/api/ai/explain/{zone_id}")
def ai_explain(zone_id: int, db: Session = Depends(get_db)):
    return engine.explain_zone(db, zone_id)


@app.get("/api/ai/advisor")
def ai_advisor_endpoint(db: Session = Depends(get_db)):
    return engine.ai_advisor(db)


@app.get("/api/ai/attendee-advisory")
def ai_attendee_advisory(db: Session = Depends(get_db)):
    return engine.attendee_advisory(db)


@app.get("/api/ai/forecast/{zone_id}")
def ai_forecast(zone_id: int, db: Session = Depends(get_db)):
    """LSTM advisory forecast only — see ml_advisory.py's module docstring.
    Never merged into risk_factors()/zone_risk(); the deterministic Risk
    Engine's own numbers are untouched by this endpoint's existence."""
    result = ml_advisory.zone_forecast(db, zone_id)
    if result is None:
        raise HTTPException(404, "zone not found")
    return result


@app.get("/api/ai/network-pressure")
def ai_network_pressure(db: Session = Depends(get_db)):
    """GNN advisory network view for the live event only — see
    ml_advisory.py's module docstring. Advisory only, same guarantee as
    ai_forecast above."""
    live = engine.get_live_event(db)
    if not live:
        raise HTTPException(404, "no live event configured")
    return ml_advisory.event_network_pressure(db, live.id)


@app.get("/api/transport/flights")
def transport_flights(db: Session = Depends(get_db)):
    return engine.transport_hub_arrivals(db)


@app.get("/api/transport/local")
def transport_local(db: Session = Depends(get_db)):
    return engine.local_transit_feed(db)


# --- Back office (Event Command Operator): event configuration, no live-ops-only access beyond that role ----------

@app.patch("/api/admin/event")
def admin_update_event(req: EventUpdate, db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    require_admin(role)
    event = engine.get_live_event(db)
    if not event:
        raise HTTPException(404, "no event configured yet")
    event.name = req.name.strip() or event.name
    event.expected_attendance = req.expected_attendance
    event.safe_capacity = req.safe_capacity
    db.commit()
    return {"name": event.name, "expected_attendance": event.expected_attendance, "safe_capacity": event.safe_capacity}


@app.post("/api/admin/event")
def admin_create_event(req: EventCreate, db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    require_admin(role)
    if req.region not in INDIA_REGIONS:
        raise HTTPException(400, "unknown state/UT")
    if not req.name.strip():
        raise HTTPException(400, "name is required")
    owner_email = req.owner_email.strip().lower() if req.owner_email else None
    event = engine.create_event(
        db, req.name.strip(), req.region, req.expected_attendance, req.safe_capacity, owner_email,
        req.venue_lat, req.venue_lng,
    )
    return {"id": event.id, "name": event.name, "region": event.region}


@app.delete("/api/admin/event")
def admin_delete_event(db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    require_admin(role)
    engine.delete_event(db)
    return {"configured": False}


@app.delete("/api/admin/events/{event_id}")
def admin_delete_event_by_id(event_id: int, db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    """Deletes any single event (live or paused) by id — unlike DELETE
    /api/admin/event above, which always acts on whichever is live."""
    require_admin(role)
    if not engine.delete_event_by_id(db, event_id):
        raise HTTPException(404, "event not found")
    return {"deleted": event_id}


@app.get("/api/admin/my-events")
def admin_my_events(email: str, db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    """Events owned by this Event Command Operator — used by the login/
    header 'Switch Event' picker to decide whether it has anything to show."""
    require_admin(role)
    return engine.list_operator_events(db, email.strip().lower())


@app.get("/api/admin/all-events")
def admin_all_events(db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    """Every event regardless of owner/status — Back Office's 'Manage
    Events' list, so orphaned or other operators' events can be deleted too."""
    require_admin(role)
    return engine.list_all_events(db)


@app.post("/api/admin/switch-event")
def admin_switch_event(req: SwitchEventRequest, db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    require_admin(role)
    event = engine.switch_to_event(db, req.event_id, req.email.strip().lower())
    if event is None:
        raise HTTPException(404, "event not found, not yours, or not switchable")
    return {"id": event.id, "name": event.name, "status": event.status}


@app.patch("/api/admin/zones/{zone_id}")
def admin_update_zone_capacity(zone_id: int, req: ZoneCapacityUpdate, db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    require_admin(role)
    zone = db.get(models.Zone, zone_id)
    if not zone:
        raise HTTPException(404, "zone not found")
    if req.capacity <= 0:
        raise HTTPException(400, "capacity must be positive")
    zone.capacity = req.capacity
    db.commit()
    return {"id": zone.id, "name": zone.name, "capacity": zone.capacity}


@app.get("/api/admin/raw-tables")
def admin_raw_tables(db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    """Every row of every table, straight from the DB — for the raw table
    viewer page. Debug/inspection only, not used by any real screen."""
    require_admin(role)
    tables = {
        "events": models.Event,
        "zones": models.Zone,
        "resources": models.Resource,
        "visitor_profiles": models.VisitorProfile,
        "user_accounts": models.UserAccount,
        "scenarios": models.Scenario,
        "sim_state": models.SimState,
        "alerts": models.Alert,
        "notifications": models.Notification,
        "attendees": models.Attendee,
        "event_attendees": models.EventAttendee,
        "transit_routes": models.TransitRoute,
        "event_tiers": models.EventTier,
        "event_seats": models.EventSeat,
        "hotel_inventory_snapshots": models.HotelInventorySnapshot,
    }
    out = {}
    for name, model in tables.items():
        cols = [c.name for c in model.__table__.columns]
        rows = db.query(model).all()
        out[name] = {
            "columns": cols,
            "rows": [{c: getattr(r, c) for c in cols} for r in rows],
        }
    return out


@app.get("/api/admin/users")
def admin_list_users(db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    require_admin(role)
    users = db.query(models.UserAccount).order_by(models.UserAccount.email).all()
    return [{"id": u.id, "email": u.email, "role": u.role} for u in users]


@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: int, db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    require_admin(role)
    user = db.get(models.UserAccount, user_id)
    if not user:
        raise HTTPException(404, "user not found")
    db.delete(user)
    db.commit()
    return {"deleted": user_id}


# --- Event Health Score (Section 29) ----------------------------------------

@app.get("/api/health-score")
def health_score(db: Session = Depends(get_db)):
    return engine.event_health_score(db)


@app.get("/api/health-breakdown")
def health_breakdown(db: Session = Depends(get_db)):
    return engine.health_breakdown(db)


# --- Alerts + notifications (Sections 22-23, 27) ----------------------------

@app.get("/api/alerts")
def get_alerts(status: str | None = None, db: Session = Depends(get_db)):
    # Scoped to the live event only -- was previously unscoped, so the
    # Command Centre's Alerts tab (its only caller) mixed in alerts from
    # every other event ever created (including resolved ones from past
    # demo runs), which is exactly the "resolved alerts look like current
    # emergencies" confusion this needs to avoid.
    live = engine.get_live_event(db)
    if not live:
        return []
    return engine.list_alerts(db, status=status, event_id=live.id)


@app.patch("/api/alerts/{alert_id}")
def patch_alert(alert_id: int, req: AlertStatusUpdate, db: Session = Depends(get_db)):
    alert = engine.set_alert_status(db, alert_id, req.status)
    if alert is None:
        raise HTTPException(404, "alert not found or invalid status")
    return {"id": alert.id, "status": alert.status}


@app.get("/api/notifications")
def get_notifications(role: str | None = None, event_id: int | None = None, db: Session = Depends(get_db)):
    return engine.list_notifications(db, role=role, event_id=event_id)


@app.post("/api/notifications/{notification_id}/read")
def read_notification(notification_id: int, db: Session = Depends(get_db)):
    n = engine.mark_notification_read(db, notification_id)
    if n is None:
        raise HTTPException(404, "notification not found")
    return {"id": n.id, "is_read": n.is_read}


# --- hotel recommendation engine (Section 16) -------------------------------

@app.get("/api/hotels/recommendations")
def hotel_recommendations(db: Session = Depends(get_db)):
    return engine.hotel_recommendations(db)


# --- live nearby hotel/transport discovery (OpenStreetMap / Overpass) ------
# Map data only — public OSM tags never carry a hotel's private live room
# inventory, so live rooms are only ever shown for a connected KAIRO/partner
# feed (HotelInventorySnapshot), never invented from map data.

def _price_band_from_tags(tags: dict):
    raw = str(tags.get("price_range") or tags.get("price") or "").strip().lower()
    if raw in {"$", "1", "budget", "cheap", "low"}:
        return "budget", "mapped"
    if raw in {"$$", "2", "mid", "medium"}:
        return "mid", "mapped"
    if raw in {"$$$", "3", "premium", "high", "expensive"}:
        return "premium", "mapped"
    stars = None
    try:
        stars = float(str(tags.get("stars", "")).replace("★", "").strip())
    except Exception:
        pass
    if stars is not None:
        if stars <= 2:
            return "budget", "estimated_from_stars"
        if stars <= 4:
            return "mid", "estimated_from_stars"
        return "premium", "estimated_from_stars"
    return "unknown", "unavailable"


def _overpass_hotels(lat: float, lng: float, radius_m: int):
    q = f"""
    [out:json][timeout:25];
    (
      nwr["tourism"="hotel"](around:{int(radius_m)},{lat},{lng});
      nwr["tourism"="motel"](around:{int(radius_m)},{lat},{lng});
      nwr["tourism"="guest_house"](around:{int(radius_m)},{lat},{lng});
      nwr["tourism"="hostel"](around:{int(radius_m)},{lat},{lng});
    );
    out center tags;
    """
    urls = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.private.coffee/api/interpreter",
    ]
    headers = {"User-Agent": "KAIRO-PS8-HotelDiscovery/2.0 (hackathon prototype)"}
    last_error = None
    for url in urls:
        try:
            req = urllib.request.Request(url, data=q.encode("utf-8"), headers={**headers, "Content-Type": "application/x-www-form-urlencoded"}, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8")).get("elements", [])
        except Exception as exc:
            last_error = exc
    raise HTTPException(502, f"hotel discovery source unavailable: {last_error}")


def _address_fields(item: dict) -> dict:
    addr = item.get("address", {}) or {}
    return {
        "display_name": item.get("display_name", ""),
        "venue_name": addr.get("amenity") or addr.get("building") or addr.get("tourism") or addr.get("leisure") or "",
        "city": addr.get("city") or addr.get("town") or addr.get("village") or addr.get("municipality") or addr.get("county") or "",
        "state": addr.get("state") or "",
    }


@app.post("/api/hotels/geocode")
def hotel_geocode(req: GeocodeRequest):
    query = req.query.strip()
    if len(query) < 2:
        raise HTTPException(400, "Enter a valid event location")
    try:
        qs = urllib.parse.urlencode({"q": query, "format": "jsonv2", "limit": 1, "addressdetails": 1})
        geo_req = urllib.request.Request(
            "https://nominatim.openstreetmap.org/search?" + qs,
            headers={"User-Agent": "KAIRO-PS8-HotelDiscovery/2.0"},
        )
        with urllib.request.urlopen(geo_req, timeout=15) as resp:
            items = json.loads(resp.read().decode("utf-8"))
        if not items:
            raise HTTPException(404, "Location not found")
        item = items[0]
        return {
            "lat": float(item["lat"]),
            "lng": float(item["lon"]),
            **_address_fields(item),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"location search unavailable: {exc}")


@app.get("/api/geocode/reverse")
def geocode_reverse(lat: float, lng: float):
    """Address details for a map-click point (no search query) — lets Create
    Event's venue picker fill venue name/city/region from wherever the
    operator clicks, not just from a text search."""
    try:
        qs = urllib.parse.urlencode({"lat": lat, "lon": lng, "format": "jsonv2", "addressdetails": 1})
        geo_req = urllib.request.Request(
            "https://nominatim.openstreetmap.org/reverse?" + qs,
            headers={"User-Agent": "KAIRO-PS8-HotelDiscovery/2.0"},
        )
        with urllib.request.urlopen(geo_req, timeout=15) as resp:
            item = json.loads(resp.read().decode("utf-8"))
        if not item or "address" not in item:
            raise HTTPException(404, "No address found for this point")
        return _address_fields(item)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"reverse geocoding unavailable: {exc}")


@app.post("/api/hotels/discover-nearby")
def discover_nearby_hotels(req: HotelDiscoveryRequest):
    if not (-90 <= req.lat <= 90 and -180 <= req.lng <= 180):
        raise HTTPException(400, "Invalid latitude/longitude")
    radius = int(req.radius_m)
    raw = _overpass_hotels(req.lat, req.lng, radius)
    hotels = []
    seen = set()
    seen_named = set()
    for el in raw:
        tags = el.get("tags") or {}
        center = el.get("center") or {}
        lat = el.get("lat", center.get("lat"))
        lng = el.get("lon", center.get("lon"))
        if lat is None or lng is None:
            continue
        key = (el.get("type"), el.get("id"))
        if key in seen:
            continue
        seen.add(key)
        d = engine._haversine_km(req.lat, req.lng, float(lat), float(lng))
        if d * 1000 > radius + 1:
            continue
        name = tags.get("name") or tags.get("official_name")
        if not name:
            continue
        kind = tags.get("tourism", "hotel").replace("_", " ").title()
        stars = tags.get("stars")
        try:
            stars_num = float(stars)
        except Exception:
            stars_num = None
        price_band, price_source = _price_band_from_tags(tags)
        norm_name = " ".join(name.lower().split())
        if norm_name in seen_named:
            continue
        seen_named.add(norm_name)
        hotels.append({
            "id": f"{el.get('type','nwr')}-{el.get('id')}",
            "name": name,
            "type": kind,
            "lat": round(float(lat), 6),
            "lng": round(float(lng), 6),
            "distance_km": round(d, 2),
            "stars": stars_num,
            "rating": stars_num,
            "rating_type": "hotel_star_class" if stars_num is not None else "unavailable",
            "price_band": price_band,
            "price_source": price_source,
            "address": tags.get("addr:full") or " ".join(filter(None, [tags.get("addr:housenumber"), tags.get("addr:street"), tags.get("addr:city")])) or "Address not mapped",
            "phone": tags.get("phone") or tags.get("contact:phone"),
            "website": tags.get("website") or tags.get("contact:website"),
            "live_inventory": None,
            "inventory_source": "not_connected",
        })
    hotels.sort(key=lambda item: (item["distance_km"], item["name"].lower()))
    return {
        "center": {"lat": req.lat, "lng": req.lng},
        "radius_m": radius,
        "count": len(hotels),
        "hotels": hotels,
        "source": "OpenStreetMap / Overpass",
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        "note": "Hotels are rediscovered from the current event coordinates on every request. Room availability is shown only for connected hotel/PMS/partner feeds; map data is not used to invent live inventory.",
    }


def _overpass_transport(lat: float, lng: float, radius_m: int):
    q = f"""
    [out:json][timeout:25];
    (
      nwr["highway"="bus_stop"](around:{int(radius_m)},{lat},{lng});
      nwr["public_transport"="platform"](around:{int(radius_m)},{lat},{lng});
      nwr["public_transport"="station"](around:{int(radius_m)},{lat},{lng});
      nwr["railway"="station"](around:{int(radius_m)},{lat},{lng});
      nwr["railway"="halt"](around:{int(radius_m)},{lat},{lng});
      nwr["railway"="tram_stop"](around:{int(radius_m)},{lat},{lng});
      nwr["aeroway"="aerodrome"](around:{int(radius_m)},{lat},{lng});
      nwr["amenity"="taxi"](around:{int(radius_m)},{lat},{lng});
    );
    out center tags;
    """
    urls = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.private.coffee/api/interpreter",
    ]
    headers = {"User-Agent": "KAIRO-PS8-TransportDiscovery/1.0 (hackathon prototype)"}
    last_error = None
    for url in urls:
        try:
            request = urllib.request.Request(
                url, data=q.encode("utf-8"),
                headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8")).get("elements", [])
        except Exception as exc:
            last_error = exc
    raise HTTPException(502, f"transport discovery source unavailable: {last_error}")


@app.post("/api/transport/discover-nearby")
def discover_nearby_transport(req: TransportDiscoveryRequest):
    if not (-90 <= req.lat <= 90 and -180 <= req.lng <= 180):
        raise HTTPException(400, "Invalid latitude/longitude")
    raw = _overpass_transport(req.lat, req.lng, int(req.radius_m))
    out = []
    seen = set()
    for el in raw:
        tags = el.get("tags") or {}
        center = el.get("center") or {}
        lat = el.get("lat", center.get("lat"))
        lng = el.get("lon", center.get("lon"))
        if lat is None or lng is None:
            continue
        name = tags.get("name") or tags.get("official_name")
        if not name:
            continue
        key = (el.get("type"), el.get("id"))
        if key in seen:
            continue
        seen.add(key)
        d = engine._haversine_km(req.lat, req.lng, float(lat), float(lng))
        if d * 1000 > req.radius_m + 1:
            continue
        if tags.get("highway") == "bus_stop":
            mode = "Bus stop"
        elif tags.get("railway") in {"station", "halt"}:
            mode = "Rail station"
        elif tags.get("railway") == "tram_stop":
            mode = "Tram stop"
        elif tags.get("aeroway") == "aerodrome":
            mode = "Airport"
        elif tags.get("amenity") == "taxi":
            mode = "Taxi stand"
        elif tags.get("public_transport") == "station":
            mode = "Transit station"
        else:
            mode = "Transit platform"
        out.append({
            "id": f"{el.get('type','nwr')}-{el.get('id')}",
            "name": name,
            "type": mode,
            "lat": round(float(lat), 6),
            "lng": round(float(lng), 6),
            "distance_km": round(d, 2),
        })
    out.sort(key=lambda x: (x["distance_km"], x["name"].lower()))
    return {
        "center": {"lat": req.lat, "lng": req.lng},
        "radius_m": int(req.radius_m),
        "count": len(out),
        "items": out,
        "source": "OpenStreetMap / Overpass",
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        "note": "Nearby transport points are mapped locations; live departure/seat availability requires an operator feed.",
    }


# --- hotel partner portal / PMS webhook (live inventory) --------------------

def _hotel_snapshot(db: Session, hotel):
    snap = db.query(models.HotelInventorySnapshot).filter_by(hotel_id=hotel.id).first()
    occupied = snap.occupied_rooms if snap else hotel.current_count
    available = snap.available_rooms if snap else max(hotel.capacity - hotel.current_count, 0)
    return occupied, available, (snap.updated_at.isoformat() if snap else None), (snap.source if snap else "seed/demo")


@app.get("/api/partner/hotels")
def partner_hotels(db: Session = Depends(get_db)):
    hotels = db.query(models.Zone).filter(models.Zone.type == "hotel").order_by(models.Zone.name).all()
    out = []
    for h in hotels:
        occupied, available, updated_at, source = _hotel_snapshot(db, h)
        out.append({
            "id": h.id, "name": h.name, "capacity": h.capacity,
            "occupied_rooms": occupied, "available_rooms": available,
            "occupancy_pct": round((occupied / h.capacity) * 100, 1) if h.capacity else 0,
            "lat": h.lat, "lng": h.lng, "last_updated": updated_at, "source": source,
        })
    return out


@app.get("/api/partner/hotels/{hotel_id}")
def partner_hotel(hotel_id: int, db: Session = Depends(get_db)):
    h = db.get(models.Zone, hotel_id)
    if not h or h.type != "hotel":
        raise HTTPException(404, "hotel not found")
    occupied, available, updated_at, source = _hotel_snapshot(db, h)
    return {
        "id": h.id, "name": h.name, "capacity": h.capacity,
        "occupied_rooms": occupied, "available_rooms": available,
        "occupancy_pct": round((occupied / h.capacity) * 100, 1) if h.capacity else 0,
        "last_updated": updated_at, "source": source,
    }


def _update_hotel_rooms(db: Session, hotel_id: int, occupied_rooms: int, source: str):
    h = db.get(models.Zone, hotel_id)
    if not h or h.type != "hotel":
        raise HTTPException(404, "hotel not found")
    if occupied_rooms < 0 or occupied_rooms > h.capacity:
        raise HTTPException(400, f"occupied_rooms must be between 0 and {h.capacity}")
    h.last_count = h.current_count
    h.prev_delta = occupied_rooms - h.current_count
    h.current_count = occupied_rooms
    h.peak_count = max(h.peak_count or 0, occupied_rooms)
    available = max(h.capacity - occupied_rooms, 0)
    now = datetime.now(timezone.utc)
    snap = db.query(models.HotelInventorySnapshot).filter_by(hotel_id=h.id).first()
    if not snap:
        snap = models.HotelInventorySnapshot(hotel_id=h.id)
        db.add(snap)
    snap.occupied_rooms = occupied_rooms
    snap.available_rooms = available
    snap.source = source
    snap.updated_at = now
    db.commit()
    return {
        "hotel_id": h.id, "hotel": h.name,
        "occupied_rooms": occupied_rooms, "available_rooms": available,
        "occupancy_pct": round((occupied_rooms / h.capacity) * 100, 1) if h.capacity else 0,
        "source": source, "last_updated": now.isoformat(), "status": "updated",
    }


@app.patch("/api/partner/hotels/{hotel_id}/availability")
def partner_update_hotel(hotel_id: int, req: HotelAvailabilityUpdate, db: Session = Depends(get_db)):
    return _update_hotel_rooms(db, hotel_id, req.occupied_rooms, "hotel_partner_portal")


@app.post("/api/integrations/hotel/webhook")
def hotel_webhook(req: HotelWebhookUpdate, db: Session = Depends(get_db)):
    return _update_hotel_rooms(db, req.hotel_id, req.occupied_rooms, req.source)


# --- Service Provider portal (nearby events, opt-in, prediction, report) ----

@app.get("/api/partner/my-hotel")
def partner_my_hotel(email: str, db: Session = Depends(get_db)):
    """Which hotel Zone this Service Provider account manages, if claimed yet."""
    account = (
        db.query(models.UserAccount)
        .filter(models.UserAccount.email == email.strip().lower(), models.UserAccount.role == "Service Provider")
        .first()
    )
    if not account or not account.managed_zone_id:
        return {"hotel_id": None}
    h = db.get(models.Zone, account.managed_zone_id)
    if not h:
        return {"hotel_id": None}
    return {"hotel_id": h.id, "hotel_name": h.name}


@app.post("/api/partner/claim-hotel")
def partner_claim_hotel(req: ClaimHotelRequest, db: Session = Depends(get_db)):
    email = req.email.strip().lower()
    hotel = db.get(models.Zone, req.hotel_id)
    if not hotel or hotel.type != "hotel":
        raise HTTPException(404, "hotel not found")
    account = (
        db.query(models.UserAccount)
        .filter(models.UserAccount.email == email, models.UserAccount.role == "Service Provider")
        .first()
    )
    if not account:
        account = models.UserAccount(email=email, role="Service Provider")
        db.add(account)
    account.managed_zone_id = hotel.id
    db.commit()
    return {"hotel_id": hotel.id, "hotel_name": hotel.name}


@app.get("/api/partner/hotels/{hotel_id}/nearby-events")
def partner_nearby_events(hotel_id: int, db: Session = Depends(get_db)):
    hotel = db.get(models.Zone, hotel_id)
    if not hotel or hotel.type != "hotel":
        raise HTTPException(404, "hotel not found")
    return {"hotel_id": hotel_id, "events": engine.nearby_events_for_hotel(db, hotel)}


@app.post("/api/partner/hotels/{hotel_id}/opt-in")
def partner_hotel_opt_in(hotel_id: int, req: HotelOptInRequest, db: Session = Depends(get_db)):
    hotel = db.get(models.Zone, hotel_id)
    if not hotel or hotel.type != "hotel":
        raise HTTPException(404, "hotel not found")
    if not db.get(models.Event, req.event_id):
        raise HTTPException(404, "event not found")
    result = engine.set_hotel_event_interest(db, hotel_id, req.event_id, req.status)
    if result is None:
        raise HTTPException(400, "status must be opted_in or declined")
    return result


@app.get("/api/partner/hotels/{hotel_id}/prediction")
def partner_hotel_prediction(hotel_id: int, db: Session = Depends(get_db)):
    hotel = db.get(models.Zone, hotel_id)
    if not hotel or hotel.type != "hotel":
        raise HTTPException(404, "hotel not found")
    return engine.hotel_situation_prediction(db, hotel)


@app.get("/api/partner/hotels/{hotel_id}/report/{event_id}")
def partner_hotel_report(hotel_id: int, event_id: int, db: Session = Depends(get_db)):
    hotel = db.get(models.Zone, hotel_id)
    if not hotel or hotel.type != "hotel":
        raise HTTPException(404, "hotel not found")
    event = db.get(models.Event, event_id)
    if not event:
        raise HTTPException(404, "event not found")
    return engine.hotel_event_report(db, hotel, event)


@app.get("/api/attendee/hotels")
def attendee_hotels(event_id: int | None = None, db: Session = Depends(get_db)):
    """Live connected hotel inventory for the attendee side, scoped to one
    event (pass the attendee's booked event_id, e.g. from /api/my-plan) so a
    hotel booked/mapped for a different event is never shown. Falls back to
    the currently-live event when no event_id is given — never "every hotel
    across every event," which was the previous (buggy) behavior.
    Only hotels with KAIRO/partner inventory are shown as live; no room count is invented from map data."""
    event = db.get(models.Event, event_id) if event_id is not None else engine.get_live_event(db)
    if not event:
        return {"hotels": [], "note": "No event context available."}
    hotels = (
        db.query(models.Zone)
        .filter(models.Zone.type == "hotel", models.Zone.event_id == event.id)
        .order_by(models.Zone.name)
        .all()
    )
    out = []
    venue = db.query(models.Zone).filter(models.Zone.type == "arena", models.Zone.event_id == event.id).first()
    for h in hotels:
        occupied, available, updated_at, source = _hotel_snapshot(db, h)
        distance = engine._haversine_km(venue.lat, venue.lng, h.lat, h.lng) if venue else None
        out.append({
            "id": h.id, "name": h.name, "available_rooms": available,
            "occupied_rooms": occupied, "capacity": h.capacity,
            "occupancy_pct": round((occupied / h.capacity) * 100, 1) if h.capacity else 0,
            "distance_km": round(distance, 1) if distance is not None else None,
            "price_tier": h.price_tier, "live": updated_at is not None, "source": source,
            "last_updated": updated_at,
        })
    out.sort(key=lambda x: (x["available_rooms"] <= 0, x["distance_km"] is None, x["distance_km"] or 999))
    return {
        "event_id": event.id,
        "hotels": out,
        "note": "Live room counts come from connected KAIRO partner inventory for this event. Map-only hotels are not assigned room counts.",
    }


@app.get("/api/attendee/transport")
def attendee_transport(event_id: int | None = None, db: Session = Depends(get_db)):
    """Attendee-facing transport summary. `local` (buses/trains) and `flights`
    are genuinely city/region-level feeds (not tied to any one event — labeled
    as such below) but region-gated by the attendee's own event (falling back
    to the currently-live event) rather than "whichever event happens to be
    live" independent of what this attendee booked; `demand` is the per-event
    transport-zone prediction, scoped the same way."""
    event = db.get(models.Event, event_id) if event_id is not None else engine.get_live_event(db)
    local = engine.local_transit_feed(db, event=event)
    flights = engine.transport_hub_arrivals(db, event=event)
    demand = engine.transport_demand_prediction(db, event_id=event_id)
    crowd = engine.transport_crowd_advisory(db, event)
    return {
        "local": local, "flights": flights, "demand": demand, "crowd": crowd,
        "local_scope": "city/region-level — not specific to any one event",
    }


# --- Attendee POI directory: food / essentials / emergency / venue map ------
# event_id falls back to the currently-live event when omitted, same
# convention as /api/attendee/hotels and /api/attendee/transport above.

@app.get("/api/attendee/restaurants")
def attendee_restaurants(
    event_id: int | None = None, cuisine: str | None = None, max_price: int | None = None,
    open_now: bool = False, sort: str | None = None, db: Session = Depends(get_db),
):
    return engine.list_restaurants(db, event_id=event_id, cuisine=cuisine, max_price=max_price, open_now=open_now, sort=sort)


@app.get("/api/attendee/services")
def attendee_services(
    event_id: int | None = None, category: str | None = None, open_now: bool = False,
    sort: str | None = None, db: Session = Depends(get_db),
):
    return engine.list_essential_services(db, event_id=event_id, category=category, open_now=open_now, sort=sort)


@app.get("/api/attendee/emergency")
def attendee_emergency(event_id: int | None = None, db: Session = Depends(get_db)):
    return engine.attendee_emergency_directory(db, event_id=event_id)


@app.get("/api/attendee/venue-pois")
def attendee_venue_pois(event_id: int | None = None, db: Session = Depends(get_db)):
    return engine.venue_pois(db, event_id=event_id)


# --- transport demand prediction (Section 14) -------------------------------

@app.get("/api/transport/demand-prediction")
def transport_demand_prediction(db: Session = Depends(get_db)):
    return engine.transport_demand_prediction(db)


@app.get("/api/rideshare/opportunities")
def rideshare_opportunities(db: Session = Depends(get_db)):
    """Concept feed for a rideshare partner's own driver-supply tooling — see
    engine.rideshare_partner_opportunities's docstring for the honest framing."""
    return engine.rideshare_partner_opportunities(db)


# --- dynamic staff allocation, generalized (Section 13) ---------------------

@app.get("/api/staff/suggestions")
def staff_suggestions(db: Session = Depends(get_db)):
    return engine.staff_allocation_suggestions(db)


# --- emergency mode (Section 28) --------------------------------------------

@app.post("/api/emergency/trigger")
def emergency_trigger(req: EmergencyTrigger, db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    if role is not None and role not in FULL_ACCESS_ROLES:
        raise HTTPException(403, f"role '{role}' cannot declare an emergency")
    result = engine.trigger_emergency(db, req.zone_id, req.message)
    if result is None:
        raise HTTPException(404, "zone not found")
    return result


@app.post("/api/emergency/clear")
def emergency_clear(db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    if role is not None and role not in FULL_ACCESS_ROLES:
        raise HTTPException(403, f"role '{role}' cannot clear an emergency")
    return engine.clear_emergency(db)


@app.get("/api/emergency/status")
def emergency_status_endpoint(db: Session = Depends(get_db)):
    return engine.emergency_status(db)


# --- post-event analytics / historical learning (Sections 31-32) ----------

@app.get("/api/analytics/post-event")
def post_event_analytics(event_id: int | None = None, db: Session = Depends(get_db)):
    return engine.event_analytics(db, event_id=event_id)


# --- Government/Admin: city-level intelligence (read-only) ------------------

@app.get("/api/government/overview")
def government_overview(db: Session = Depends(get_db)):
    return engine.government_overview(db)


@app.get("/api/government/events")
def government_events(db: Session = Depends(get_db)):
    return engine.government_events(db)


@app.get("/api/government/events/{event_id}/impact")
def government_event_impact(event_id: int, db: Session = Depends(get_db)):
    result = engine.government_event_impact(db, event_id)
    if result is None:
        raise HTTPException(404, "event not found")
    return result


@app.get("/api/government/risk")
def government_risk(db: Session = Depends(get_db)):
    return engine.government_risk(db)


@app.get("/api/government/trends")
def government_trends(db: Session = Depends(get_db)):
    return engine.government_trends(db)


@app.get("/api/government/emergency-services")
def government_emergency_services(db: Session = Depends(get_db)):
    return engine.government_emergency_services(db)


# --- multi-event attendee registration (Sections 4-5) -----------------------

@app.post("/api/attendee/register-event")
def attendee_register_event(req: AttendeeEmail, db: Session = Depends(get_db)):
    result = engine.register_attendee_for_event(db, req.email.strip().lower())
    if result is None:
        raise HTTPException(404, "no event configured yet")
    return result


@app.get("/api/attendee/my-events")
def attendee_my_events(email: str, db: Session = Depends(get_db)):
    return engine.list_my_events(db, email.strip().lower())


@app.post("/api/attendee/current-event")
def attendee_set_current_event(req: SetCurrentEvent, db: Session = Depends(get_db)):
    result = engine.set_current_event(db, req.email.strip().lower(), req.event_attendee_id)
    if result is None:
        raise HTTPException(404, "registration not found")
    return result


@app.get("/api/attendee/current-event-context")
def attendee_current_event_context(email: str, db: Session = Depends(get_db)):
    return engine.current_event_context(db, email.strip().lower())


frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8001)
