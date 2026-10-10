# Bulk Certificate Generator

A FastAPI service that accepts a batch of recipients, creates a PDF certificate for each valid recipient, reports per-recipient outcomes, and provides certificate downloads. The certificate design is a single fixed landscape template.

## Requirements

- Python 3.10+
- pip

## Set up and run

From the project directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

The service creates a SQLite database named `certificates.db` in the current directory on startup. Set `CERTIFICATE_DB_PATH` to change its location, for example:

```powershell
$env:CERTIFICATE_DB_PATH = "data\certificates.db"
uvicorn app.main:app --reload
```

Interactive API documentation is available at <http://127.0.0.1:8000/docs>.

## Submit a generation request

Send one `POST /jobs` request with shared certificate information and a list of recipients:

```powershell
$body = @{
  course_name = "Practical Data Science"
  completion_date = "2026-09-30"
  issuer = "Example Learning"
  recipients = @(
    @{ name = "Avery Stone"; email = "avery@example.com" },
    @{ name = "Jordan Lee"; email = "jordan@example.com" }
  )
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/jobs `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

The API returns HTTP `202 Accepted` and a job ID. Invalid recipient records are stored as failed results in that job rather than rejecting valid records in the same batch. Missing/invalid batch-level fields (course, date, issuer) and an empty recipient list are rejected with HTTP `422`.

## Track progress and retrieve certificates

Poll the returned `status_url` (or call `GET /jobs/{job_id}`) to see the job status and each recipient's status. Job states are `queued`, `running`, `completed`, and `completed_with_errors`. The response includes success/failure counts and a `download_url` for each successful certificate.

Download an individual PDF using its URL:

```powershell
Invoke-WebRequest `
  -Uri http://127.0.0.1:8000/certificates/1/download `
  -OutFile certificate.pdf
```

Downloads return `409 Conflict` until the certificate has been generated. Unknown job and certificate IDs return `404`.

## Run tests

```powershell
python -m pytest
```

The tests cover job submission, request and per-recipient validation, status/progress, isolated generation failures, PDF generation/download, and not-found behavior. Each test uses a temporary SQLite database.

## Design decisions

- **FastAPI and SQLite:** provide a small, dependency-light REST API and durable relational storage for jobs, recipient outcomes, and generated PDFs.
- **Background processing:** the API creates a persisted job and returns `202` before generation. FastAPI background tasks then process each recipient in sequence; clients poll job status. This avoids keeping the submission request open, but is intentionally a single-process solution. For sustained high-volume production workloads, move `process_job` to a durable worker queue (for example Celery/RQ) and use multiple workers.
- **Per-recipient isolation:** validation failures are recorded individually before processing. A rendering exception is recorded against that recipient, and the worker continues with remaining recipients.
- **One predefined PDF template:** ReportLab creates landscape PDF certificates in memory. The PDF bytes are stored with each certificate so downloads remain available across application restarts without a separate file store.
- **Validation boundary:** shared certificate data is validated at request time; each recipient's name and email are validated independently and reported in the job result.
