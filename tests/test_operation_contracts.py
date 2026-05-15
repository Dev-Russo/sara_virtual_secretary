import unittest

from app.agent.contracts import (
    WRITE_REASON_EXCEPTION,
    WRITE_REASON_INVALID_PERIOD,
    WRITE_REASON_POST_VALIDATION_FAILED,
    WRITE_REASON_TASK_NOT_FOUND,
    WRITE_STATUS_ERROR,
    WRITE_STATUS_INVALID_PERIOD,
    WRITE_STATUS_NOT_CONFIRMED,
    WRITE_STATUS_NOT_FOUND,
    WRITE_STATUS_SUCCESS,
)
from app.agent.tools import _bulk_task_write_result, _task_write_result


class OperationContractsTest(unittest.TestCase):
    def test_single_write_result_uses_shared_status_and_reason(self) -> None:
        result = _task_write_result(
            status=WRITE_STATUS_NOT_CONFIRMED,
            message="falhou",
            task_id="123",
            task_title="Teste",
            reason=WRITE_REASON_POST_VALIDATION_FAILED,
        )

        self.assertEqual(WRITE_STATUS_NOT_CONFIRMED, result["status"])
        self.assertEqual(WRITE_REASON_POST_VALIDATION_FAILED, result["reason"])
        self.assertEqual("123", result["task_id"])
        self.assertEqual("Teste", result["task_title"])

    def test_bulk_write_result_preserves_shared_status_and_ids(self) -> None:
        result = _bulk_task_write_result(
            status=WRITE_STATUS_SUCCESS,
            message="ok",
            task_ids=["1", "2"],
            task_titles=["A", "B"],
        )

        self.assertEqual(WRITE_STATUS_SUCCESS, result["status"])
        self.assertEqual(["1", "2"], result["task_ids"])
        self.assertEqual(["A", "B"], result["task_titles"])
        self.assertIsNone(result["reason"])

    def test_shared_write_contract_values_are_stable(self) -> None:
        self.assertEqual("success", WRITE_STATUS_SUCCESS)
        self.assertEqual("not_found", WRITE_STATUS_NOT_FOUND)
        self.assertEqual("error", WRITE_STATUS_ERROR)
        self.assertEqual("invalid_period", WRITE_STATUS_INVALID_PERIOD)
        self.assertEqual("not_confirmed", WRITE_STATUS_NOT_CONFIRMED)
        self.assertEqual("task_not_found", WRITE_REASON_TASK_NOT_FOUND)
        self.assertEqual("exception", WRITE_REASON_EXCEPTION)
        self.assertEqual("invalid_period", WRITE_REASON_INVALID_PERIOD)
        self.assertEqual("post_validation_failed", WRITE_REASON_POST_VALIDATION_FAILED)


if __name__ == "__main__":
    unittest.main()
