import re
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.certificates import generate_certificate
from app.database import connect, initialize_database


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    initialize_database()
    yield


app = FastAPI(
    title="Bulk Certificate Generator",
    description="Submit recipient batches, track generation, and download PDF certificates.",
    version="1.0.0",
    lifespan=lifespan,
)


class GenerationRequest(BaseModel):
    course_name: str = Field(..., min_length=1, max_length=200)
    completion_date: date
    issuer: str = Field(..., min_length=1, max_length=200)
    recipients: List[Any] = Field(..., min_length=1)

    @field_validator("completion_date", mode="before")
    @classmethod
    def validate_completion_date(cls, value: Any) -> date:
        if not isinstance(value, str):
            raise ValueError("must be an ISO date in YYYY-MM-DD format")
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise ValueError("must be an ISO date in YYYY-MM-DD format") from None

    @field_validator("course_name", "issuer")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class RecipientInput(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: str = Field(..., min_length=3, max_length=254)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("must be a valid email address")
        return value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validation_messages(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
        for issue in error.errors()
    )


def process_job(job_id: str) -> None:
    with connect() as connection:
        job = connection.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if job is None:
            return
        connection.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (job_id,))
        recipients = connection.execute(
            """
            SELECT id, recipient_name, recipient_email
            FROM certificates
            WHERE job_id = ? AND status = 'queued'
            ORDER BY id
            """,
            (job_id,),
        ).fetchall()

    for recipient in recipients:
        try:
            pdf = generate_certificate(
                recipient_name=recipient["recipient_name"],
                course_name=job["course_name"],
                completion_date=job["completion_date"],
                issuer=job["issuer"],
            )
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            with connect() as connection:
                connection.execute(
                    """
                    UPDATE certificates
                    SET status = 'failed', error = ?
                    WHERE id = ? AND job_id = ?
                    """,
                    (message, recipient["id"], job_id),
                )
                connection.execute(
                    "UPDATE jobs SET failed = failed + 1 WHERE id = ?", (job_id,)
                )
        else:
            with connect() as connection:
                connection.execute(
                    """
                    UPDATE certificates
                    SET status = 'succeeded', pdf = ?
                    WHERE id = ? AND job_id = ?
                    """,
                    (sqlite3.Binary(pdf), recipient["id"], job_id),
                )
                connection.execute(
                    "UPDATE jobs SET succeeded = succeeded + 1 WHERE id = ?", (job_id,)
                )

    with connect() as connection:
        counts = connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
            FROM certificates
            WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
        status = "completed_with_errors" if counts["failed"] else "completed"
        connection.execute(
            """
            UPDATE jobs
            SET status = ?, total = ?, succeeded = ?, failed = ?
            WHERE id = ?
            """,
            (
                status,
                counts["total"],
                counts["succeeded"] or 0,
                counts["failed"] or 0,
                job_id,
            ),
        )


@app.post("/jobs", status_code=202)
def create_job(
    request: GenerationRequest, background_tasks: BackgroundTasks
) -> Dict[str, Any]:
    job_id = str(uuid.uuid4())
    created_at = utc_now()
    initial_failed = 0

    with connect() as connection:
        connection.execute(
            """
            INSERT INTO jobs (
                id, course_name, completion_date, issuer, status,
                total, succeeded, failed, created_at
            ) VALUES (?, ?, ?, ?, 'queued', ?, 0, 0, ?)
            """,
            (
                job_id,
                request.course_name,
                request.completion_date.isoformat(),
                request.issuer,
                len(request.recipients),
                created_at,
            ),
        )
        for raw_recipient in request.recipients:
            error: Optional[str] = None
            if not isinstance(raw_recipient, dict):
                recipient_name = ""
                recipient_email = ""
                error = "recipient must be an object with name and email fields"
            else:
                try:
                    recipient = RecipientInput.model_validate(raw_recipient)
                    recipient_name = recipient.name
                    recipient_email = recipient.email
                except ValidationError as validation_error:
                    recipient_name = str(raw_recipient.get("name", "")).strip()
                    recipient_email = str(raw_recipient.get("email", "")).strip()
                    error = validation_messages(validation_error)

            status = "failed" if error else "queued"
            initial_failed += int(error is not None)
            connection.execute(
                """
                INSERT INTO certificates (
                    job_id, recipient_name, recipient_email, status, error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    recipient_name or "(unknown recipient)",
                    recipient_email,
                    status,
                    error,
                    created_at,
                ),
            )

        connection.execute(
            "UPDATE jobs SET failed = ? WHERE id = ?", (initial_failed, job_id)
        )

    background_tasks.add_task(process_job, job_id)
    return {
        "job_id": job_id,
        "status": "queued",
        "total": len(request.recipients),
        "succeeded": 0,
        "failed": initial_failed,
        "status_url": f"/jobs/{job_id}",
    }


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> Dict[str, Any]:
    with connect() as connection:
        job = connection.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if job is None:
            raise HTTPException(status_code=404, detail="Generation job not found")
        recipients = connection.execute(
            """
            SELECT id, recipient_name, recipient_email, status, error
            FROM certificates
            WHERE job_id = ?
            ORDER BY id
            """,
            (job_id,),
        ).fetchall()

    return {
        "job_id": job["id"],
        "status": job["status"],
        "total": job["total"],
        "succeeded": job["succeeded"],
        "failed": job["failed"],
        "created_at": job["created_at"],
        "recipients": [
            {
                "certificate_id": row["id"],
                "name": row["recipient_name"],
                "email": row["recipient_email"],
                "status": row["status"],
                "error": row["error"],
                "download_url": (
                    f"/certificates/{row['id']}/download"
                    if row["status"] == "succeeded"
                    else None
                ),
            }
            for row in recipients
        ],
    }


@app.get("/certificates/{certificate_id}/download")
def download_certificate(certificate_id: int) -> Response:
    with connect() as connection:
        certificate = connection.execute(
            "SELECT recipient_name, status, pdf FROM certificates WHERE id = ?",
            (certificate_id,),
        ).fetchone()

    if certificate is None:
        raise HTTPException(status_code=404, detail="Certificate not found")
    if certificate["status"] != "succeeded" or certificate["pdf"] is None:
        raise HTTPException(
            status_code=409,
            detail="Certificate is not available; check the generation job status",
        )

    filename = re.sub(r"[^A-Za-z0-9_-]+", "_", certificate["recipient_name"]).strip("_")
    filename = filename or "certificate"
    return Response(
        content=certificate["pdf"],
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'},
    )
