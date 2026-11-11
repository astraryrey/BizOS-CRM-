"""
=============================================================================
INTEGRATION POINT #2 - MAPPING ("Grusher" GIS)
=============================================================================

What maps are for in this product:
  * on-site jobs and crew routes;
  * service zones and callout surcharges;
  * where customers are;
  * equipment and network node locations;
  * businesses with several premises.

The core knows only the MapAdapter interface below. How Grusher works
inside is its own business.

THREE MODES (environment variable MAPS_ADAPTER):

  null     no maps; everything else still works.
  mock     LOCAL MOCK (mocks/grusher_mock.py): geocoding, service zones,
           crew routing. Real HTTP calls to a toy service.
  grusher  PRODUCTION Grusher. Needs credentials (see below).

WHAT TO REQUEST FROM WHOEVER OWNS GRUSHER:

  1. BASE_URL of the API
  2. Token or key
  3. Which objects it exposes: addresses only, or equipment and nodes too
  4. Whether it geocodes (address -> coordinates) or only stores points
  5. Whether it computes routes, or we compute them on our side
  6. Coordinate format (WGS84 or something custom)

Once that exists, fill in GrusherAdapter and run:

  MAPS_ADAPTER=grusher GRUSHER_URL=... GRUSHER_TOKEN=... uvicorn ...

IF GRUSHER RUNS ON POSTGIS (likely):
  its database could be read directly, but it is better to ask for a
  couple of SQL views or a small HTTP endpoint. That decouples the two
  systems and survives their upgrades.
"""
import logging
import os
from typing import Protocol

import httpx

log = logging.getLogger("bizos.maps")


class MapAdapter(Protocol):
    name: str

    def geocode(self, address: str) -> dict | None:
        """Address -> {lat, lon, formatted}. None if nothing matched."""
        ...

    def zone_for(self, lat: float, lon: float) -> dict | None:
        """Which service zone a point falls in: {zone_id, name, surcharge}."""
        ...

    def route(self, points: list[dict], optimize: bool = False) -> dict:
        """
        Route through the points: {order, distance_km, duration_min, legs}.

        optimize=False - keep the order given (appointments already have
                         times promised to customers).
        optimize=True  - suggest the shortest round trip (planning a blank day).
        """
        ...

    def health(self) -> dict: ...


class NullMaps:
    name = "null"

    def geocode(self, address: str) -> dict | None:
        return None

    def zone_for(self, lat: float, lon: float) -> dict | None:
        return None

    def route(self, points: list[dict], optimize: bool = False) -> dict:
        return {"order": list(range(len(points))), "distance_km": None,
                "duration_min": None, "legs": []}

    def health(self) -> dict:
        return {"ok": True, "mode": "null", "note": "maps not connected"}


class MockGrusher:
    """Talks to mocks/grusher_mock.py over HTTP, as it would to the real GIS."""
    name = "mock-grusher"

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or os.getenv("GRUSHER_URL",
                                               "http://127.0.0.1:8082")).rstrip("/")
        self.timeout = 4.0

    def geocode(self, address: str) -> dict | None:
        r = httpx.get(f"{self.base_url}/api/geocode",
                      params={"address": address}, timeout=self.timeout)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def zone_for(self, lat: float, lon: float) -> dict | None:
        r = httpx.get(f"{self.base_url}/api/zone",
                      params={"lat": lat, "lon": lon}, timeout=self.timeout)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def route(self, points: list[dict], optimize: bool = False) -> dict:
        r = httpx.post(f"{self.base_url}/api/route",
                       json={"points": points, "optimize": optimize},
                       timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def health(self) -> dict:
        try:
            r = httpx.get(f"{self.base_url}/health", timeout=2.0)
            return {"ok": r.status_code == 200, "mode": "mock",
                    "url": self.base_url, **r.json()}
        except Exception as e:
            return {"ok": False, "mode": "mock", "url": self.base_url,
                    "error": f"maps mock is not running: {e}"}


class GrusherAdapter:
    """SKELETON FOR PRODUCTION GRUSHER. Methods marked TODO."""
    name = "grusher"

    def __init__(self):
        self.base_url = os.getenv("GRUSHER_URL", "").rstrip("/")
        self.token = os.getenv("GRUSHER_TOKEN", "")

    def _ready(self) -> bool:
        return bool(self.base_url and self.token)

    def geocode(self, address: str) -> dict | None:
        if not self._ready():
            raise NotImplementedError("Grusher not configured: GRUSHER_URL, GRUSHER_TOKEN")
        # TODO: real call once the documentation arrives
        raise NotImplementedError("Geocoding against Grusher not implemented")

    def zone_for(self, lat: float, lon: float) -> dict | None:
        raise NotImplementedError("Zone lookup not implemented")

    def route(self, points: list[dict], optimize: bool = False) -> dict:
        raise NotImplementedError("Route computation not implemented")

    def health(self) -> dict:
        return {"ok": self._ready(), "mode": "grusher", "url": self.base_url,
                "note": "production mode; needs GRUSHER_URL and GRUSHER_TOKEN"}


def get_maps() -> MapAdapter:
    which = os.getenv("MAPS_ADAPTER", "null").lower()
    if which in ("mock", "mock-grusher"):
        return MockGrusher()
    if which == "grusher":
        return GrusherAdapter()
    return NullMaps()
