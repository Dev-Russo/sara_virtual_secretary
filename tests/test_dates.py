import unittest
from datetime import date, datetime

import pytz

from app.agent.dates import parse_explicit_or_relative_date, resolve_relative_iso_date
from app.agent.dates import parse_task_due_date


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


if __name__ == "__main__":
    unittest.main()
