import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from app.agent.contracts import (
    WRITE_REASON_EXCEPTION,
    WRITE_REASON_INVALID_DUE_DATE,
    WRITE_REASON_INVALID_PERIOD,
    WRITE_REASON_POST_VALIDATION_FAILED,
    WRITE_REASON_TASK_NOT_FOUND,
    WRITE_STATUS_ERROR,
    WRITE_STATUS_INVALID_PERIOD,
    WRITE_STATUS_NOT_CONFIRMED,
    WRITE_STATUS_NOT_FOUND,
    WRITE_STATUS_SUCCESS,
)
from app.agent.tools import (
    _bulk_task_write_result,
    _task_write_result,
    delete_tasks_by_ids_result,
    reschedule_task_result,
)


class _FakeQuery:
    def __init__(self, supplier):
        self._supplier = supplier

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return self._supplier()

    def first(self):
        values = self._supplier()
        return values[0] if values else None


class _FakeDB:
    def __init__(self, tasks, *, persist_after_delete: bool = False):
        self.tasks = list(tasks)
        self.persist_after_delete = persist_after_delete
        self.deleted_ids: list[uuid.UUID] = []

    def query(self, target):
        if getattr(target, "name", None) == "id":
            return _FakeQuery(lambda: [(task.id,) for task in self.tasks])
        return _FakeQuery(lambda: list(self.tasks))

    def delete(self, task):
        self.deleted_ids.append(task.id)
        if not self.persist_after_delete:
            self.tasks = [current for current in self.tasks if current.id != task.id]

    def commit(self):
        return None

    def rollback(self):
        return None

    def refresh(self, task):
        return None

    def close(self):
        return None


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
        self.assertEqual("invalid_due_date", WRITE_REASON_INVALID_DUE_DATE)
        self.assertEqual("post_validation_failed", WRITE_REASON_POST_VALIDATION_FAILED)

    def test_delete_tasks_by_ids_result_returns_success_with_post_validation(self) -> None:
        task_a = SimpleNamespace(id=uuid.uuid4(), title="A", user_id="u1", status="pending")
        task_b = SimpleNamespace(id=uuid.uuid4(), title="B", user_id="u1", status="pending")
        fake_db = _FakeDB([task_a, task_b])

        with patch("app.agent.tools.SessionLocal", return_value=fake_db):
            result = delete_tasks_by_ids_result([str(task_b.id), str(task_a.id)], "u1")

        self.assertEqual(WRITE_STATUS_SUCCESS, result["status"])
        self.assertEqual([str(task_b.id), str(task_a.id)], result["task_ids"])
        self.assertEqual(["B", "A"], result["task_titles"])
        self.assertIsNone(result["reason"])

    def test_delete_tasks_by_ids_result_reports_not_confirmed_when_rows_remain(self) -> None:
        task = SimpleNamespace(id=uuid.uuid4(), title="Persistida", user_id="u1", status="pending")
        fake_db = _FakeDB([task], persist_after_delete=True)

        with patch("app.agent.tools.SessionLocal", return_value=fake_db):
            result = delete_tasks_by_ids_result([str(task.id)], "u1")

        self.assertEqual(WRITE_STATUS_NOT_CONFIRMED, result["status"])
        self.assertEqual(WRITE_REASON_POST_VALIDATION_FAILED, result["reason"])
        self.assertEqual([str(task.id)], result["task_ids"])
        self.assertEqual(["Persistida"], result["task_titles"])

    def test_reschedule_task_result_returns_success_with_shared_contract(self) -> None:
        task = SimpleNamespace(id=uuid.uuid4(), title="Mover", user_id="u1", status="pending", due_date=None)
        fake_db = _FakeDB([task])

        with patch("app.agent.tools.SessionLocal", return_value=fake_db):
            result = reschedule_task_result(str(task.id), "u1", "2026-05-20 10:30")

        self.assertEqual(WRITE_STATUS_SUCCESS, result["status"])
        self.assertEqual("Mover", result["task_title"])
        self.assertIn("reagendada para", result["message"])

    def test_reschedule_task_result_reports_invalid_due_date_with_shared_reason(self) -> None:
        task = SimpleNamespace(id=uuid.uuid4(), title="Mover", user_id="u1", status="pending", due_date=None)
        fake_db = _FakeDB([task])

        with patch("app.agent.tools.SessionLocal", return_value=fake_db):
            result = reschedule_task_result(str(task.id), "u1", "amanha cedo")

        self.assertEqual(WRITE_STATUS_ERROR, result["status"])
        self.assertEqual(WRITE_REASON_INVALID_DUE_DATE, result["reason"])
        self.assertEqual("Mover", result["task_title"])


if __name__ == "__main__":
    unittest.main()
