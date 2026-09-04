from sqlalchemy.orm import Session

from . import models


def seed_if_empty(db: Session):
    if db.query(models.Event).count() > 0:
        return

    event = models.Event(
        name="Panvel Mega Fest — Navi Mumbai",
        expected_attendance=50000,
        safe_capacity=60000,
        status="active",
    )
    db.add(event)
    db.flush()

    # 10 zones total, one owner domain each:
    #   Venue Manager      -> Main Hall, VIP Zone, Gate 1, Gate 2, Gate 3
    #   Transport Operator -> Corridor A, Corridor B, Transport Hub
    #   Hospitality Operator -> Hotel A, Hotel B
    # Gate 2 -> Corridor B -> Hotel A stays the canonical SRS Appendix chain
    # (Gate 2 ~115%, Corridor B ~108%, Hotel A ~97% after an 11-tick ramp) —
    # same underlying numbers as before, just modeled as zones now.

    # Real-world anchors (verified via web search, not invented):
    #  - D Y Patil Stadium, Nerul: real 45,300-cap concert venue ("Western
    #    India's largest concert destination" — Justin Bieber 2017, etc.)
    #  - Sion-Panvel Expressway / Palm Beach Road: the actual Mumbai<->Panvel
    #    road corridors through Navi Mumbai
    #  - Panvel Railway Station: real Harbour + Trans-Harbour line junction
    #    that itself interconnects to Navi Mumbai International Airport (NMIA)
    #  - NMIA opened 25 Dec 2025 (IndiGo, Air India Express, Akasa Air)
    #  - Four Points by Sheraton (Vashi) / Serenity Monarch (near Panvel
    #    station): real hotels in the area
    main_hall = models.Zone(event_id=event.id, name="Main Hall", type="arena", domain="venue",
                             location_note="D Y Patil Stadium bowl, Sector 7, Nerul — Western India's largest concert venue",
                             capacity=45300, current_count=37000, last_count=37000)
    vip_zone = models.Zone(event_id=event.id, name="VIP Zone", type="vip", domain="venue",
                            location_note="D Y Patil Stadium VIP stand, Nerul",
                            capacity=2000, current_count=900, last_count=900)
    corridor_a = models.Zone(event_id=event.id, name="Corridor A", type="corridor", domain="transport",
                              location_note="Sion–Panvel Expressway — the main Mumbai↔Panvel road corridor",
                              capacity=6000, current_count=3200, last_count=3200)
    corridor_b = models.Zone(event_id=event.id, name="Corridor B", type="corridor", domain="transport",
                              location_note="Palm Beach Road, Navi Mumbai",
                              capacity=6000, current_count=5700, last_count=5700)
    transport_hub = models.Zone(event_id=event.id, name="Transport Hub", type="hub", domain="transport",
                                 location_note="Panvel Railway Station — Harbour + Trans-Harbour junction, connects to NMIA",
                                 capacity=5000, current_count=2100, last_count=2100)
    hotel_a = models.Zone(event_id=event.id, name="Hotel A", type="hotel", domain="hospitality",
                           location_note="Four Points by Sheraton, Vashi",
                           capacity=2000, current_count=1840, last_count=1840)
    hotel_b = models.Zone(event_id=event.id, name="Hotel B", type="hotel", domain="hospitality",
                           location_note="Serenity Monarch, near Panvel Railway Station",
                           capacity=2000, current_count=1120, last_count=1120)
    db.add_all([main_hall, vip_zone, corridor_a, corridor_b, transport_hub, hotel_a, hotel_b])
    db.flush()

    gate_1 = models.Zone(event_id=event.id, name="Gate 1", type="gate", domain="venue",
                          location_note="D Y Patil Stadium Gate 1, Sector 7, Nerul",
                          capacity=10000, current_count=4500, last_count=4500,
                          linked_transport_zone_id=corridor_a.id)
    gate_2 = models.Zone(event_id=event.id, name="Gate 2", type="gate", domain="venue",
                          location_note="D Y Patil Stadium Gate 2, Sector 7, Nerul",
                          capacity=10000, current_count=8800, last_count=8800,
                          linked_transport_zone_id=corridor_b.id, linked_hospitality_zone_id=hotel_a.id)
    gate_3 = models.Zone(event_id=event.id, name="Gate 3", type="gate", domain="venue",
                          location_note="D Y Patil Stadium Gate 3, Sector 7, Nerul",
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

    db.add(models.SimState(tick=0, scenario_active=False, trigger_tick=-1))

    db.commit()
