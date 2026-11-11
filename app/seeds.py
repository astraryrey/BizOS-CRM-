"""
Demo data: three businesses, clients with history, addresses, coordinates.

Run: python3 -m app.seeds
"""
import random
from datetime import timedelta

from . import clock
from .db import SessionLocal, init_db
from .lifecycle import find_conflict, on_appointment_created, on_appointment_done
from .models import (
    Appointment, AppointmentStatus, Asset, Client, Resource, Service, Tenant,
)
from .verticals import load_vertical

NAMES = ["Andrew", "Oksana", "Igor", "Marina", "Sergey", "Natalia",
         "Dmytro", "Elena", "Victor", "Julia", "Oleg", "Tatiana"]
CARS = ["Toyota Camry", "VW Golf", "Skoda Octavia", "Renault Duster", "Kia Sportage"]
PETS = [("cat", "Barsik"), ("dog", "Rex"), ("cat", "Murka"), ("dog", "Jessie")]
STREETS = ["Shevchenko St", "Peremohy Ave", "Lisova St", "Sadova St",
           "Khreshchatyk St", "Khmelnytskoho St", "Nauky Ave",
           "Vyshneva St", "Prirechna St", "Druzhby Blvd"]

# Billing accounts - these match mocks/ubilling_mock.py
TENANT_SPEC = [
    ("Motor Garage", "auto_service", "GAR-0417", 50.4280, 30.5380),
    ("Malva Salon", "beauty", "SAL-2209", 50.4620, 30.5190),
    ("Aibolit Vet Clinic", "vet", "VET-3318", 50.4410, 30.5600),
]


def make_tenant(db, name, vertical, account, lat, lon) -> Tenant:
    tenant = Tenant(name=name, vertical=vertical, billing_account=account,
                    lat=lat, lon=lon)
    db.add(tenant)
    db.flush()
    cfg = load_vertical(vertical)
    for s in cfg.get("default_services", []):
        db.add(Service(tenant_id=tenant.id, **s))
    for r in cfg.get("default_resources", []):
        db.add(Resource(tenant_id=tenant.id, **r))
    db.flush()
    return tenant


def make_asset(db, tenant: Tenant, client: Client) -> Asset | None:
    cfg = load_vertical(tenant.vertical)
    atype = cfg.get("asset_type")
    if not atype:
        return None
    if atype == "car":
        brand = random.choice(CARS)
        plate = f"AA{random.randint(1000, 9999)}BB"
        asset = Asset(tenant_id=tenant.id, client_id=client.id, type="car",
                      title=f"{brand} {plate}",
                      attributes={"brand": brand, "plate": plate,
                                  "mileage": random.randint(30_000, 180_000)})
    else:
        species, nick = random.choice(PETS)
        asset = Asset(tenant_id=tenant.id, client_id=client.id, type="pet",
                      title=f"{nick} ({species})",
                      attributes={"species": species})
    db.add(asset)
    db.flush()
    return asset


def place(db, tenant, client, asset, service, resources, start, status=None):
    """Book onto the first resource that is actually free."""
    end = start + timedelta(minutes=service.duration_min)
    for res in random.sample(resources, len(resources)):
        if find_conflict(db, tenant, res.id, start, end) is None:
            appt = Appointment(tenant_id=tenant.id, client_id=client.id,
                               asset_id=asset.id if asset else None,
                               service_id=service.id, resource_id=res.id,
                               starts_at=start, ends_at=end)
            if status:
                appt.status = status
            db.add(appt)
            db.flush()
            return appt
    return None


def seed():
    init_db()
    db = SessionLocal()
    if db.query(Tenant).count():
        print("Database already seeded, skipping.")
        return

    tenants = [make_tenant(db, *spec) for spec in TENANT_SPEC]

    for tenant in tenants:
        services = db.query(Service).filter(Service.tenant_id == tenant.id).all()
        resources = db.query(Resource).filter(Resource.tenant_id == tenant.id).all()
        onsite = [s for s in services if s.onsite]

        for i in range(10):
            client = Client(
                tenant_id=tenant.id,
                phone=f"+3809{random.randint(10_000_000, 99_999_999)}",
                name=random.choice(NAMES),
                address=f"{random.choice(STREETS)} {random.randint(1, 120)}",
            )
            db.add(client)
            db.flush()
            asset = make_asset(db, tenant, client)

            # History: one or two past visits
            for _ in range(random.randint(1, 2)):
                service = random.choice(services)
                start = (clock.now() - timedelta(days=random.randint(5, 60))).replace(
                    hour=random.randint(10, 17), minute=random.choice([0, 30]),
                    second=0, microsecond=0)
                appt = place(db, tenant, client, asset, service, resources,
                             start, status=AppointmentStatus.done)
                if appt:
                    on_appointment_done(db, appt, tenant)

            # Upcoming: the first few land today so the grid is not empty
            # when the console is opened.
            if i % 2 == 0:
                service = random.choice(services)
                offset = 0 if i < 8 else random.randint(1, 5)
                start = (clock.now() + timedelta(days=offset)).replace(
                    hour=random.randint(10, 17), minute=random.choice([0, 30]),
                    second=0, microsecond=0)
                appt = place(db, tenant, client, asset, service, resources, start)
                if appt:
                    on_appointment_created(db, appt, tenant)

        # A few on-site jobs tomorrow, so the map has a route to draw
        if onsite:
            mobile = [r for r in resources if r.mobile] or resources
            clients = (db.query(Client).filter(Client.tenant_id == tenant.id)
                         .limit(4).all())
            for n, c in enumerate(clients[:3]):
                start = (clock.now() + timedelta(days=1)).replace(
                    hour=10 + n * 2, minute=0, second=0, microsecond=0)
                a = c.assets[0] if c.assets else None
                appt = place(db, tenant, c, a, onsite[0], mobile, start)
                if appt:
                    on_appointment_created(db, appt, tenant)

    db.commit()
    print("Seeded:", ", ".join(t.name for t in tenants))
    print("Businesses: 1 = garage, 2 = beauty salon, 3 = vet clinic")


if __name__ == "__main__":
    seed()
