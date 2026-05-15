import re
from datetime import date, datetime, timedelta


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
