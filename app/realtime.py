"""
Live channel to the operator console: the client card pops up by itself
when a call comes in. Plain HTTP cannot do that — the browser would have
to poll. A WebSocket lets the server push the moment telephony reports
an incoming call.
"""
import asyncio
import logging
from collections import defaultdict

from fastapi import WebSocket

log = logging.getLogger("bizos.realtime")


class PanelHub:
    """Holds open consoles, grouped by business (tenant)."""

    def __init__(self) -> None:
        self._peers: dict[int, set[WebSocket]] = defaultdict(set)

    async def join(self, tenant_id: int, ws: WebSocket) -> None:
        await ws.accept()
        self._peers[tenant_id].add(ws)
        log.info("Console connected (tenant %s), total: %s",
                 tenant_id, len(self._peers[tenant_id]))

    def leave(self, tenant_id: int, ws: WebSocket) -> None:
        self._peers[tenant_id].discard(ws)

    def count(self, tenant_id: int) -> int:
        return len(self._peers[tenant_id])

    async def push(self, tenant_id: int, event: dict) -> int:
        """Broadcast to every console of this business. Returns recipients."""
        peers = list(self._peers[tenant_id])
        if not peers:
            return 0
        results = await asyncio.gather(
            *(ws.send_json(event) for ws in peers), return_exceptions=True)
        delivered = 0
        for ws, res in zip(peers, results):
            if isinstance(res, Exception):
                self.leave(tenant_id, ws)
            else:
                delivered += 1
        return delivered


hub = PanelHub()
