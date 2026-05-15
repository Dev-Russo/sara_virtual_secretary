import re
from datetime import date, datetime, timedelta

import pytz


def resolve_relative_iso_date(
    message: str,
    *,
    base_date: date,
    allow_yesterday: bool = False,
    allow_day_after_tomorrow: bool = False,
) -> str | None:
    if not message:
        return None

    msg = message.lower().strip()
    if allow_day_after_tomorrow and re.search(r"\bdepois de amanh[aã]\b", msg):
        return (base_date + timedelta(days=2)).strftime("%Y-%m-%d")
    if re.search(r"\bamanh[aã]\b", msg):
        return (base_date + timedelta(days=1)).strftime("%Y-%m-%d")
    if re.search(r"\bhoje\b", msg):
        return base_date.strftime("%Y-%m-%d")
    if allow_yesterday and re.search(r"\bontem\b", msg):
        return (base_date - timedelta(days=1)).strftime("%Y-%m-%d")
    return None


def parse_explicit_or_relative_date(message: str, *, now: datetime) -> str | None:
    if not message:
        return None

    relative = resolve_relative_iso_date(
        message,
        base_date=now.date(),
        allow_day_after_tomorrow=True,
    )
    if relative:
        return relative

    msg = message.lower().strip()
    match_iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", msg)
    if match_iso:
        try:
            return datetime.strptime(match_iso.group(1), "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            return None

    match_br = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b", msg)
    if not match_br:
        return None

    dia = int(match_br.group(1))
    mes = int(match_br.group(2))
    ano = int(match_br.group(3) or now.year)
    try:
        parsed = date(ano, mes, dia)
        if match_br.group(3) is None and parsed < now.date():
            parsed = date(ano + 1, mes, dia)
        return parsed.strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse_task_due_date(value: str | None, *, timezone: pytz.BaseTzInfo) -> tuple[datetime | None, bool]:
    text = str(value or "").strip()
    if not text:
        return None, False

    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            return timezone.localize(parsed), fmt == "%Y-%m-%d"
        except ValueError:
            continue
    raise ValueError("invalid_due_date")


def local_day_bounds(value: date, *, timezone: pytz.BaseTzInfo) -> tuple[datetime, datetime]:
    start = timezone.localize(datetime.combine(value, datetime.min.time()))
    end = timezone.localize(datetime.combine(value, datetime.max.time().replace(microsecond=0)))
    return start, end


def parse_iso_date_range(
    start_date: str,
    end_date: str | None = None,
    *,
    timezone: pytz.BaseTzInfo,
) -> tuple[str, datetime, datetime] | None:
    try:
        start = datetime.strptime(start_date.strip(), "%Y-%m-%d").date()
        end = datetime.strptime((end_date or start_date).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None

    start_dt, _ = local_day_bounds(start, timezone=timezone)
    _, end_dt = local_day_bounds(end, timezone=timezone)
    label = start_date if start_date == (end_date or start_date) else f"{start_date} a {end_date}"
    return label, start_dt, end_dt


def resolve_named_period_range(
    period: str,
    *,
    today: date,
    logical_today_bounds: tuple[datetime, datetime],
    timezone: pytz.BaseTzInfo,
) -> tuple[str, datetime, datetime] | None:
    normalized = (period or "").strip().lower()

    if normalized == "today":
        start, end = logical_today_bounds
        return "hoje", start, end
    if normalized == "yesterday":
        start, end = local_day_bounds(today - timedelta(days=1), timezone=timezone)
        return "ontem", start, end
    if normalized == "this_week":
        monday = today - timedelta(days=today.weekday())
        start, _ = local_day_bounds(monday, timezone=timezone)
        _, end = local_day_bounds(monday + timedelta(days=6), timezone=timezone)
        return "esta semana", start, end
    if normalized == "last_week":
        monday = today - timedelta(days=today.weekday() + 7)
        start, _ = local_day_bounds(monday, timezone=timezone)
        _, end = local_day_bounds(monday + timedelta(days=6), timezone=timezone)
        return "semana passada", start, end
    return None
