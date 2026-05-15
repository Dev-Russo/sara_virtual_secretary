import unittest

from app.agent.review_helpers import summarize_review_outcome


class ReviewHelpersTest(unittest.TestCase):
    def test_summarize_review_outcome_reports_move_failures(self) -> None:
        result = summarize_review_outcome(
            task_titles_by_id={
                "1": "Feita",
                "2": "Mover 1",
                "3": "Mover 2",
            },
            done_ids=["1"],
            done_status_by_id={"1": "success"},
            pending_ids=["2", "3"],
            pending_action="move",
            pending_date="2026-05-20",
            move_status_by_id={"2": "success", "3": "error"},
        )

        self.assertIn("Feita", result)
        self.assertIn("Mover 1", result)
        self.assertIn("Não consegui aplicar tudo em: Mover 2.", result)

    def test_summarize_review_outcome_keeps_pending_titles_without_move(self) -> None:
        result = summarize_review_outcome(
            task_titles_by_id={
                "1": "Feita",
                "2": "Pendente",
            },
            done_ids=["1"],
            done_status_by_id={"1": "success"},
            pending_ids=["2"],
            pending_action="keep",
            pending_date=None,
            move_status_by_id={},
        )

        self.assertIn("Feita", result)
        self.assertIn("Pendente", result)
        self.assertNotIn("Não consegui aplicar tudo", result)


if __name__ == "__main__":
    unittest.main()
