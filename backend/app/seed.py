import json

from sqlalchemy.orm import Session

from . import models
from .regions import DEFAULT_REGION

# 10 zones total, one owner domain each:
#   Venue Manager        -> Main Hall, VIP Zone, Gate 1, Gate 2, Gate 3
#   Transport Operator   -> Corridor A, Corridor B, Transport Hub
#   Hospitality Operator -> Hotel A, Hotel B
# Gate 2 -> Corridor B -> Hotel A stays the canonical SRS Appendix chain
# (Gate 2 ~115%, Corridor B ~108%, Hotel A ~97% after the default "Session
# Release" scenario's 11-tick ramp). Real-world location_note grounding is
# applied afterwards via regions.apply_region(), not hardcoded here — this
# function only creates the generic structure and baseline numbers.


def create_zones_and_resources(db: Session, event: models.Event):
    # Illustrative map placements around D Y Patil Stadium, Nerul (~19.033, 73.030)
    # — a demo layout, not surveyed coordinates. Freely editable afterwards by
    # clicking the map or typing lat/long in the Command Centre.
    main_hall = models.Zone(event_id=event.id, name="Main Hall", type="arena", domain="venue",
                             lat=19.0330, lng=73.0297,
                             capacity=50000, current_count=41000, last_count=41000)
    vip_zone = models.Zone(event_id=event.id, name="VIP Zone", type="vip", domain="venue",
                            lat=19.0335, lng=73.0300,
                            capacity=2000, current_count=900, last_count=900)
    corridor_a = models.Zone(event_id=event.id, name="Corridor A", type="corridor", domain="transport",
                              lat=19.0340, lng=73.0250,
                              capacity=6000, current_count=3200, last_count=3200)
    corridor_b = models.Zone(event_id=event.id, name="Corridor B", type="corridor", domain="transport",
                              lat=19.0340, lng=73.0350,
                              capacity=6000, current_count=5700, last_count=5700)
    transport_hub = models.Zone(event_id=event.id, name="Transport Hub", type="hub", domain="transport",
                                 lat=19.0250, lng=73.0450,
                                 capacity=5000, current_count=2100, last_count=2100)
    hotel_a = models.Zone(event_id=event.id, name="Hotel A", type="hotel", domain="hospitality",
                           lat=19.0450, lng=73.0150,
                           capacity=2000, current_count=1840, last_count=1840)
    hotel_b = models.Zone(event_id=event.id, name="Hotel B", type="hotel", domain="hospitality",
                           lat=19.0230, lng=73.0470,
                           capacity=2000, current_count=1120, last_count=1120)
    db.add_all([main_hall, vip_zone, corridor_a, corridor_b, transport_hub, hotel_a, hotel_b])
    db.flush()

    gate_1 = models.Zone(event_id=event.id, name="Gate 1", type="gate", domain="venue",
                          lat=19.0330, lng=73.0270,
                          capacity=10000, current_count=4500, last_count=4500,
                          linked_transport_zone_id=corridor_a.id)
    gate_2 = models.Zone(event_id=event.id, name="Gate 2", type="gate", domain="venue",
                          lat=19.0330, lng=73.0325,
                          capacity=10000, current_count=8800, last_count=8800,
                          linked_transport_zone_id=corridor_b.id, linked_hospitality_zone_id=hotel_a.id)
    gate_3 = models.Zone(event_id=event.id, name="Gate 3", type="gate", domain="venue",
                          lat=19.0305, lng=73.0297,
                          capacity=10000, current_count=4100, last_count=4100,
                          linked_transport_zone_id=transport_hub.id)
    db.add_all([gate_1, gate_2, gate_3])
    db.flush()

    resources = [
        models.Resource(type="bus", quantity_total=20, quantity_available=20),
        models.Resource(type="staff", quantity_total=40, quantity_available=40),
        models.Resource(type="medical", quantity_total=10, quantity_available=10),
    ]
    db.add_all(resources)
    db.flush()


def seed_default_scenarios(db: Session):
    if db.query(models.Scenario).count() > 0:
        return
    db.add(models.Scenario(
        name="Session Release",
        description="Headline session ends — attendees begin exiting toward Gate 2",
        duration_ticks=11,
        effects_json=json.dumps({"Gate 2": 273, "Corridor B": 76, "Hotel A": 9}),
    ))
    db.commit()


def seed_if_empty(db: Session):
    if db.query(models.Event).count() > 0:
        seed_default_scenarios(db)  # scenarios are global reference data, independent of event resets
        return

    event = models.Event(
        name="Panvel Mega Fest — Navi Mumbai",
        region=DEFAULT_REGION,
        expected_attendance=50000,
        safe_capacity=60000,
        status="active",
    )
    db.add(event)
    db.flush()

    create_zones_and_resources(db, event)
    seed_default_scenarios(db)
    db.add(models.SimState(tick=0, scenario_active=False, trigger_tick=-1))
    db.commit()

    from .engine import apply_region
    apply_region(db, DEFAULT_REGION, rename=False)
