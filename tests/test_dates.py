import unittest
from datetime import date, datetime

import pytz

from app.agent.dates import (
    local_day_bounds,
    next_day_iso,
    parse_explicit_or_relative_date,
    parse_iso_date_range,
    parse_task_due_date,
    resolve_named_period_range,
    resolve_relative_iso_date,
)


class DateContractsTest(unittest.TestCase):
    def test_resolve_relative_iso_date_uses_given_base_date(self) -> None:
        result = resolve_relative_iso_date(
            "listar tarefas de hoje",
            base_date=date(2026, 5, 20),
            allow_yesterday=True,
        )

        self.assertEqual("2026-05-20", result)

    def test_resolve_relative_iso_date_can_resolve_yesterday(self) -> None:
        result = resolve_relative_iso_date(
            "listar tarefas de ontem",
            base_date=date(2026, 5, 20),
            allow_yesterday=True,
        )

        self.assertEqual("2026-05-19", result)

    def test_parse_explicit_or_relative_date_handles_day_after_tomorrow(self) -> None:
        result = parse_explicit_or_relative_date(
            "vamos planejar depois de amanhã",
            now=datetime(2026, 5, 20, 9, 0),
        )

        self.assertEqual("2026-05-22", result)

    def test_parse_explicit_or_relative_date_handles_iso(self) -> None:
        result = parse_explicit_or_relative_date(
            "mover para 2026-06-01",
            now=datetime(2026, 5, 20, 9, 0),
        )

        self.assertEqual("2026-06-01", result)

    def test_parse_explicit_or_relative_date_rolls_br_date_without_year(self) -> None:
        result = parse_explicit_or_relative_date(
            "mover para 01/01",
            now=datetime(2026, 5, 20, 9, 0),
        )

        self.assertEqual("2027-01-01", result)

    def test_parse_task_due_date_supports_date_only(self) -> None:
        parsed, date_only = parse_task_due_date("2026-05-20", timezone=pytz.timezone("America/Sao_Paulo"))

        self.assertEqual("2026-05-20 00:00", parsed.strftime("%Y-%m-%d %H:%M"))
        self.assertTrue(date_only)

    def test_parse_task_due_date_supports_datetime(self) -> None:
        parsed, date_only = parse_task_due_date("2026-05-20 10:30", timezone=pytz.timezone("America/Sao_Paulo"))

        self.assertEqual("2026-05-20 10:30", parsed.strftime("%Y-%m-%d %H:%M"))
        self.assertFalse(date_only)

    def test_parse_task_due_date_rejects_invalid_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid_due_date"):
            parse_task_due_date("amanha cedo", timezone=pytz.timezone("America/Sao_Paulo"))

    def test_local_day_bounds_cover_full_local_day(self) -> None:
        start, end = local_day_bounds(date(2026, 5, 20), timezone=pytz.timezone("America/Sao_Paulo"))

        self.assertEqual("2026-05-20 00:00", start.strftime("%Y-%m-%d %H:%M"))
        self.assertEqual("2026-05-20 23:59", end.strftime("%Y-%m-%d %H:%M"))

    def test_parse_iso_date_range_supports_single_day(self) -> None:
        label, start, end = parse_iso_date_range("2026-05-20", timezone=pytz.timezone("America/Sao_Paulo"))

        self.assertEqual("2026-05-20", label)
        self.assertEqual("2026-05-20 00:00", start.strftime("%Y-%m-%d %H:%M"))
        self.assertEqual("2026-05-20 23:59", end.strftime("%Y-%m-%d %H:%M"))

    def test_resolve_named_period_range_supports_this_week(self) -> None:
        tz = pytz.timezone("America/Sao_Paulo")
        logical_bounds = local_day_bounds(date(2026, 5, 20), timezone=tz)
        label, start, end = resolve_named_period_range(
            "this_week",
            today=date(2026, 5, 20),
            logical_today_bounds=logical_bounds,
            timezone=tz,
        )

        self.assertEqual("esta semana", label)
        self.assertEqual("2026-05-18 00:00", start.strftime("%Y-%m-%d %H:%M"))
        self.assertEqual("2026-05-24 23:59", end.strftime("%Y-%m-%d %H:%M"))

    def test_next_day_iso_formats_next_calendar_day(self) -> None:
        self.assertEqual("2026-05-21", next_day_iso(date(2026, 5, 20)))


if __name__ == "__main__":
    unittest.main()
