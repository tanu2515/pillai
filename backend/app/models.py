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
    capacity = Column(Integer, nullable=False)
    current_count = Column(Integer, default=0)
    last_count = Column(Integer, default=0)
    prev_delta = Column(Float, default=0.0)
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
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class SimState(Base):
    __tablename__ = "sim_state"

    id = Column(Integer, primary_key=True)
    tick = Column(Integer, default=0)
    scenario_active = Column(Boolean, default=False)
    trigger_tick = Column(Integer, default=-1)
