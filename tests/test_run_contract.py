from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError as SchemaValidationError

from codeagent_v2.model import ValidationError
from codeagent_v2.run_api import InMemoryRunStore, RunAPI
from codeagent_v2.runtime import FixedExecutor
from codeagent_v2.schemas import (
    CancelRunRequest,
    CreateRunRequest,
    ResumeRunRequest,
    RunResponse,
    RunStatus,
)


class RunSchemaTests(unittest.TestCase):
    def test_status_enum_and_optional_response_fields(self) -> None:
        self.assertEqual(
            {status.value for status in RunStatus},
            {"queued", "running", "waiting_for_user", "succeeded", "failed", "cancelled"},
        )
        response = RunResponse(
            run_id="run_1",
            run_type="text",
            status=RunStatus.QUEUED,
            created_at=datetime(2026, 11, 19, tzinfo=UTC),
        )
        for field in ("input", "output", "error", "started_at", "ended_at"):
            with self.subTest(field=field):
                self.assertIsNone(getattr(response, field))

    def test_create_request_field_boundaries(self) -> None:
        valid = {"run_type": "text", "idempotency_key": "key-0001", "input": {"query": "test"}}
        for field, value in (
            ("run_type", ""),
            ("run_type", "x" * 101),
            ("idempotency_key", "short"),
            ("idempotency_key", "x" * 201),
            ("input", []),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(SchemaValidationError):
                CreateRunRequest.model_validate({**valid, field: value})

    def test_invalid_input_metadata_is_rejected(self) -> None:
        deep_value = "nested"
        for _ in range(64):
            deep_value = [deep_value]
        for value in ("\ud800", float("inf"), deep_value):
            with self.subTest(value=value), self.assertRaises(SchemaValidationError):
                CreateRunRequest(
                    run_type="text",
                    idempotency_key="key-0001",
                    input={"query": "test", "metadata": value},
                )

    def test_cancel_and_resume_field_boundaries(self) -> None:
        self.assertEqual(CancelRunRequest(reason="a" * 300).reason, "a" * 300)
        self.assertEqual(ResumeRunRequest(resume_token="x" * 200).input, {})
        for model, field, value in (
            (CancelRunRequest, "reason", ""),
            (CancelRunRequest, "reason", "a" * 301),
            (ResumeRunRequest, "resume_token", ""),
            (ResumeRunRequest, "resume_token", "a" * 201),
        ):
            with self.subTest(field=field, length=len(value)), self.assertRaises(SchemaValidationError):
                model.model_validate({field: value})


class RunContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryRunStore()
        self.api = RunAPI(self.store, FixedExecutor())

    def test_create_and_query_return_same_typed_response(self) -> None:
        nested_value = "nested"
        for _ in range(63):
            nested_value = [nested_value]
        inputs = {"query": "解释任务队列", "tag": {"id": 3}, "nested": nested_value}
        request = CreateRunRequest(run_type="text", idempotency_key="key-0001", input=inputs)

        response = self.api.create_run(request)

        self.assertIsInstance(response, RunResponse)
        self.assertEqual(response.run_type, "text")
        self.assertEqual(response.status, RunStatus.SUCCEEDED)
        self.assertEqual(response.input, inputs)
        self.assertEqual(response.output, {"text": "固定执行器完成：解释任务队列"})
        self.assertIsNone(response.error)
        times = [response.created_at, response.started_at, response.ended_at]
        self.assertEqual(times, sorted(times))
        self.assertTrue(all(time.utcoffset() == timedelta(0) for time in times))
        self.assertEqual(self.api.query_run(response.run_id), response)

    def test_failed_response_remains_queryable(self) -> None:
        request = CreateRunRequest(
            run_type="text", idempotency_key="key-0001", input={"query": "[fail] 验收"}
        )
        response = self.api.create_run(request)

        self.assertEqual(response.status, RunStatus.FAILED)
        self.assertIsNone(response.output)
        self.assertEqual(response.error, {"code": "RUN_FAILED", "message": "固定执行器按要求返回失败"})
        self.assertEqual(self.api.query_run(response.run_id), response)

    def test_response_and_request_mutations_leave_stored_input_intact(self) -> None:
        request = CreateRunRequest(
            run_type="text", idempotency_key="key-0001", input={"query": "原文", "tag": {"id": 3}}
        )
        response = self.api.create_run(request)
        request.input["tag"]["id"] = 4
        response.input["tag"]["id"] = 5

        stored = self.api.query_run(response.run_id)
        self.assertEqual(stored.input, {"query": "原文", "tag": {"id": 3}})

    def test_invalid_query_creates_no_record(self) -> None:
        request = CreateRunRequest(
            run_type="text", idempotency_key="key-0001", input={"query": " "}
        )
        with self.assertRaises(ValidationError):
            self.api.create_run(request)
        self.assertEqual(self.store.count(), 0)

    def test_unknown_query_has_no_response(self) -> None:
        self.assertIsNone(self.api.query_run("run_missing"))
