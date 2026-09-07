import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.api.v1.endpoints import operations  # noqa: E402
from app.workers.tasks import orchestrator  # noqa: E402


class FakeQuery:
    def __init__(self, database, table):
        self.database = database
        self.table = table
        self.action = "select"
        self.payload = None
        self.filters = []
        self.as_single = False
        self.limit_value = None
        self.ordering = None

    def select(self, *_args, **_kwargs):
        self.action = "select"
        return self

    def update(self, payload):
        self.action = "update"
        self.payload = payload
        return self

    def insert(self, payload):
        self.action = "insert"
        self.payload = payload
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def is_(self, field, value):
        assert value == "null"
        self.filters.append((field, None))
        return self

    def single(self):
        self.as_single = True
        return self

    def maybe_single(self):
        self.as_single = True
        return self

    def gte(self, field, value):
        self.filters.append((f"{field}__gte", value))
        return self

    def order(self, field, desc=False):
        self.ordering = (field, desc)
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def _matches(self, row):
        for field, value in self.filters:
            if field.endswith("__gte"):
                if row.get(field[:-5], "") < value:
                    return False
            elif row.get(field) != value:
                return False
        return True

    def execute(self):
        rows = self.database.tables[self.table]
        if self.action == "select":
            matches = [row.copy() for row in rows if self._matches(row)]
            if self.ordering:
                field, desc = self.ordering
                matches.sort(key=lambda row: row.get(field, ""), reverse=desc)
            if self.limit_value is not None:
                matches = matches[:self.limit_value]
            data = matches[0] if self.as_single and matches else (
                None if self.as_single else matches
            )
            return SimpleNamespace(data=data)
        if self.action == "update":
            matches = [row for row in rows if self._matches(row)]
            for row in matches:
                row.update(self.payload)
            return SimpleNamespace(data=[row.copy() for row in matches])
        if self.action == "insert":
            if any(
                row.get("operation_id") == self.payload.get("operation_id")
                and row.get("component") == self.payload.get("component")
                for row in rows
            ):
                raise RuntimeError("duplicate key")
            rows.append(self.payload.copy())
            return SimpleNamespace(data=[self.payload.copy()])
        raise AssertionError(self.action)


class FakeSupabase:
    def __init__(self, *, operation_status="completed", snapshot_status="completed"):
        self.tables = {
            "operations": [
                {
                    "id": "op-1",
                    "status": operation_status,
                    "score": 59.6,
                    "rating": "C",
                    "taxa_sugerida": 0.0624,
                    "analysis_attempts": 3,
                }
            ],
            "component_snapshots": (
                [
                    {
                        "operation_id": "op-1",
                        "component": "score_engine",
                        "status": snapshot_status,
                        "started_at": datetime.now(timezone.utc).isoformat(),
                        "parsed_result": {"score": 59.6, "rating": "C"},
                    }
                ]
                if snapshot_status is not None
                else []
            ),
            "score_snapshot_versions": [],
        }

    def table(self, name):
        return FakeQuery(self, name)


def _request():
    return SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))


def test_reprocess_score_requires_director(monkeypatch):
    monkeypatch.setattr(operations, "supabase", FakeSupabase())

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            operations.reprocess_operation_score(
                "op-1",
                BackgroundTasks(),
                _request(),
                {"id": "user-1", "role": "analista"},
            )
        )

    assert exc.value.status_code == 403


@pytest.mark.parametrize("status", ["pending", "processing", "approved"])
def test_reprocess_score_rejects_invalid_operation_status(monkeypatch, status):
    monkeypatch.setattr(operations, "supabase", FakeSupabase(operation_status=status))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            operations.reprocess_operation_score(
                "op-1",
                BackgroundTasks(),
                _request(),
                {"id": "director-1", "role": "diretor"},
            )
        )

    assert exc.value.status_code == 409


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_director_can_schedule_only_score_reprocessing(monkeypatch, status):
    database = FakeSupabase(operation_status=status, snapshot_status="completed")
    monkeypatch.setattr(operations, "supabase", database)
    background = BackgroundTasks()

    result = asyncio.run(
        operations.reprocess_operation_score(
            "op-1",
            background,
            _request(),
            {"id": "director-1", "role": "diretor"},
        )
    )

    assert result["status"] == "accepted"
    assert database.tables["component_snapshots"][0]["status"] == "running"
    assert database.tables["operations"][0]["analysis_attempts"] == 3
    assert len(background.tasks) == 1
    assert background.tasks[0].func.__name__ == "reprocess_score"
    assert background.tasks[0].kwargs["previous_value"] == {
        "score": 59.6,
        "rating": "C",
        "taxa_sugerida": 0.0624,
    }


def test_missing_score_snapshot_is_recreated(monkeypatch):
    database = FakeSupabase(snapshot_status=None)
    monkeypatch.setattr(operations, "supabase", database)

    asyncio.run(
        operations.reprocess_operation_score(
            "op-1",
            BackgroundTasks(),
            _request(),
            {"id": "director-1", "role": "diretor"},
        )
    )

    assert database.tables["component_snapshots"] == [
        {
            "operation_id": "op-1",
            "component": "score_engine",
            "status": "running",
            "started_at": database.tables["component_snapshots"][0]["started_at"],
        }
    ]


def test_concurrent_score_reprocessing_is_rejected(monkeypatch):
    monkeypatch.setattr(
        operations,
        "supabase",
        FakeSupabase(snapshot_status="running"),
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            operations.reprocess_operation_score(
                "op-1",
                BackgroundTasks(),
                _request(),
                {"id": "director-1", "role": "diretor"},
            )
        )

    assert exc.value.status_code == 409
    assert "andamento" in exc.value.detail


def test_stale_score_reprocessing_lock_can_be_reclaimed(monkeypatch):
    database = FakeSupabase(snapshot_status="running")
    stale = datetime.now(timezone.utc) - timedelta(minutes=16)
    database.tables["component_snapshots"][0]["started_at"] = stale.isoformat()
    monkeypatch.setattr(operations, "supabase", database)

    result = asyncio.run(
        operations.reprocess_operation_score(
            "op-1",
            BackgroundTasks(),
            _request(),
            {"id": "director-1", "role": "diretor"},
        )
    )

    assert result["status"] == "accepted"
    refreshed = datetime.fromisoformat(
        database.tables["component_snapshots"][0]["started_at"]
    )
    assert refreshed > stale


def test_background_reprocessing_runs_only_score_and_audits_comparison(monkeypatch):
    calls = []
    audit_entries = []

    async def fake_run_component(run_fn, operation_id):
        calls.append((run_fn.__name__, operation_id))
        return {"status": "completed"}

    async def fake_complete(operation_id):
        calls.append(("complete_analysis", operation_id))
        return {
            "operation_id": operation_id,
            "status": "completed",
            "score": 49.6,
            "rating": "D",
            "taxa_sugerida": 0.0839,
        }

    class FakeAudit:
        def log(self, **kwargs):
            audit_entries.append(kwargs)

    monkeypatch.setattr(orchestrator, "_run_component", fake_run_component)
    monkeypatch.setattr(orchestrator, "_complete_analysis", fake_complete)
    archived_versions = iter([None, "version-1"])
    monkeypatch.setattr(
        orchestrator,
        "_latest_archived_score_version_id",
        lambda *_args, **_kwargs: next(archived_versions),
    )
    monkeypatch.setattr("app.services.audit_service.AuditService", FakeAudit)

    previous = {"score": 59.6, "rating": "C", "taxa_sugerida": 0.0624}
    result = asyncio.run(
        orchestrator.reprocess_score(
            "op-1",
            actor_id="director-1",
            actor_type="diretor",
            ip_address="127.0.0.1",
            previous_value=previous,
        )
    )

    assert calls == [
        ("run_score_engine", "op-1"),
        ("complete_analysis", "op-1"),
    ]
    assert result["rating"] == "D"
    assert audit_entries == [
        {
            "operation_id": "op-1",
            "action": "score_reprocessed",
            "actor_id": "director-1",
            "actor_type": "diretor",
            "ip_address": "127.0.0.1",
            "previous_value": previous,
            "new_value": {
                "score": 49.6,
                "rating": "D",
                "taxa_sugerida": 0.0839,
            },
            "payload": {
                "status": "completed",
                "archived_version_id": "version-1",
            },
        }
    ]


def test_failed_reprocessing_audit_references_archived_snapshot(monkeypatch):
    audit_entries = []

    async def fake_run_component(_run_fn, _operation_id):
        return {"error": "score failed"}

    class FakeAudit:
        def log(self, **kwargs):
            audit_entries.append(kwargs)

    monkeypatch.setattr(orchestrator, "_run_component", fake_run_component)
    archived_versions = iter(["version-older", "version-before-failure"])
    monkeypatch.setattr(
        orchestrator,
        "_latest_archived_score_version_id",
        lambda *_args, **_kwargs: next(archived_versions),
    )
    monkeypatch.setattr("app.services.audit_service.AuditService", FakeAudit)

    result = asyncio.run(
        orchestrator.reprocess_score(
            "op-1",
            actor_id="director-1",
            actor_type="diretor",
            ip_address=None,
            previous_value={"score": 59.6, "rating": "C"},
        )
    )

    assert result["status"] == "failed"
    assert audit_entries[0]["payload"] == {
        "status": "failed",
        "error": "score failed",
        "archived_version_id": "version-before-failure",
    }


def test_score_versions_endpoint_is_director_only_and_returns_history(monkeypatch):
    database = FakeSupabase()
    database.tables["score_snapshot_versions"] = [
        {
            "id": "version-old",
            "operation_id": "op-1",
            "archived_at": "2026-09-01T10:00:00+00:00",
            "parsed_result": {"score": 59.6},
        },
        {
            "id": "version-new",
            "operation_id": "op-1",
            "archived_at": "2026-09-02T10:00:00+00:00",
            "parsed_result": {"score": 49.6},
        },
    ]
    monkeypatch.setattr(operations, "supabase", database)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            operations.list_score_versions(
                "op-1",
                {"id": "user-1", "role": "analista"},
            )
        )
    assert exc.value.status_code == 403

    result = asyncio.run(
        operations.list_score_versions(
            "op-1",
            {"id": "director-1", "role": "diretor"},
        )
    )
    assert result["total"] == 2
    assert [item["id"] for item in result["items"]] == [
        "version-new",
        "version-old",
    ]


def test_score_version_migration_archives_only_previous_nonempty_score_snapshot():
    migration = (
        Path(__file__).parents[2]
        / "infra"
        / "supabase"
        / "migrations"
        / "026_score_snapshot_versions.sql"
    ).read_text(encoding="utf-8")

    assert "OLD.component = 'score_engine'" in migration
    assert "OLD.parsed_result IS DISTINCT FROM NEW.parsed_result" in migration
    assert "OLD.parsed_result IS NOT NULL" in migration
    assert "BEFORE UPDATE OF parsed_result ON component_snapshots" in migration
    assert "ON score_snapshot_versions (operation_id, archived_at DESC)" in migration
    assert "EXCEPTION WHEN duplicate_object THEN NULL" in migration
