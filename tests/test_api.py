import sqlite3
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from threading import Event

import pytest

from app import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CERTIFICATE_DB_PATH", str(tmp_path / "test.sqlite3"))
    with TestClient(main.app) as test_client:
        yield test_client


def request_body(recipients):
    return {
        "course_name": "Practical Data Science",
        "completion_date": "2026-09-30",
        "issuer": "Example Learning",
        "recipients": recipients,
    }


def test_create_job_generates_certificates_and_reports_progress(client):
    response = client.post(
        "/jobs",
        json=request_body(
            [
                {"name": "Avery Stone", "email": "avery@example.com"},
                {"name": "Jordan Lee", "email": "jordan@example.com"},
            ]
        ),
    )

    assert response.status_code == 202
    created = response.json()
    assert created["total"] == 2
    assert created["status"] == "queued"

    status = client.get(created["status_url"])
    assert status.status_code == 200
    result = status.json()
    assert result["status"] == "completed"
    assert result["total"] == 2
    assert result["succeeded"] == 2
    assert result["failed"] == 0
    assert all(item["status"] == "succeeded" for item in result["recipients"])


def test_job_status_reports_progress_while_generation_is_running(
    client, tmp_path, monkeypatch
):
    real_generator = main.generate_certificate
    second_recipient_started = Event()
    finish_second_recipient = Event()

    def pause_on_second_recipient(recipient_name, course_name, completion_date, issuer):
        if recipient_name == "Second Recipient":
            second_recipient_started.set()
            assert finish_second_recipient.wait(timeout=5)
        return real_generator(recipient_name, course_name, completion_date, issuer)

    monkeypatch.setattr(main, "generate_certificate", pause_on_second_recipient)
    body = request_body(
        [
            {"name": "First Recipient", "email": "first@example.com"},
            {"name": "Second Recipient", "email": "second@example.com"},
        ]
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending_response = executor.submit(client.post, "/jobs", json=body)
        assert second_recipient_started.wait(timeout=5)
        with sqlite3.connect(tmp_path / "test.sqlite3") as connection:
            job_id = connection.execute("SELECT id FROM jobs").fetchone()[0]
        with TestClient(main.app) as status_client:
            job = status_client.get(f"/jobs/{job_id}").json()
        assert job["status"] == "running"
        assert job["total"] == 2
        assert job["succeeded"] == 1
        assert job["failed"] == 0
        finish_second_recipient.set()
        assert pending_response.result(timeout=5).status_code == 202


def test_request_level_validation_rejects_missing_or_invalid_batch_fields(client):
    response = client.post(
        "/jobs",
        json={
            "course_name": " ",
            "completion_date": "not-a-date",
            "issuer": "Example Learning",
            "recipients": [{"name": "Avery", "email": "avery@example.com"}],
        },
    )

    assert response.status_code == 422


def test_invalid_recipient_is_reported_without_blocking_valid_recipients(client):
    response = client.post(
        "/jobs",
        json=request_body(
            [
                {"name": "Avery Stone", "email": "avery@example.com"},
                {"name": "No Email", "email": "invalid"},
            ]
        ),
    )

    job = client.get(response.json()["status_url"]).json()
    assert job["status"] == "completed_with_errors"
    assert job["succeeded"] == 1
    assert job["failed"] == 1
    assert job["recipients"][0]["status"] == "succeeded"
    assert job["recipients"][1]["status"] == "failed"
    assert "valid email" in job["recipients"][1]["error"]


def test_one_generation_error_does_not_prevent_other_certificates(client, monkeypatch):
    real_generator = main.generate_certificate

    def fail_one_recipient(recipient_name, course_name, completion_date, issuer):
        if recipient_name == "Broken Recipient":
            raise RuntimeError("simulated renderer failure")
        return real_generator(recipient_name, course_name, completion_date, issuer)

    monkeypatch.setattr(main, "generate_certificate", fail_one_recipient)
    response = client.post(
        "/jobs",
        json=request_body(
            [
                {"name": "Broken Recipient", "email": "broken@example.com"},
                {"name": "Successful Recipient", "email": "success@example.com"},
            ]
        ),
    )

    job = client.get(response.json()["status_url"]).json()
    assert job["status"] == "completed_with_errors"
    assert job["succeeded"] == 1
    assert job["failed"] == 1
    assert job["recipients"][0]["status"] == "failed"
    assert "simulated renderer failure" in job["recipients"][0]["error"]
    assert job["recipients"][1]["status"] == "succeeded"


def test_generated_certificate_can_be_downloaded(client):
    response = client.post(
        "/jobs",
        json=request_body([{"name": "Avery Stone", "email": "avery@example.com"}]),
    )
    job = client.get(response.json()["status_url"]).json()
    certificate_id = job["recipients"][0]["certificate_id"]

    download = client.get(f"/certificates/{certificate_id}/download")

    assert download.status_code == 200
    assert download.headers["content-type"] == "application/pdf"
    assert download.content.startswith(b"%PDF-")
    assert "Avery_Stone.pdf" in download.headers["content-disposition"]


def test_unknown_job_and_certificate_return_not_found(client):
    assert client.get("/jobs/missing").status_code == 404
    assert client.get("/certificates/999/download").status_code == 404


def test_empty_batch_is_rejected(client):
    response = client.post("/jobs", json=request_body([]))

    assert response.status_code == 422
