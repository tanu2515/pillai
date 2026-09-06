from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from . import engine, models
from .regions import INDIA_REGIONS
from .database import Base, SessionLocal, engine as db_engine, get_db
from .seed import seed_if_empty

Base.metadata.create_all(bind=db_engine)

# create_all only creates missing tables, not missing columns on a table that
# already exists — needed once a real event's zones already live in the DB
# (no Alembic here, see database.py), so a model field added after the fact
# is patched in by hand, guarded so it's a no-op once the column is present.
with db_engine.begin() as conn:
    zone_columns = {c["name"] for c in inspect(db_engine).get_columns("zones")}
    if "is_accessible" not in zone_columns:
        conn.execute(text("ALTER TABLE zones ADD COLUMN is_accessible BOOLEAN NOT NULL DEFAULT FALSE"))

with SessionLocal() as db:
    seed_if_empty(db)

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


class EventUpdate(BaseModel):
    name: str
    expected_attendance: int
    safe_capacity: int


class EventCreate(BaseModel):
    name: str
    region: str
    expected_attendance: int
    safe_capacity: int


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


class ZoneLocationUpdate(BaseModel):
    lat: float
    lng: float


class GateSetupUpdate(BaseModel):
    capacity: int
    staff_assigned: int | None = None
    is_accessible: bool | None = None


class HotelCreate(BaseModel):
    name: str
    capacity: int
    lat: float
    lng: float
    price_tier: int | None = None
    contact: str | None = None
    amenities: str | None = None


class HotelUpdate(BaseModel):
    capacity: int | None = None
    occupied_rooms: int | None = None
    price_tier: int | None = None
    contact: str | None = None
    amenities: str | None = None
    manual_recommended: bool | None = None


class TransportZoneCreate(BaseModel):
    name: str
    capacity: int
    lat: float
    lng: float
    type: str = "corridor"
    contact: str | None = None


class TransportZoneUpdate(BaseModel):
    capacity: int | None = None
    current_count: int | None = None
    contact: str | None = None


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


class BookTierRequest(BaseModel):
    name: str
    email: str | None = None
    seat_id: int | None = None
    quantity: int = 1
    hotel_zone_id: int | None = None
    wants_transport: bool = False


# --- role enforcement (backward-compatible) ---------------------------------
# Reads an optional X-User-Role header. Absent header = full access (every
# client that predates this change keeps working exactly as before); a
# client that *does* send a role is held to that role's domain, same scoping
# command-center.html already does client-side via ROLE_CONFIG.
DOMAIN_ROLES = {
    "venue": "Venue Manager",
    "transport": "Transport Operator",
    "hospitality": "Hospitality Operator",
}
FULL_ACCESS_ROLES = {"Administrator", "Event Command Operator"}


def get_role(x_user_role: str | None = Header(default=None)):
    return x_user_role


def require_domain_access(role: str | None, domain: str | None):
    if role is None or role in FULL_ACCESS_ROLES:
        return
    if domain and DOMAIN_ROLES.get(domain) == role:
        return
    raise HTTPException(403, f"role '{role}' cannot act on domain '{domain}'")


def require_admin(role: str | None):
    if role is not None and role != "Administrator":
        raise HTTPException(403, "Administrator role required")


@app.get("/api/event")
def get_event(db: Session = Depends(get_db)):
    event = engine.get_live_event(db)
    if not event:
        return {"configured": False}
    return {
        "configured": True, "id": event.id, "name": event.name, "region": event.region,
        "expected_attendance": event.expected_attendance,
        "safe_capacity": event.safe_capacity, "status": event.status,
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
    event = engine.create_event_listing(
        db, req.name, req.description, req.event_date, req.region,
        req.expected_attendance, req.safe_capacity, [t.model_dump() for t in req.tiers],
        event_time=req.event_time, category=req.category, city=req.city, venue_name=req.venue_name,
        venue_address=req.venue_address, banner_emoji=req.banner_emoji, is_featured=req.is_featured,
    )
    return {"id": event.id, "name": event.name}


@app.post("/api/events/{event_id}/tiers/{tier_id}/book")
def book_tier(event_id: int, tier_id: int, req: BookTierRequest, db: Session = Depends(get_db)):
    return engine.book_tier(
        db, event_id, tier_id, req.name, req.seat_id, req.email, req.quantity, req.hotel_zone_id, req.wants_transport,
    )


@app.get("/api/my-bookings")
def my_bookings(email: str, db: Session = Depends(get_db)):
    return engine.list_my_bookings(db, email.strip().lower())


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
def delete_zone(zone_id: int, db: Session = Depends(get_db)):
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
    zone = engine.update_gate_setup(db, zone_id, req.capacity, req.staff_assigned, req.is_accessible)
    if zone is None:
        raise HTTPException(404, "gate not found")
    return {"id": zone.id}


@app.get("/api/evacuation-routes")
def get_evacuation_routes(
    accessible_only: bool = False, lat: float | None = None, lng: float | None = None, db: Session = Depends(get_db),
):
    return engine.evacuation_routes(db, accessible_only=accessible_only, lat=lat, lng=lng)


@app.post("/api/attendee/accessibility-request")
def post_accessibility_request(req: AccessibilityRequest, db: Session = Depends(get_db)):
    return engine.request_accessibility_assistance(
        db, email=req.email, note=req.note, lat=req.lat, lng=req.lng, zone_name=req.zone_name,
    )


@app.post("/api/event-setup/hotels")
def post_hotel(req: HotelCreate, db: Session = Depends(get_db)):
    zone = engine.create_hotel(db, req.name, req.capacity, req.lat, req.lng, req.price_tier, req.contact, req.amenities)
    if zone is None:
        raise HTTPException(404, "no event configured yet")
    return {"id": zone.id}


@app.patch("/api/event-setup/hotels/{zone_id}")
def patch_hotel(zone_id: int, req: HotelUpdate, db: Session = Depends(get_db)):
    zone = engine.update_hotel(
        db, zone_id, req.capacity, req.occupied_rooms, req.price_tier, req.contact, req.amenities, req.manual_recommended,
    )
    if zone is None:
        raise HTTPException(404, "hotel not found")
    return {"id": zone.id}


@app.delete("/api/event-setup/hotels/{zone_id}")
def delete_hotel(zone_id: int, db: Session = Depends(get_db)):
    result = engine.delete_zone(db, zone_id)
    if not result["ok"]:
        raise HTTPException(409 if "error" in result else 404, result.get("error", "not found"))
    return {"deleted": zone_id}


@app.post("/api/event-setup/transport")
def post_transport_zone(req: TransportZoneCreate, db: Session = Depends(get_db)):
    zone = engine.create_transport_zone(db, req.name, req.capacity, req.lat, req.lng, req.type, req.contact)
    if zone is None:
        raise HTTPException(404, "no event configured yet")
    return {"id": zone.id}


@app.patch("/api/event-setup/transport/{zone_id}")
def patch_transport_zone(zone_id: int, req: TransportZoneUpdate, db: Session = Depends(get_db)):
    zone = engine.update_transport_zone(db, zone_id, req.capacity, req.current_count, req.contact)
    if zone is None:
        raise HTTPException(404, "transport zone not found")
    return {"id": zone.id}


@app.delete("/api/event-setup/transport/{zone_id}")
def delete_transport_zone(zone_id: int, db: Session = Depends(get_db)):
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


@app.get("/api/transport/flights")
def transport_flights(db: Session = Depends(get_db)):
    return engine.transport_hub_arrivals(db)


@app.get("/api/transport/local")
def transport_local(db: Session = Depends(get_db)):
    return engine.local_transit_feed(db)


# --- Administrator: event configuration only, no live-ops access ----------

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
    event = engine.create_event(db, req.name.strip(), req.region, req.expected_attendance, req.safe_capacity)
    return {"id": event.id, "name": event.name, "region": event.region}


@app.delete("/api/admin/event")
def admin_delete_event(db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    require_admin(role)
    engine.delete_event(db)
    return {"configured": False}


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


# --- Alerts + notifications (Sections 22-23, 27) ----------------------------

@app.get("/api/alerts")
def get_alerts(status: str | None = None, db: Session = Depends(get_db)):
    return engine.list_alerts(db, status=status)


@app.patch("/api/alerts/{alert_id}")
def patch_alert(alert_id: int, req: AlertStatusUpdate, db: Session = Depends(get_db)):
    alert = engine.set_alert_status(db, alert_id, req.status)
    if alert is None:
        raise HTTPException(404, "alert not found or invalid status")
    return {"id": alert.id, "status": alert.status}


@app.get("/api/notifications")
def get_notifications(role: str | None = None, db: Session = Depends(get_db)):
    return engine.list_notifications(db, role=role)


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


# --- transport demand prediction (Section 14) -------------------------------

@app.get("/api/transport/demand-prediction")
def transport_demand_prediction(db: Session = Depends(get_db)):
    return engine.transport_demand_prediction(db)


# --- dynamic staff allocation, generalized (Section 13) ---------------------

@app.get("/api/staff/suggestions")
def staff_suggestions(db: Session = Depends(get_db)):
    return engine.staff_allocation_suggestions(db)


# --- emergency mode (Section 28) --------------------------------------------

@app.post("/api/emergency/trigger")
def emergency_trigger(req: EmergencyTrigger, db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    if role is not None and role not in FULL_ACCESS_ROLES | {"Venue Manager"}:
        raise HTTPException(403, f"role '{role}' cannot declare an emergency")
    result = engine.trigger_emergency(db, req.zone_id, req.message)
    if result is None:
        raise HTTPException(404, "zone not found")
    return result


@app.post("/api/emergency/clear")
def emergency_clear(db: Session = Depends(get_db), role: str | None = Depends(get_role)):
    if role is not None and role not in FULL_ACCESS_ROLES | {"Venue Manager"}:
        raise HTTPException(403, f"role '{role}' cannot clear an emergency")
    return engine.clear_emergency(db)


@app.get("/api/emergency/status")
def emergency_status_endpoint(db: Session = Depends(get_db)):
    return engine.emergency_status(db)


# --- post-event analytics / historical learning (Sections 31-32) ----------

@app.get("/api/analytics/post-event")
def post_event_analytics(db: Session = Depends(get_db)):
    return engine.event_analytics(db)


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
