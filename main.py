import asyncio
import logging
import os
import secrets
import smtplib
import uuid
from email.message import EmailMessage
import aiofiles
import bcrypt
import jwt
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import BackgroundTasks, FastAPI, Depends, Header, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.requests import Request
from starlette.responses import Response
from fastapi.responses import FileResponse
from typing import Optional
from datetime import datetime
import math
import httpx

from sqlalchemy import or_
from database import init_db, get_db, ContactSubmission, CareerSubmission, AdmissionSubmission, AdminUser

UPLOAD_DIR = "uploads"
KIVA_DIR = Path(__file__).parent.parent / "kiva"
STATIC_DIR = KIVA_DIR / "dist"

JWT_SECRET = os.environ.get("KIVA_JWT_SECRET")
if not JWT_SECRET:
    JWT_SECRET = secrets.token_urlsafe(32)
    print("WARNING: KIVA_JWT_SECRET not set — using a random secret (tokens won't survive restarts)")

# Load .env file if present (so `fastapi dev` picks up config without manual sourcing)
from dotenv import load_dotenv
load_dotenv()

# SMTP config (all optional — if KIVA_NOTIFY_EMAIL is unset, no emails are sent)
SMTP_HOST = os.environ.get("KIVA_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("KIVA_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("KIVA_SMTP_USER", "")
SMTP_PASS = os.environ.get("KIVA_SMTP_PASS", "")
SMTP_FROM = os.environ.get("KIVA_SMTP_FROM", "") or SMTP_USER
NOTIFY_EMAIL = os.environ.get("KIVA_NOTIFY_EMAIL", "")
SITE_URL = os.environ.get("KIVA_SITE_URL", "http://localhost:8000").rstrip("/")

logger = logging.getLogger("kiva")


def send_notification_email(subject: str, submission_type: str, submission_id: int, summary: str):
    """Send an email notification with a link to the dashboard detail page. Runs in a background task."""
    if not NOTIFY_EMAIL or not SMTP_USER or not SMTP_PASS:
        return

    link = f"{SITE_URL}/dashboard/{submission_type}/{submission_id}"
    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = NOTIFY_EMAIL
    msg["Subject"] = subject
    msg.set_content(f"{summary}\n\nView in dashboard:\n{link}")

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
    except Exception:
        logger.exception("Failed to send notification email")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create tables and upload directory
    init_db()
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    yield


app = FastAPI(title="Kiva School Backend", lifespan=lifespan)

CORS_ORIGINS = os.environ.get("KIVA_CORS_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoginRequest(BaseModel):
    username: str
    password: str


def require_auth(authorization: Optional[str] = Header(None)) -> str:
    """Dependency that validates JWT and returns the username."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = authorization.removeprefix("Bearer ")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload["sub"]
    except (jwt.InvalidTokenError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid token")


def require_auth_or_query_token(
    request: Request, authorization: Optional[str] = Header(None)
) -> str:
    """Like require_auth but also accepts ?token= query param (for file download links)."""
    token = request.query_params.get("token")
    if not token:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing or invalid token")
        token = authorization.removeprefix("Bearer ")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload["sub"]
    except (jwt.InvalidTokenError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid token")


def row_to_dict(row) -> dict:
    """Convert SQLAlchemy model instance to dict with ISO datetime strings."""
    d = {}
    for c in row.__table__.columns:
        v = getattr(row, c.name)
        d[c.name] = v.isoformat() if isinstance(v, datetime) else v
    return d


def paginated_query(db: Session, model, page: int, per_page: int, sort: str, order: str, allowed_sorts: set, search_filter=None):
    """Run a paginated, sorted query and return {items, total, page, per_page, pages}."""
    per_page = min(max(per_page, 1), 100)
    page = max(page, 1)

    sort_col = sort if sort in allowed_sorts else "created_at"
    col = getattr(model, sort_col)
    order_clause = col.asc() if order == "asc" else col.desc()

    query = db.query(model)
    if search_filter is not None:
        query = query.filter(search_filter)

    total = query.count()
    items = query.order_by(order_clause).offset((page - 1) * per_page).limit(per_page).all()

    return {
        "items": [row_to_dict(r) for r in items],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": math.ceil(total / per_page) if total else 1,
    }


@app.post("/api/auth/login")
async def auth_login(body: LoginRequest, db: Session = Depends(get_db)):
    """Authenticate admin user and return a JWT."""
    user = db.query(AdminUser).filter(AdminUser.username == body.username).first()
    if not user or not bcrypt.checkpw(body.password.encode(), user.password_hash.encode()):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = jwt.encode({"sub": user.username}, JWT_SECRET, algorithm="HS256")
    return {"token": token}


@app.get("/api/auth/me")
async def auth_me(username: str = Depends(require_auth)):
    """Return the currently authenticated user."""
    return {"username": username}


async def save_upload_file(upload_file: UploadFile) -> str:
    """Save uploaded file and return the path."""
    ext = os.path.splitext(upload_file.filename)[1] if upload_file.filename else ""
    filename = f"{uuid.uuid4()}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    async with aiofiles.open(filepath, "wb") as f:
        content = await upload_file.read()
        await f.write(content)

    return filepath


@app.post("/api/contact")
async def submit_contact(
    background_tasks: BackgroundTasks,
    name: str = Form(...),
    email: str = Form(...),
    subject: Optional[str] = Form(None),
    phone: Optional[str] = Form(None),
    message: str = Form(...),
    db: Session = Depends(get_db),
):
    """Handle contact form submission."""
    submission = ContactSubmission(
        name=name,
        email=email,
        subject=subject,
        phone=phone,
        message=message,
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)

    background_tasks.add_task(
        send_notification_email,
        f"[Contact Form] {subject or 'New submission'} — {name}",
        "contacts", submission.id, f"New contact from {name} ({email})",
    )

    return {"success": True, "id": submission.id}


@app.post("/api/careers")
async def submit_career(
    background_tasks: BackgroundTasks,
    name: str = Form(...),
    email: str = Form(...),
    phone: Optional[str] = Form(None),
    position: Optional[str] = Form(None),
    coverLetter: Optional[str] = Form(None),
    cv: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    """Handle career application submission."""
    cv_path = None
    if cv and cv.filename:
        cv_path = await save_upload_file(cv)

    submission = CareerSubmission(
        name=name,
        email=email,
        phone=phone,
        position=position,
        cover_letter=coverLetter,
        cv_path=cv_path,
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)

    background_tasks.add_task(
        send_notification_email,
        f"[Career Application] {position or 'New application'} — {name}",
        "careers", submission.id, f"New career application from {name} ({email})",
    )

    return {"success": True, "id": submission.id}


@app.post("/api/admission")
async def submit_admission(
    background_tasks: BackgroundTasks,
    # Session
    session: str = Form(...),
    # Child's Information
    childName: str = Form(...),
    dob: str = Form(...),
    address: str = Form(...),
    appliedBefore: str = Form(...),
    previousSchool: Optional[str] = Form(None),
    previousClass: Optional[str] = Form(None),
    hasReport: Optional[str] = Form(None),
    reason: Optional[str] = Form(None),
    medicalInfo: Optional[str] = Form(None),
    specialNeeds: str = Form(...),
    # Mother's Details
    motherName: str = Form(...),
    motherProfession: Optional[str] = Form(None),
    motherEducation: Optional[str] = Form(None),
    motherOrganization: Optional[str] = Form(None),
    motherEmail: Optional[str] = Form(None),
    motherPhone: Optional[str] = Form(None),
    motherCnic: Optional[str] = Form(None),
    # Father's Details
    fatherName: str = Form(...),
    fatherProfession: Optional[str] = Form(None),
    fatherEducation: Optional[str] = Form(None),
    fatherOrganization: Optional[str] = Form(None),
    fatherEmail: Optional[str] = Form(None),
    fatherPhone: Optional[str] = Form(None),
    fatherCnic: Optional[str] = Form(None),
    # Sibling Information
    siblingName: Optional[str] = Form(None),
    siblingGrade: Optional[str] = Form(None),
    siblingSchool: Optional[str] = Form(None),
    # Emergency Contact
    emergencyName: str = Form(...),
    emergencyPhone: str = Form(...),
    # How did you hear about us
    hearAbout: Optional[list[str]] = Form(None),
    # Why Kiva is a good fit
    fitResponse: Optional[str] = Form(None),
    # Declaration
    declaration: bool = Form(...),
    signature: str = Form(...),
    # File upload
    progressReport: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    """Handle admission form submission."""
    progress_report_path = None
    if progressReport and progressReport.filename:
        progress_report_path = await save_upload_file(progressReport)

    # Convert list to comma-separated string for storage
    hear_about_str = ",".join(hearAbout) if hearAbout else None

    submission = AdmissionSubmission(
        session=session,
        child_name=childName,
        dob=dob,
        address=address,
        applied_before=appliedBefore,
        previous_school=previousSchool,
        previous_class=previousClass,
        has_report=hasReport,
        progress_report_path=progress_report_path,
        reason=reason,
        medical_info=medicalInfo,
        special_needs=specialNeeds,
        mother_name=motherName,
        mother_profession=motherProfession,
        mother_education=motherEducation,
        mother_organization=motherOrganization,
        mother_email=motherEmail,
        mother_phone=motherPhone,
        mother_cnic=motherCnic,
        father_name=fatherName,
        father_profession=fatherProfession,
        father_education=fatherEducation,
        father_organization=fatherOrganization,
        father_email=fatherEmail,
        father_phone=fatherPhone,
        father_cnic=fatherCnic,
        sibling_name=siblingName,
        sibling_grade=siblingGrade,
        sibling_school=siblingSchool,
        emergency_name=emergencyName,
        emergency_phone=emergencyPhone,
        hear_about=hear_about_str,
        fit_response=fitResponse,
        declaration=declaration,
        signature=signature,
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)

    background_tasks.add_task(
        send_notification_email,
        f"[Admission Form] {childName} — {session}",
        "admissions", submission.id, f"New admission application for {childName} ({session})",
    )

    return {"success": True, "id": submission.id}


_rebuild_lock = asyncio.Lock()


@app.post("/api/rebuild")
async def rebuild_site(username: str = Depends(require_auth)):
    """Rebuild the Astro static site from current CMS content. Requires auth."""
    if _rebuild_lock.locked():
        return {"success": False, "error": "A rebuild is already in progress"}

    async with _rebuild_lock:
        proc = await asyncio.create_subprocess_exec(
            "npx", "astro", "build",
            cwd=KIVA_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            return {
                "success": False,
                "error": stderr.decode().strip()[-500:],
            }

        return {"success": True}


TINA_GRAPHQL_URL = os.environ.get("TINA_GRAPHQL_URL", "http://localhost:4001/graphql")


@app.api_route("/graphql", methods=["GET", "POST"])
async def graphql_proxy(request: Request):
    """Proxy GraphQL requests to the TinaCMS content API."""
    async with httpx.AsyncClient() as client:
        response = await client.request(
            method=request.method,
            url=TINA_GRAPHQL_URL,
            headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
            content=await request.body(),
        )
        excluded = {"transfer-encoding", "content-encoding", "content-length"}
        headers = {k: v for k, v in response.headers.items() if k.lower() not in excluded}
        return Response(content=response.content, status_code=response.status_code, headers=headers)


# ── Submissions API (read-only, auth required) ──────────────────────────────

@app.get("/api/submissions/contacts")
async def list_contacts(
    page: int = 1,
    per_page: int = 25,
    sort: str = "created_at",
    order: str = "desc",
    search: str = "",
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    search_filter = None
    if search:
        pattern = f"%{search}%"
        search_filter = or_(
            ContactSubmission.name.ilike(pattern),
            ContactSubmission.email.ilike(pattern),
            ContactSubmission.subject.ilike(pattern),
        )
    return paginated_query(
        db, ContactSubmission, page, per_page, sort, order,
        {"id", "name", "email", "subject", "created_at"},
        search_filter,
    )


@app.get("/api/submissions/careers")
async def list_careers(
    page: int = 1,
    per_page: int = 25,
    sort: str = "created_at",
    order: str = "desc",
    search: str = "",
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    search_filter = None
    if search:
        pattern = f"%{search}%"
        search_filter = or_(
            CareerSubmission.name.ilike(pattern),
            CareerSubmission.email.ilike(pattern),
            CareerSubmission.position.ilike(pattern),
        )
    result = paginated_query(
        db, CareerSubmission, page, per_page, sort, order,
        {"id", "name", "email", "position", "created_at"},
        search_filter,
    )
    # Replace cv_path with download URL
    for item in result["items"]:
        if item.get("cv_path"):
            item["cv_url"] = f"/api/submissions/careers/{item['id']}/cv"
            del item["cv_path"]
        else:
            item["cv_url"] = None
            item.pop("cv_path", None)
    return result


@app.get("/api/submissions/admissions")
async def list_admissions(
    page: int = 1,
    per_page: int = 25,
    sort: str = "created_at",
    order: str = "desc",
    search: str = "",
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    search_filter = None
    if search:
        pattern = f"%{search}%"
        search_filter = or_(
            AdmissionSubmission.child_name.ilike(pattern),
            AdmissionSubmission.mother_name.ilike(pattern),
            AdmissionSubmission.father_name.ilike(pattern),
        )
    result = paginated_query(
        db, AdmissionSubmission, page, per_page, sort, order,
        {"id", "child_name", "session", "created_at"},
        search_filter,
    )
    # Return summary fields only for the list view
    summary_keys = {"id", "session", "child_name", "dob", "mother_name", "father_name", "mother_phone", "father_phone", "created_at"}
    result["items"] = [{k: v for k, v in item.items() if k in summary_keys} for item in result["items"]]
    return result


@app.get("/api/submissions/admissions/{submission_id}")
async def get_admission(
    submission_id: int,
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    row = db.query(AdmissionSubmission).filter(AdmissionSubmission.id == submission_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Submission not found")
    data = row_to_dict(row)
    if data.get("progress_report_path"):
        data["progress_report_url"] = f"/api/submissions/admissions/{submission_id}/progress-report"
        del data["progress_report_path"]
    else:
        data["progress_report_url"] = None
        data.pop("progress_report_path", None)
    return data


@app.get("/api/submissions/contacts/{submission_id}")
async def get_contact(
    submission_id: int,
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    row = db.query(ContactSubmission).filter(ContactSubmission.id == submission_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Submission not found")
    return row_to_dict(row)


@app.get("/api/submissions/careers/{submission_id}", response_model=None)
async def get_career(
    submission_id: int,
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    row = db.query(CareerSubmission).filter(CareerSubmission.id == submission_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Submission not found")
    data = row_to_dict(row)
    if data.get("cv_path"):
        data["cv_url"] = f"/api/submissions/careers/{submission_id}/cv"
        del data["cv_path"]
    else:
        data["cv_url"] = None
        data.pop("cv_path", None)
    return data


@app.get("/api/submissions/careers/{submission_id}/cv")
async def download_career_cv(
    submission_id: int,
    username: str = Depends(require_auth_or_query_token),
    db: Session = Depends(get_db),
):
    row = db.query(CareerSubmission).filter(CareerSubmission.id == submission_id).first()
    if not row or not row.cv_path:
        raise HTTPException(status_code=404, detail="CV not found")
    if not os.path.exists(row.cv_path):
        raise HTTPException(status_code=404, detail="File not found on disk")
    return FileResponse(row.cv_path, filename=f"cv_{submission_id}{os.path.splitext(row.cv_path)[1]}")


@app.get("/api/submissions/admissions/{submission_id}/progress-report")
async def download_progress_report(
    submission_id: int,
    username: str = Depends(require_auth_or_query_token),
    db: Session = Depends(get_db),
):
    row = db.query(AdmissionSubmission).filter(AdmissionSubmission.id == submission_id).first()
    if not row or not row.progress_report_path:
        raise HTTPException(status_code=404, detail="Progress report not found")
    if not os.path.exists(row.progress_report_path):
        raise HTTPException(status_code=404, detail="File not found on disk")
    return FileResponse(row.progress_report_path, filename=f"report_{submission_id}{os.path.splitext(row.progress_report_path)[1]}")


# SPA catch-all: serve dashboard/index.html for any /dashboard/* sub-path
@app.get("/dashboard/{rest:path}")
async def dashboard_spa(rest: str):
    return FileResponse(os.path.join(STATIC_DIR, "dashboard", "index.html"))


# TinaCMS admin: proxy all /admin/* requests to the TinaCMS Vite dev server
TINA_DEV_URL = os.environ.get("TINA_DEV_URL", "http://localhost:4001")


@app.api_route("/admin/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
@app.api_route("/admin", methods=["GET"])
async def admin_proxy(request: Request, path: str = ""):
    """Proxy TinaCMS admin UI requests to the Vite dev server."""
    url = f"{TINA_DEV_URL}/admin/{path}"
    if request.url.query:
        url += f"?{request.url.query}"
    async with httpx.AsyncClient() as client:
        response = await client.request(
            method=request.method,
            url=url,
            headers={k: v for k, v in request.headers.items() if k.lower() not in {"host", "connection"}},
            content=await request.body(),
        )
        excluded = {"transfer-encoding", "content-encoding", "content-length"}
        headers = {k: v for k, v in response.headers.items() if k.lower() not in excluded}
        return Response(content=response.content, status_code=response.status_code, headers=headers)


# Serve Astro static build (must be last - catches all routes)
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
