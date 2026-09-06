import json

from sqlalchemy.orm import Session

from . import models
from .regions import DEFAULT_REGION

# 10 zones total, one owner domain each:
#   Event Command Operator (domain=venue)      -> Main Hall, VIP Zone, Gate 1, Gate 2, Gate 3
#   Event Command Operator (domain=transport)  -> Corridor A, Corridor B, Transport Hub
#   Event Command Operator (domain=hospitality)-> Hotel A, Hotel B
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
                           capacity=2000, current_count=1840, last_count=1840, price_tier=4)
    hotel_b = models.Zone(event_id=event.id, name="Hotel B", type="hotel", domain="hospitality",
                           lat=19.0230, lng=73.0470,
                           capacity=2000, current_count=1120, last_count=1120, price_tier=3)
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


def create_default_resources(db: Session):
    """Bus/staff/medical resource pools for a newly-live event. Created
    separately from zones (unlike create_zones_and_resources above) because
    there's no UI to add these directly — a new event's zones are instead
    built up by hand via Event Setup's Add Gate/Hotel/Transport forms."""
    db.add_all([
        models.Resource(type="bus", quantity_total=20, quantity_available=20),
        models.Resource(type="staff", quantity_total=40, quantity_available=40),
        models.Resource(type="medical", quantity_total=10, quantity_available=10),
    ])
    db.commit()


def seed_transit_routes(db: Session):
    """Train/bus/flight rows for Maharashtra/Navi Mumbai — real routes,
    illustrative timing (see engine.py's transport_hub_arrivals/
    local_transit_feed for how these are sampled). Global reference data,
    independent of event resets, same as scenarios."""
    if db.query(models.TransitRoute).count() > 0:
        return

    routes = []
    for name, destination in [
        ("IndiGo", "Bengaluru"), ("IndiGo", "Jaipur"), ("IndiGo", "Nagpur"), ("IndiGo", "Patna"),
        ("IndiGo", "Indore"), ("IndiGo", "Ahmedabad"), ("Air India Express", "Delhi"),
        ("Air India Express", "Bengaluru"), ("Akasa Air", "Goa"), ("Akasa Air", "Kochi"),
        ("Akasa Air", "Delhi"), ("Akasa Air", "Ahmedabad"),
    ]:
        routes.append(models.TransitRoute(region="Maharashtra", mode="flight", name=name, destination=destination, base_arrival_min=8))

    for line, dest, via in [
        ("Harbour Line", "CSMT", "via Vashi, Nerul, Kharghar"),
        ("Harbour Line", "Goregaon", "via Vashi, Nerul, Kharghar"),
        ("Trans-Harbour Line", "Thane", "via Vashi, Koparkhairane"),
    ]:
        routes.append(models.TransitRoute(region="Maharashtra", mode="suburban_train", name=line, destination=dest, description=via, base_arrival_min=4))

    # Panvel Junction also carries Central Railway mainline/Konkan Railway
    # long-distance services (platforms 5-7) — separate from the suburban
    # Harbour/Trans-Harbour services above, and the reason it's called one of
    # Central Railway's most important junctions, not just a suburban terminus.
    for name, dest, via in [
        ("Solapur Vande Bharat Express", "Solapur", "Panvel–Karjat–Pune corridor"),
        ("Panvel–Hazur Sahib Nanded Express", "Hazur Sahib Nanded", "Panvel–Karjat–Pune corridor"),
        ("Deccan Express", "Pune", "Panvel–Karjat–Pune corridor"),
        ("Pragati Express", "Pune", "Panvel–Karjat–Pune corridor"),
        ("Konkan Railway Express", "Ratnagiri/Goa direction", "via Roha, platform 7"),
    ]:
        routes.append(models.TransitRoute(region="Maharashtra", mode="long_distance_train", name=name, destination=dest, description=via, base_arrival_min=15))

    for route, desc in [
        ("24", "Panvel Railway Station ↔ Thane, via Ghansoli Depot"),
        ("59", "Usarli Khurd ↔ Panvel Railway Station"),
        ("Kharghar–Panvel", "Kharghar ↔ Panvel — every 10–15 min at peak"),
        ("Vashi–Belapur", "Vashi ↔ Belapur — every 10–15 min at peak"),
    ]:
        routes.append(models.TransitRoute(region="Maharashtra", mode="city_bus", name=route, description=desc, base_arrival_min=3))

    # MSRTC (state transport) village/outstation routes out of Panvel ST Depot —
    # Panvel is the headquarters of Raigad district's largest sub-division by
    # village count, and the depot is the real gateway from Navi Mumbai into
    # interior Raigad/Konkan, distinct from NMMT's local city routes above.
    for route, desc in [
        ("Panvel–Alibaug", "via Pen — Raigad district"),
        ("Panvel–Pen", "Raigad district"),
        ("Panvel–Roha", "Raigad district, Konkan gateway"),
        ("Panvel–Mangaon", "Raigad district"),
        ("Panvel–Khopoli", "Raigad district"),
        ("Panvel–Karjat", "Raigad district"),
        ("Panvel–Uran", "Raigad district"),
    ]:
        routes.append(models.TransitRoute(region="Maharashtra", mode="village_bus", name=route, description=desc, base_arrival_min=10))

    db.add_all(routes)
    db.commit()


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


def seed_sample_catalog_events(db: Session):
    """A couple of 'upcoming' (not live/simulated) events with price tiers,
    purely for the event-browsing/booking catalog — independent of whichever
    event is live. Seeded once; an organizer can add more via the catalog API."""
    if db.query(models.Event).filter(models.Event.status == "upcoming").count() > 0:
        return

    catalog = [
        {
            "name": "Chennai Music Fest", "description": "A weekend of live music across three stages.",
            "event_date": "2026-10-18", "event_time": "17:00", "category": "Concert", "city": "Chennai",
            "venue_name": "Jawaharlal Nehru Stadium", "venue_address": "Periyar EVR High Rd, Chennai, Tamil Nadu",
            "banner_emoji": "🎤", "is_featured": True, "region": "Tamil Nadu",
            "expected_attendance": 15000, "safe_capacity": 18000,
            "tiers": [
                {"name": "General", "price": 999, "capacity": 12000, "gate_name": "Gate 1"},
                {"name": "Premium", "price": 2499, "capacity": 2500, "gate_name": "Gate 2"},
                {"name": "VIP", "price": 4999, "capacity": 500, "gate_name": "Gate 3"},
            ],
        },
        {
            "name": "Bengaluru Tech Conclave", "description": "Annual technology and innovation summit.",
            "event_date": "2026-11-05", "event_time": "09:30", "category": "Conference", "city": "Bengaluru",
            "venue_name": "KTPO Convention Centre", "venue_address": "Whitefield, Bengaluru, Karnataka",
            "banner_emoji": "💻", "is_featured": True, "region": "Karnataka",
            "expected_attendance": 8000, "safe_capacity": 9500,
            "tiers": [
                {"name": "Standard Pass", "price": 1499, "capacity": 6000, "gate_name": "Gate 1"},
                {"name": "VIP Pass", "price": 5999, "capacity": 500, "gate_name": "Gate 2"},
            ],
        },
        {
            "name": "Mumbai Freshers Party 2026", "description": "College welcome bash with live DJs and games.",
            "event_date": "2026-09-14", "event_time": "18:00", "category": "College Event", "city": "Mumbai",
            "venue_name": "Main Auditorium", "venue_address": "College Campus, Mumbai, Maharashtra",
            "banner_emoji": "🎉", "is_featured": False, "region": "Maharashtra",
            "expected_attendance": 2000, "safe_capacity": 2500,
            "tiers": [
                {"name": "General", "price": 199, "capacity": 2000, "gate_name": "Gate 1"},
            ],
        },
        {
            "name": "Goa Beach Festival", "description": "Music, food stalls and beach games all weekend.",
            "event_date": "2026-12-20", "event_time": "16:00", "category": "Festival", "city": "Margao",
            "venue_name": "Pandit Jawaharlal Nehru Stadium, Fatorda", "venue_address": "Fatorda, Margao, Goa",
            "banner_emoji": "🏖️", "is_featured": True, "region": "Goa",
            "expected_attendance": 10000, "safe_capacity": 12000,
            "tiers": [
                {"name": "General", "price": 799, "capacity": 9000, "gate_name": "Gate 1"},
                {"name": "VIP", "price": 2999, "capacity": 1000, "gate_name": "Gate 2"},
            ],
        },
    ]
    from .engine import create_event_listing
    for c in catalog:
        create_event_listing(
            db, c["name"], c["description"], c["event_date"], c["region"],
            c["expected_attendance"], c["safe_capacity"], c["tiers"],
            event_time=c.get("event_time"), category=c.get("category"), city=c.get("city"),
            venue_name=c.get("venue_name"), venue_address=c.get("venue_address"),
            banner_emoji=c.get("banner_emoji"), is_featured=c.get("is_featured", False),
        )


def seed_if_empty(db: Session):
    seed_default_scenarios(db)  # scenarios are global reference data, independent of event resets
    seed_transit_routes(db)
    seed_sample_catalog_events(db)

    from .engine import get_live_event
    if get_live_event(db):
        return

    event = models.Event(
        name="Panvel Mega Fest — Navi Mumbai",
        region=DEFAULT_REGION,
        expected_attendance=50000,
        safe_capacity=60000,
        status="live",
    )
    db.add(event)
    db.flush()

    create_zones_and_resources(db, event)
    db.add(models.SimState(tick=0, scenario_active=False, trigger_tick=-1))
    db.commit()

    from .engine import apply_region
    apply_region(db, DEFAULT_REGION, rename=False)
