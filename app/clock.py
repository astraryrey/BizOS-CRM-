"""
Virtual clock. All code reads time ONLY from here (clock.now()).

Demo feature: jump the clock forward and the scheduler immediately sends
everything that has come due. Turns a two-day wait into five seconds.
"""
from datetime import datetime, timedelta

_offset = timedelta(0)


def now() -> datetime:
    return datetime.utcnow() + _offset


def travel(days: int = 0, hours: int = 0) -> datetime:
    global _offset
    _offset += timedelta(days=days, hours=hours)
    return now()


def reset() -> datetime:
    global _offset
    _offset = timedelta(0)
    return now()
