import unittest
from datetime import date, datetime

from app.agent.dates import parse_explicit_or_relative_date, resolve_relative_iso_date


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


if __name__ == "__main__":
    unittest.main()
