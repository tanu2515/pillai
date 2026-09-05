import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from .database import Base


ROLES = [
    "Administrator",
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
    __tablename__ = "events"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    region = Column(String, nullable=True)  # key into regions.INDIA_REGIONS
    expected_attendance = Column(Integer, nullable=False)
    safe_capacity = Column(Integer, nullable=False)
    status = Column(String, default="active")


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
    # A gate can point at the transport zone its exiting crowd flows onto, and
    # at the hospitality zone that absorbs same-night overflow demand — used
    # to compute that gate's resource_pressure factor (SRS Section 8.2).
    linked_transport_zone_id = Column(Integer, ForeignKey("zones.id"), nullable=True)
    linked_hospitality_zone_id = Column(Integer, ForeignKey("zones.id"), nullable=True)
    ack_status = Column(String, default="open")  # open | acknowledged | escalated


class Resource(Base):
    __tablename__ = "resources"

    id = Column(Integer, primary_key=True)
    type = Column(String, nullable=False)  # bus | staff | medical
    quantity_total = Column(Integer, nullable=False)
    quantity_available = Column(Integer, nullable=False)


class VisitorProfile(Base):
    __tablename__ = "visitor_profiles"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    gate_zone_id = Column(Integer, ForeignKey("zones.id"))
    code = Column(String, default=lambda: uuid.uuid4().hex[:8])
    checked_in = Column(Boolean, default=False)
    walk_in = Column(Boolean, default=False)


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
