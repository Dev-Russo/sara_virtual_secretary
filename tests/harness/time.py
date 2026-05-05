"""Time helpers for deterministic scheduler and scenario tests."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime

import app.scheduler.jobs as jobs


@contextmanager
def freeze_jobs_now(frozen_dt: datetime):
    """Temporarily freeze `datetime.now()` inside `app.scheduler.jobs`."""
    real_datetime = jobs.datetime

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return frozen_dt.replace(tzinfo=None)
            return frozen_dt.astimezone(tz)

    jobs.datetime = FrozenDateTime
    try:
        yield
    finally:
        jobs.datetime = real_datetime

