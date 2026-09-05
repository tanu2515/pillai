from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import engine, models
from .regions import INDIA_REGIONS
from .database import Base, SessionLocal, engine as db_engine, get_db
from .seed import seed_if_empty

Base.metadata.create_all(bind=db_engine)
with SessionLocal() as db:
    seed_if_empty(db)

app = FastAPI(title="KAIRO — PS-8 Mega-Event Orchestration")

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


@app.get("/api/event")
def get_event(db: Session = Depends(get_db)):
    event = db.query(models.Event).first()
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
def admin_apply_region(req: RegionUpdate, db: Session = Depends(get_db)):
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
def create_scenario(req: ScenarioUpsert, db: Session = Depends(get_db)):
    scenario = engine.create_scenario(db, req.name, req.description, req.duration_ticks, req.effects)
    return {"id": scenario.id}


@app.patch("/api/admin/scenarios/{scenario_id}")
def update_scenario(scenario_id: int, req: ScenarioUpsert, db: Session = Depends(get_db)):
    scenario = engine.update_scenario(db, scenario_id, req.name, req.description, req.duration_ticks, req.effects)
    if scenario is None:
        raise HTTPException(404, "scenario not found")
    return {"id": scenario.id}


@app.delete("/api/admin/scenarios/{scenario_id}")
def delete_scenario(scenario_id: int, db: Session = Depends(get_db)):
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
        db, req.redirect_count, req.open_gate3, req.add_buses, req.move_staff
    )


@app.get("/api/recommendations")
def recommendations(db: Session = Depends(get_db)):
    return engine.generate_recommendations(db)


@app.post("/api/action-plans/approve")
def approve(req: ApproveRequest, db: Session = Depends(get_db)):
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


@app.get("/api/risks/causal-chain/{zone_id}")
def get_zone_causal_chain(zone_id: int, db: Session = Depends(get_db)):
    return {"chain": engine.zone_causal_chain(db, zone_id)}


@app.post("/api/zones/{zone_id}/ack")
def ack_zone(zone_id: int, req: AckRequest, db: Session = Depends(get_db)):
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


# --- Administrator: event configuration only, no live-ops access ----------

@app.patch("/api/admin/event")
def admin_update_event(req: EventUpdate, db: Session = Depends(get_db)):
    event = db.query(models.Event).first()
    if not event:
        raise HTTPException(404, "no event configured yet")
    event.name = req.name.strip() or event.name
    event.expected_attendance = req.expected_attendance
    event.safe_capacity = req.safe_capacity
    db.commit()
    return {"name": event.name, "expected_attendance": event.expected_attendance, "safe_capacity": event.safe_capacity}


@app.post("/api/admin/event")
def admin_create_event(req: EventCreate, db: Session = Depends(get_db)):
    if req.region not in INDIA_REGIONS:
        raise HTTPException(400, "unknown state/UT")
    if not req.name.strip():
        raise HTTPException(400, "name is required")
    event = engine.create_event(db, req.name.strip(), req.region, req.expected_attendance, req.safe_capacity)
    return {"id": event.id, "name": event.name, "region": event.region}


@app.delete("/api/admin/event")
def admin_delete_event(db: Session = Depends(get_db)):
    engine.delete_event(db)
    return {"configured": False}


@app.patch("/api/admin/zones/{zone_id}")
def admin_update_zone_capacity(zone_id: int, req: ZoneCapacityUpdate, db: Session = Depends(get_db)):
    zone = db.get(models.Zone, zone_id)
    if not zone:
        raise HTTPException(404, "zone not found")
    if req.capacity <= 0:
        raise HTTPException(400, "capacity must be positive")
    zone.capacity = req.capacity
    db.commit()
    return {"id": zone.id, "name": zone.name, "capacity": zone.capacity}


@app.get("/api/admin/raw-tables")
def admin_raw_tables(db: Session = Depends(get_db)):
    """Every row of every table, straight from the DB — for the raw table
    viewer page. Debug/inspection only, not used by any real screen."""
    tables = {
        "events": models.Event,
        "zones": models.Zone,
        "resources": models.Resource,
        "visitor_profiles": models.VisitorProfile,
        "user_accounts": models.UserAccount,
        "scenarios": models.Scenario,
        "sim_state": models.SimState,
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
def admin_list_users(db: Session = Depends(get_db)):
    users = db.query(models.UserAccount).order_by(models.UserAccount.email).all()
    return [{"id": u.id, "email": u.email, "role": u.role} for u in users]


@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: int, db: Session = Depends(get_db)):
    user = db.get(models.UserAccount, user_id)
    if not user:
        raise HTTPException(404, "user not found")
    db.delete(user)
    db.commit()
    return {"deleted": user_id}


frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8001)
