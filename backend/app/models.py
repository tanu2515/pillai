import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from .database import Base


ROLES = [
    "Attendee",
    "Event Command Operator",
    "Hospitality Operator",
    "Transport Operator",
    "Venue Manager",
]

# Every zone belongs to exactly one operator domain, so role-scoping is a
# straight zone.domain filter. Event Command Operator sees every domain.
DOMAINS = ["venue", "transport", "hospitality"]


class Event(Base):
    """Multiple rows now coexist (the BookMyShow-style catalog an attendee
    browses/searches) — but only ONE ever has status="live" at a time, and
    every existing crowd-monitoring/risk-engine function (zones, alerts,
    scenarios, the simulation clock) only ever operates on that one, via
    engine.get_live_event(). status: upcoming | live | completed."""
    __tablename__ = "events"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    event_date = Column(String, nullable=True)  # ISO date, e.g. "2026-09-15"
    event_time = Column(String, nullable=True)  # e.g. "18:00"
    category = Column(String, nullable=True)  # College Event | Concert | Conference | Sports | Festival | Workshop
    city = Column(String, nullable=True)
    venue_name = Column(String, nullable=True)
    venue_address = Column(String, nullable=True)
    banner_emoji = Column(String, nullable=True)  # stand-in for a real uploaded banner image
    is_featured = Column(Boolean, default=False)  # drives the Home screen's "Popular"/"Recommended" sections
    region = Column(String, nullable=True)  # key into regions.INDIA_REGIONS
    expected_attendance = Column(Integer, nullable=False)
    safe_capacity = Column(Integer, nullable=False)
    status = Column(String, default="live")  # upcoming | live | completed


class EventTier(Base):
    """A bookable price tier for an event's catalog listing (General/VIP/
    Premium etc) — independent of the live simulation's Zone model, so an
    'upcoming' event can be browsed and booked before it ever goes live and
    gets real zones. gate_name is just a label (e.g. "Gate 1") resolved
    against the live event's actual Zone by name at check-in time — it does
    not need a Zone to exist yet."""
    __tablename__ = "event_tiers"

    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    name = Column(String, nullable=False)
    price = Column(Float, nullable=False)
    capacity = Column(Integer, nullable=False)
    booked_count = Column(Integer, default=0)  # aggregate-tracked tier capacity (below the numbered-seat threshold)
    uses_seats = Column(Boolean, default=False)  # capacity >= 10,000: also has a bounded block of EventSeat rows
    gate_name = Column(String, nullable=True)  # e.g. "Gate 1" — which zone this tier routes to once the event is live


class EventSeat(Base):
    """Individually numbered seats — only generated for a bounded 'named'
    block within a large (uses_seats) tier, not one row per physical seat at
    stadium scale (e.g. 50,000) — see engine.SEATS_PER_LARGE_TIER. The rest
    of a large tier's capacity stays aggregate-tracked on EventTier itself,
    same as a normal tier."""
    __tablename__ = "event_seats"

    id = Column(Integer, primary_key=True)
    tier_id = Column(Integer, ForeignKey("event_tiers.id"), nullable=False)
    seat_label = Column(String, nullable=False)  # e.g. "A1"
    status = Column(String, default="available")  # available | booked


class Zone(Base):
    __tablename__ = "zones"

    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("events.id"))
    name = Column(String, nullable=False)
    type = Column(String, default="gate")
    domain = Column(String, nullable=False)  # venue | transport | hospitality
    location_note = Column(String, nullable=True)  # real-world anchor, e.g. "Panvel Railway Station"
    lat = Column(Float, nullable=True)  # map position — set by clicking the map or typing lat/long
    lng = Column(Float, nullable=True)
    capacity = Column(Integer, nullable=False)
    current_count = Column(Integer, default=0)
    last_count = Column(Integer, default=0)
    prev_delta = Column(Float, default=0.0)
    peak_count = Column(Integer, default=0)  # highest current_count ever seen — for post-event analytics
    peak_tick = Column(Integer, default=0)
    price_tier = Column(Integer, nullable=True)  # hotels only: 1 (budget) - 5 (luxury), for the recommendation ranker
    staff_assigned = Column(Integer, nullable=True)  # gates only: staff headcount, set on the Event Setup Form
    contact = Column(String, nullable=True)  # hotels/transport: phone or booking contact
    amenities = Column(String, nullable=True)  # hotels only: comma-separated, e.g. "WiFi, Breakfast, Shuttle"
    manual_recommended = Column(Boolean, default=False)  # hotels only: operator override — beats the algorithmic pick
    is_accessible = Column(Boolean, nullable=False, default=False)  # gates: wheelchair-accessible entry/exit, factored into evacuation routing
    # A gate can point at the transport zone its exiting crowd flows onto, and
    # at the hospitality zone that absorbs same-night overflow demand — used
    # to compute that gate's resource_pressure factor (SRS Section 8.2).
    linked_transport_zone_id = Column(Integer, ForeignKey("zones.id"), nullable=True)
    linked_hospitality_zone_id = Column(Integer, ForeignKey("zones.id"), nullable=True)
    ack_status = Column(String, default="open")  # open | acknowledged | escalated


class CrowdSnapshot(Base):
    """An auditable reading from any crowd-count source.

    Zone.current_count remains the live value used by the risk engine; this
    table records how that value was obtained so camera, check-in, manual and
    simulated data can share one downstream pipeline.
    """
    __tablename__ = "crowd_snapshots"

    id = Column(Integer, primary_key=True)
    zone_id = Column(Integer, ForeignKey("zones.id"), nullable=False)
    count = Column(Integer, nullable=False)
    source = Column(String, nullable=False)  # yolo | checkin | manual | simulation
    captured_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class Resource(Base):
    __tablename__ = "resources"

    id = Column(Integer, primary_key=True)
    type = Column(String, nullable=False)  # bus | staff | medical
    quantity_total = Column(Integer, nullable=False)
    quantity_available = Column(Integer, nullable=False)


class VisitorProfile(Base):
    """A booking/registration. gate_zone_id is the original direct-gate flow
    (still used for the live event's day-of registration); event_id/tier_id/
    seat_id are set instead when booked through the event catalog — gate_name
    on the tier is resolved to an actual live Zone only at check-in time."""
    __tablename__ = "visitor_profiles"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=True)  # ties a booking to My Events / the attendee's account
    gate_zone_id = Column(Integer, ForeignKey("zones.id"), nullable=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=True)
    tier_id = Column(Integer, ForeignKey("event_tiers.id"), nullable=True)
    seat_id = Column(Integer, ForeignKey("event_seats.id"), nullable=True)
    quantity = Column(Integer, default=1)
    hotel_zone_id = Column(Integer, ForeignKey("zones.id"), nullable=True)  # optional add-on picked at booking time
    wants_transport = Column(Boolean, default=False)  # optional add-on: request a shuttle/bus assignment
    code = Column(String, default=lambda: uuid.uuid4().hex[:8])
    checked_in = Column(Boolean, default=False)
    walk_in = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class HotelInventorySnapshot(Base):
    """Latest room-availability push for a hotel Zone, from the partner
    portal (manual update) or a PMS webhook — separate from Zone.current_count
    so the source/timestamp of the last live update is preserved. Falls back
    to the zone's own current_count/capacity when no snapshot exists yet."""
    __tablename__ = "hotel_inventory_snapshots"

    id = Column(Integer, primary_key=True)
    hotel_id = Column(Integer, ForeignKey("zones.id"), nullable=False, unique=True)
    occupied_rooms = Column(Integer, nullable=False, default=0)
    available_rooms = Column(Integer, nullable=False, default=0)
    source = Column(String, nullable=False, default="hotel_partner_portal")
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class UserAccount(Base):
    __tablename__ = "user_accounts"
    __table_args__ = (UniqueConstraint("email", "role", name="uq_user_email_role"),)

    id = Column(Integer, primary_key=True)
    email = Column(String, nullable=False)
    role = Column(String, nullable=False)  # one of models.ROLES
    current_event_id = Column(Integer, nullable=True)  # Attendee: which registered event the chatbot/dashboard currently scopes to
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Scenario(Base):
    """A named disruption/demand event an Administrator can author: which
    zones ramp, by how much per tick, and for how long. Replaces what used to
    be hardcoded Gate-2-only ramp constants in engine.py — the Event Command
    Operator picks one of these to trigger, and it's just data now, not code."""
    __tablename__ = "scenarios"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    duration_ticks = Column(Integer, nullable=False, default=11)
    # JSON object: {"<zone name>": <count added per tick>, ...}
    effects_json = Column(String, nullable=False, default="{}")


class SimState(Base):
    __tablename__ = "sim_state"

    id = Column(Integer, primary_key=True)
    tick = Column(Integer, default=0)
    scenario_active = Column(Boolean, default=False)
    active_scenario_id = Column(Integer, ForeignKey("scenarios.id"), nullable=True)
    trigger_tick = Column(Integer, default=-1)
    emergency_active = Column(Boolean, default=False)
    emergency_zone_id = Column(Integer, ForeignKey("zones.id"), nullable=True)
    emergency_message = Column(String, nullable=True)


class Alert(Base):
    """Persisted alert lifecycle (open -> acknowledged -> resolved), separate
    from LogEntry's one-way audit trail — created when a zone crosses into
    HIGH/CRITICAL, auto-resolved when it drops back down. impact_score reuses
    the zone's own risk score (SRS Section 8.2 formula), not a second model."""
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=True)
    zone_id = Column(Integer, ForeignKey("zones.id"), nullable=True)
    alert_type = Column(String, nullable=False)  # crowd | emergency
    severity = Column(String, nullable=False)  # HIGH | CRITICAL
    impact_score = Column(Float, nullable=False)
    message = Column(String, nullable=False)
    status = Column(String, default="open")  # open | acknowledged | resolved
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    resolved_at = Column(DateTime, nullable=True)


class Notification(Base):
    """Targeted messages — a role (operator) or 'attendee' (broadcast to
    whoever's watching the affected gate) gets only the notifications
    relevant to them, not a global feed."""
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=True)
    alert_id = Column(Integer, ForeignKey("alerts.id"), nullable=True)
    audience_role = Column(String, nullable=True)  # one of models.ROLES, or null = all operators
    zone_domain = Column(String, nullable=True)
    title = Column(String, nullable=False)
    message = Column(String, nullable=False)
    priority = Column(String, default="MEDIUM")  # LOW | MEDIUM | HIGH | CRITICAL
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Attendee(Base):
    __tablename__ = "attendees"

    id = Column(Integer, primary_key=True)
    user_account_id = Column(Integer, ForeignKey("user_accounts.id"), nullable=False)
    name = Column(String, nullable=False)


class EventAttendee(Base):
    """Attendee <-> Event, many-to-many. event_name/event_date are snapshotted
    at registration time so a past registration stays meaningful even after
    an Administrator later deletes/replaces the live simulated event (Event
    rows are singleton-per-live-simulation, per create_event's own comment —
    this table is what actually lets one attendee carry multiple events)."""
    __tablename__ = "event_attendees"

    id = Column(Integer, primary_key=True)
    attendee_id = Column(Integer, ForeignKey("attendees.id"), nullable=False)
    event_id = Column(Integer, nullable=True)  # the live Event.id at registration time, if it still exists
    event_name = Column(String, nullable=False)
    event_date = Column(String, nullable=True)
    registration_status = Column(String, default="registered")
    registration_time = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class TransitRoute(Base):
    """Train/bus/flight reference data the chatbot and Transport Hub feed
    read from — previously hardcoded Python lists in engine.py, now real rows
    so they're visible in the raw-tables viewer and editable without a code
    change. mode groups them: suburban_train | long_distance_train | city_bus
    | village_bus | flight. region gates which event sees this route at all
    (only Maharashtra has independently-verified data as of writing) — same
    honest synthetic-vs-real framing as before, just data instead of code."""
    __tablename__ = "transit_routes"

    id = Column(Integer, primary_key=True)
    region = Column(String, nullable=False)
    mode = Column(String, nullable=False)
    name = Column(String, nullable=False)  # line/train name, bus route number, or airline
    destination = Column(String, nullable=True)
    description = Column(String, nullable=True)  # "via Vashi, Nerul, Kharghar" / NMMT route description
    base_arrival_min = Column(Integer, nullable=False, default=10)  # base minutes-out before per-pick offset is added


class LogEntry(Base):
    """Incident Timeline: what happened, when (by event tick), and how VYAVASTHA
    responded — scenario triggers, zone acknowledgements/escalations, zones
    newly crossing into HIGH/CRITICAL, and executed actions."""
    __tablename__ = "log_entries"

    id = Column(Integer, primary_key=True)
    tick = Column(Integer, nullable=False)
    category = Column(String, nullable=False)  # scenario | zone_critical | acknowledged | escalated | action_executed
    message = Column(String, nullable=False)
    zone_domain = Column(String, nullable=True)  # venue | transport | hospitality | null = event-wide
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
