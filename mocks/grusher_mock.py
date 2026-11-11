"""
MOCK GRUSHER - a standalone service on port 8082.

A toy GIS: turns an address into coordinates, says which service zone a
point falls in, and builds a crew route.

It exists so on-site jobs and service zones can be demonstrated while
there is no access to the production Grusher. When the real one arrives
this service is switched off and one environment variable changes in BizOS.

Run:  python3 -m uvicorn mocks.grusher_mock:app --port 8082
"""
import hashlib
import math

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Grusher (MOCK)", version="0.1.0",
              description="Toy GIS for the BizOS demo")

# City centre - points are scattered around it
CENTER = (50.4501, 30.5234)

# Service zones: radius from the centre -> callout surcharge
ZONES = [
    {"zone_id": "Z1", "name": "City centre", "radius_km": 4, "surcharge": 0},
    {"zone_id": "Z2", "name": "Inner ring", "radius_km": 10, "surcharge": 150},
    {"zone_id": "Z3", "name": "Outskirts", "radius_km": 22, "surcharge": 350},
]

# Provider infrastructure - what Grusher really tracks
NODES = [
    {"node_id": "MPLS-01", "name": "Pechersk node", "kind": "mpls",
     "lat": 50.4270, "lon": 30.5390},
    {"node_id": "MPLS-02", "name": "Obolon node", "kind": "mpls",
     "lat": 50.5120, "lon": 30.4980},
    {"node_id": "DC-01", "name": "Data centre", "kind": "datacenter",
     "lat": 50.4580, "lon": 30.5100},
]


class RoutePoint(BaseModel):
    lat: float
    lon: float
    label: str | None = None


class RouteIn(BaseModel):
    points: list[RoutePoint]
    # False = drive in the order the points were sent (correct when visits
    #         already have times promised to customers).
    # True  = suggest the shortest round trip (planning a blank day).
    optimize: bool = False


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance between two points, in km."""
    R = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = math.radians(b[0] - a[0])
    dl = math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def pseudo_coords(seed: str) -> tuple[float, float]:
    """
    Deterministic "coordinates" derived from the address string.

    A real geocoder looks the address up in a gazetteer; for a mock it is
    enough that the same address always lands on the same point somewhere
    around the city.
    """
    h = hashlib.sha256(seed.strip().lower().encode()).digest()
    dx = (h[0] / 255 - 0.5) * 0.28     # roughly +/-15 km of longitude
    dy = (h[1] / 255 - 0.5) * 0.17     # roughly +/-9 km of latitude
    return round(CENTER[0] + dy, 6), round(CENTER[1] + dx, 6)


@app.get("/health")
def health():
    return {"service": "grusher-mock", "zones": len(ZONES), "nodes": len(NODES)}


@app.get("/api/geocode")
def geocode(address: str):
    """Address -> coordinates. This is how BizOS learns where to send a crew."""
    if not address or len(address.strip()) < 3:
        raise HTTPException(404, "Address not recognised")
    lat, lon = pseudo_coords(address)
    return {"lat": lat, "lon": lon, "formatted": address.strip(),
            "source": "grusher-mock"}


@app.get("/api/zone")
def zone(lat: float, lon: float):
    """Which service zone a point is in, and what the callout costs."""
    d = haversine(CENTER, (lat, lon))
    for z in ZONES:
        if d <= z["radius_km"]:
            return {**z, "distance_km": round(d, 2)}
    raise HTTPException(404, "Point lies outside all service zones")


@app.get("/api/zones")
def zones():
    return {"center": {"lat": CENTER[0], "lon": CENTER[1]}, "zones": ZONES}


@app.get("/api/nodes")
def nodes():
    """Provider nodes and equipment - the 'equipment locations' layer."""
    return NODES


@app.post("/api/route")
def route(data: RouteIn):
    """
    Crew route.

    With optimize=False the points are driven in the order given - that is
    what you want once customers have been told a time. With optimize=True
    a nearest-neighbour pass suggests a shorter trip. The real Grusher
    would route along actual roads; here the order and rough mileage are
    enough to make the point.
    """
    pts = [(p.lat, p.lon) for p in data.points]
    if not pts:
        return {"order": [], "distance_km": 0, "duration_min": 0,
                "legs": [], "optimized": data.optimize}

    order, legs = [], []
    cur = CENTER
    total = 0.0

    if data.optimize:
        unvisited = list(range(len(pts)))
        while unvisited:
            i = min(unvisited, key=lambda j: haversine(cur, pts[j]))
            d = haversine(cur, pts[i])
            total += d
            legs.append({"to": i, "label": data.points[i].label,
                         "distance_km": round(d, 2)})
            order.append(i)
            cur = pts[i]
            unvisited.remove(i)
    else:
        # Order is dictated by appointment times and must not be reshuffled.
        for i in range(len(pts)):
            d = haversine(cur, pts[i])
            total += d
            legs.append({"to": i, "label": data.points[i].label,
                         "distance_km": round(d, 2)})
            order.append(i)
            cur = pts[i]

    # 25 km/h through traffic plus 20 minutes on site
    duration = total / 25 * 60 + len(order) * 20
    return {"order": order, "distance_km": round(total, 2),
            "duration_min": round(duration), "legs": legs,
            "optimized": data.optimize}
