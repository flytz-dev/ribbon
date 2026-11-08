"""Test the business intelligence overview behind the dashboard panel."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from api import auth, main
from core import storage

USER = "ana"


@pytest.fixture
def client():
    main.app.dependency_overrides[auth.current_user] = lambda: USER
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def _audit(job_id, days_ago, owner=USER, profile=None, pages=(), missing=0, duration=2.0):
    created = (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds")
    storage.register_audit(job_id, created, 300, "g.pdf", "l.pdf", owner=owner, profile=profile)
    for number, (status, category, review) in enumerate(pages, start=1):
        storage.save_page(job_id, {
            "Page": number, "Overall Status": status, "Category": category, "Review": review,
            "Render ms": 100, "OCR ms": 400, "Strategies Run": 8 if status == "ERROR" else 1,
        })
    storage.finish_audit(job_id, duration, [
        {"line": 90 + index, "code": "1", "amount": "1,00"} for index in range(missing)
    ])


def test_summary_counts_pages_verdicts_and_missing_slips(client):
    _audit("a1", 1, pages=[("OK", "", ""), ("ERROR", "REVIEW", "DOCUMENT"),
                           ("ERROR", "LIKELY MISREAD", "OCR"), ("ERROR", "UNREAD", "")], missing=1)

    data = client.get("/api/v1/bi/overview?scope=mine&days=0").json()

    summary = data["summary"]
    assert (summary["audits"], summary["processed_pages"], summary["conforming_pages"]) == (1, 4, 1)
    assert (summary["flagged_pages"], summary["document_discrepancies"], summary["ocr_errors"]) == (3, 1, 1)
    assert (summary["pending_review"], summary["missing_slips"], summary["consensus_pages"]) == (1, 1, 3)
    assert summary["conformity_rate"] == 25.0


def test_labels_are_in_portuguese(client):
    _audit("a1", 1, profile="exemplo-codigo-z", pages=[("ERROR", "LIKELY MISREAD", "")])
    _audit("a2", 1, pages=[("ERROR", "REVIEW", "")])

    data = client.get("/api/v1/bi/overview?scope=mine&days=0").json()

    assert {row["name"] for row in data["discrepancies_by_category"]} == {"Provável erro de leitura", "Verificar"}
    names = {row["name"] for row in data["audits_by_profile"]}
    assert "padrao" not in names and "exemplo-codigo-z" not in names
    assert len(names) == 2


def test_timeline_includes_days_without_audits(client):
    _audit("a1", 3)
    _audit("a2", 0)

    timeline = client.get("/api/v1/bi/overview?scope=mine&days=0").json()["audits_by_date"]

    assert [row["count"] for row in timeline] == [1, 0, 0, 1]


def test_period_filter_uses_local_time(client):
    _audit("recent", 6)
    _audit("old", 8)

    data = client.get("/api/v1/bi/overview?scope=mine&days=7").json()

    assert data["summary"]["audits"] == 1


def test_scope_mine_hides_other_users(client):
    _audit("mine", 1)
    _audit("theirs", 1, owner="bia")

    mine = client.get("/api/v1/bi/overview?scope=mine&days=0").json()
    everyone = client.get("/api/v1/bi/overview?scope=all&days=0").json()

    assert mine["summary"]["audits"] == 1
    assert mine["audits_by_user"] == []
    assert everyone["summary"]["audits"] == 2


def test_overview_requires_sign_in():
    assert TestClient(main.app).get("/api/v1/bi/overview").status_code == 401


def test_invalid_scope_is_rejected(client):
    assert client.get("/api/v1/bi/overview?scope=other").status_code == 422
