import asyncio
import logging
import os
import secrets
import uuid

import resend
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
from datetime import datetime, timezone
import math
import httpx

from sqlalchemy import or_
from database import init_db, get_db, ContactSubmission, CareerSubmission, AdmissionSubmission, AdmissionProgress, KivaKampSubmission, AdminUser

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

# Resend config (all optional — if KIVA_NOTIFY_EMAIL is unset, no emails are sent)
RESEND_API_KEY = os.environ.get("KIVA_RESEND_API_KEY", "")
EMAIL_FROM = os.environ.get("KIVA_EMAIL_FROM", "Kiva School <noreply@notifications.kiva.school>")
NOTIFY_EMAIL = os.environ.get("KIVA_NOTIFY_EMAIL", "")
SITE_URL = os.environ.get("KIVA_SITE_URL", "http://localhost:8000").rstrip("/")

logger = logging.getLogger("kiva")


def send_notification_email(subject: str, submission_type: str, submission_id: int, summary: str):
    """Send an email notification with a link to the dashboard detail page. Runs in a background task."""
    if not NOTIFY_EMAIL or not RESEND_API_KEY:
        return

    resend.api_key = RESEND_API_KEY
    link = f"{SITE_URL}/dashboard/{submission_type}/{submission_id}"

    try:
        resend.Emails.send({
            "from": EMAIL_FROM,
            "to": [NOTIFY_EMAIL],
            "subject": subject,
            "text": f"{summary}\n\nView in dashboard:\n{link}",
        })
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


class ContactUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    subject: Optional[str] = None
    phone: Optional[str] = None
    message: Optional[str] = None


class CareerUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    position: Optional[str] = None
    cover_letter: Optional[str] = None


class AdmissionUpdate(BaseModel):
    session: Optional[str] = None
    child_name: Optional[str] = None
    dob: Optional[str] = None
    address: Optional[str] = None
    applied_before: Optional[str] = None
    previous_school: Optional[str] = None
    previous_class: Optional[str] = None
    has_report: Optional[str] = None
    reason: Optional[str] = None
    medical_info: Optional[str] = None
    special_needs: Optional[str] = None
    mother_name: Optional[str] = None
    mother_profession: Optional[str] = None
    mother_education: Optional[str] = None
    mother_organization: Optional[str] = None
    mother_email: Optional[str] = None
    mother_phone: Optional[str] = None
    mother_cnic: Optional[str] = None
    father_name: Optional[str] = None
    father_profession: Optional[str] = None
    father_education: Optional[str] = None
    father_organization: Optional[str] = None
    father_email: Optional[str] = None
    father_phone: Optional[str] = None
    father_cnic: Optional[str] = None
    sibling_name: Optional[str] = None
    sibling_grade: Optional[str] = None
    sibling_school: Optional[str] = None
    emergency_name: Optional[str] = None
    emergency_phone: Optional[str] = None
    hear_about: Optional[str] = None
    fit_response: Optional[str] = None
    declaration: Optional[bool] = None
    signature: Optional[str] = None


class KivaKampUpdate(BaseModel):
    name: Optional[str] = None
    child_class: Optional[str] = None
    age: Optional[str] = None
    school_name: Optional[str] = None
    father_name: Optional[str] = None
    mother_name: Optional[str] = None
    father_contact: Optional[str] = None
    mother_contact: Optional[str] = None
    attended_past: Optional[str] = None
    sibling: Optional[str] = None
    group_registration: Optional[str] = None
    referral: Optional[str] = None


class ProgressUpdate(BaseModel):
    child_name: Optional[str] = None
    father_name: Optional[str] = None
    father_phone: Optional[str] = None
    mother_name: Optional[str] = None
    mother_phone: Optional[str] = None
    date_of_facilitation: Optional[str] = None
    class_name: Optional[str] = None
    form_status: Optional[str] = None
    affiliation: Optional[str] = None
    interview_applicable: Optional[str] = None
    parent_status: Optional[str] = None
    first_call_interview_assessment: Optional[str] = None
    second_call_interview_assessment: Optional[str] = None
    acceptance: Optional[str] = None
    send_confirmation_date: Optional[str] = None
    due_date_for_payment: Optional[str] = None
    follow_up: Optional[str] = None
    follow_up_2: Optional[str] = None
    follow_up_3: Optional[str] = None
    status: Optional[str] = None
    remarks: Optional[str] = None
    session: Optional[str] = None


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


def paginated_query(db: Session, model, page: int, per_page: int, sort: str, order: str, allowed_sorts: set, search_filter=None, date_from: str = "", date_to: str = ""):
    """Run a paginated, sorted query and return {items, total, page, per_page, pages}."""
    per_page = min(max(per_page, 1), 100)
    page = max(page, 1)

    sort_col = sort if sort in allowed_sorts else "created_at"
    col = getattr(model, sort_col)
    order_clause = col.asc() if order == "asc" else col.desc()

    query = db.query(model)
    if search_filter is not None:
        query = query.filter(search_filter)
    if date_from:
        try:
            query = query.filter(model.created_at >= datetime.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            # Include the entire end date by adding one day
            end = datetime.fromisoformat(date_to)
            end = end.replace(hour=23, minute=59, second=59)
            query = query.filter(model.created_at <= end)
        except ValueError:
            pass

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


@app.post("/api/kiva-kamps")
async def submit_kiva_kamp(
    background_tasks: BackgroundTasks,
    name: str = Form(...),
    child_class: str = Form(..., alias="class"),
    age: str = Form(...),
    schoolName: str = Form(...),
    fatherName: str = Form(...),
    motherName: str = Form(...),
    fatherContact: str = Form(...),
    motherContact: str = Form(...),
    attendedPast: str = Form(...),
    sibling: str = Form(...),
    group_registration: str = Form(..., alias="group"),
    referral: str = Form(...),
    db: Session = Depends(get_db),
):
    """Handle Kiva Kamps registration submission."""
    submission = KivaKampSubmission(
        name=name,
        child_class=child_class,
        age=age,
        school_name=schoolName,
        father_name=fatherName,
        mother_name=motherName,
        father_contact=fatherContact,
        mother_contact=motherContact,
        attended_past=attendedPast,
        sibling=sibling,
        group_registration=group_registration,
        referral=referral,
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)

    background_tasks.add_task(
        send_notification_email,
        f"[Kiva Kamps] Registration — {name}",
        "kamps", submission.id, f"New Kiva Kamps registration for {name} (class {child_class}, age {age})",
    )

    return {"success": True, "id": submission.id}


_rebuild_lock = asyncio.Lock()


@app.post("/api/rebuild")
async def rebuild_site(username: str = Depends(require_auth)):
    """Rebuild the Astro static site from current CMS content. Requires auth."""
    if _rebuild_lock.locked():
        return {"success": False, "error": "A rebuild is already in progress"}

    async with _rebuild_lock:
        # Clean dist/ and Astro caches so deleted pages don't linger
        import shutil
        for d in (KIVA_DIR / "dist", KIVA_DIR / ".astro", KIVA_DIR / "node_modules" / ".astro"):
            if d.exists():
                shutil.rmtree(d)

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

        # Restore production admin UI (astro build copies dev HTML from
        # public/admin/ which has localhost:4001 refs — overwrite with the
        # production build saved by start-production.sh).
        admin_src = KIVA_DIR / ".admin-production"
        admin_dest = KIVA_DIR / "dist" / "admin"
        if admin_src.exists():
            if admin_dest.exists():
                shutil.rmtree(admin_dest)
            shutil.copytree(admin_src, admin_dest)

        return {"success": True}


# ── Media API (for TinaCMS media manager) ────────────────────────────────────

MEDIA_DIR = KIVA_DIR / "public" / "images"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".avif", ".ico"}


@app.get("/api/media")
async def media_list(directory: str = "", limit: int = 36, offset: int = 0):
    """List files and directories under public/images/."""
    base = MEDIA_DIR / directory.strip("/")
    if not base.exists() or not base.is_dir():
        return {"items": [], "nextOffset": None}

    entries = sorted(base.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    items = []
    for entry in entries:
        rel = entry.relative_to(MEDIA_DIR)
        if entry.is_dir():
            items.append({
                "type": "dir",
                "id": str(rel),
                "filename": entry.name,
                "directory": directory.strip("/"),
            })
        elif entry.suffix.lower() in IMAGE_EXTENSIONS:
            items.append({
                "type": "file",
                "id": str(rel),
                "filename": entry.name,
                "directory": directory.strip("/"),
                "src": f"/images/{rel}",
            })

    total = len(items)
    page = items[offset : offset + limit]
    next_offset = offset + limit if offset + limit < total else None
    return {"items": page, "nextOffset": next_offset}


@app.post("/api/media")
async def media_upload(
    file: UploadFile = File(...),
    directory: str = Form(""),
):
    """Upload an image to public/images/."""
    target_dir = MEDIA_DIR / directory.strip("/")
    target_dir.mkdir(parents=True, exist_ok=True)

    filename = file.filename or "upload"
    dest = target_dir / filename
    # Avoid overwriting: append a number if needed
    counter = 1
    stem = dest.stem
    suffix = dest.suffix
    while dest.exists():
        dest = target_dir / f"{stem}-{counter}{suffix}"
        counter += 1

    async with aiofiles.open(dest, "wb") as f:
        content = await file.read()
        await f.write(content)

    # Generate optimized WebP version alongside the original
    if suffix.lower() in {".png", ".jpg", ".jpeg"}:
        from PIL import Image as PILImage

        with PILImage.open(dest) as pil_img:
            webp_dest = dest.with_suffix(".webp")
            img = pil_img
            if img.width > 1440:
                img = img.resize(
                    (1440, int(img.height * 1440 / img.width)),
                    PILImage.LANCZOS,
                )
            img.save(webp_dest, "WEBP", quality=80)

    rel = dest.relative_to(MEDIA_DIR)
    return {
        "type": "file",
        "id": str(rel),
        "filename": dest.name,
        "directory": directory.strip("/"),
        "src": f"/images/{rel}",
    }


@app.delete("/api/media")
async def media_delete(directory: str = "", filename: str = ""):
    """Delete an image from public/images/."""
    if not filename:
        raise HTTPException(status_code=400, detail="filename is required")
    target = MEDIA_DIR / directory.strip("/") / filename
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    target.unlink()
    # Also remove WebP sibling if it exists
    webp_sibling = target.with_suffix(".webp")
    if webp_sibling.exists():
        webp_sibling.unlink()
    return {"success": True}


TINA_GRAPHQL_URL = os.environ.get("TINA_GRAPHQL_URL", "http://localhost:4001/graphql")


# ── Instagram feed proxy ────────────────────────────────────────────────────
# Instagram Graph API (Instagram Login flow). The token belongs to the
# Kiva School IG account and is rotated manually every ~60 days. We cache
# responses for 10 minutes so we don't hammer the API on each page load.
IG_ACCESS_TOKEN = os.environ.get("KIVA_IG_ACCESS_TOKEN", "")
IG_GRAPH_BASE = "https://graph.instagram.com/v21.0"
IG_CACHE_TTL = 600  # seconds
_ig_cache: dict[int, tuple[float, dict]] = {}


@app.get("/api/instagram/media")
async def instagram_media(limit: int = 9):
    """Return the IG account's recent posts plus profile metadata for the feed widget."""
    limit = max(1, min(limit, 25))
    if not IG_ACCESS_TOKEN:
        return {"profile": None, "posts": []}

    now = datetime.now(timezone.utc).timestamp()
    cached = _ig_cache.get(limit)
    if cached and now - cached[0] < IG_CACHE_TTL:
        return cached[1]

    media_fields = "id,caption,media_type,media_url,permalink,thumbnail_url,timestamp,children{id}"
    profile_fields = "id,username,profile_picture_url,media_count"
    # Over-fetch so we can drop duplicate-caption posts (the account often
    # publishes the same announcement as both a Reel and a feed post) and
    # still return `limit` items.
    fetch_limit = min(limit * 2, 25)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            profile_resp, media_resp = await asyncio.gather(
                client.get(
                    f"{IG_GRAPH_BASE}/me",
                    params={"fields": profile_fields, "access_token": IG_ACCESS_TOKEN},
                ),
                client.get(
                    f"{IG_GRAPH_BASE}/me/media",
                    params={"fields": media_fields, "limit": fetch_limit, "access_token": IG_ACCESS_TOKEN},
                ),
            )
    except httpx.HTTPError:
        logger.exception("Instagram API request failed")
        return {"profile": None, "posts": []}

    if profile_resp.status_code != 200 or media_resp.status_code != 200:
        logger.warning(
            "Instagram API non-200: profile=%s media=%s",
            profile_resp.status_code, media_resp.status_code,
        )
        return {"profile": None, "posts": []}

    profile_raw = profile_resp.json()
    media_raw = media_resp.json().get("data", [])

    seen_captions: set[str] = set()
    posts = []
    for item in media_raw:
        caption = (item.get("caption") or "").strip()
        # Use the first 120 chars (case-insensitive, whitespace-collapsed) as
        # the dedupe key. Empty captions are never deduped.
        if caption:
            key = " ".join(caption.split()).lower()[:120]
            if key in seen_captions:
                continue
            seen_captions.add(key)

        media_type = item.get("media_type")
        thumbnail = item.get("thumbnail_url") if media_type == "VIDEO" else item.get("media_url")
        children = (item.get("children") or {}).get("data") or []
        posts.append({
            "id": item.get("id"),
            "permalink": item.get("permalink"),
            "thumbnail": thumbnail,
            "caption": item.get("caption"),
            "media_type": media_type,
            "timestamp": item.get("timestamp"),
            "children_count": len(children) if media_type == "CAROUSEL_ALBUM" else 0,
        })
        if len(posts) >= limit:
            break

    payload = {
        "profile": {
            "username": profile_raw.get("username"),
            "profile_picture_url": profile_raw.get("profile_picture_url"),
            "media_count": profile_raw.get("media_count"),
        },
        "posts": posts,
    }
    _ig_cache[limit] = (now, payload)
    return payload


@app.api_route("/graphql", methods=["GET", "POST"])
async def graphql_proxy(request: Request):
    """Proxy GraphQL requests to the TinaCMS content API."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.request(
                method=request.method,
                url=TINA_GRAPHQL_URL,
                headers={k: v for k, v in request.headers.items()
                         if k.lower() not in {"host", "connection"}},
                content=await request.body(),
            )
            excluded = {"transfer-encoding", "content-encoding", "content-length"}
            headers = {k: v for k, v in response.headers.items() if k.lower() not in excluded}
            return Response(content=response.content, status_code=response.status_code, headers=headers)
    except (httpx.ConnectError, httpx.TimeoutException):
        return Response(
            content='{"errors":[{"message":"CMS content API is not running"}]}',
            status_code=503,
            media_type="application/json",
        )


# ── Submissions API (read-only, auth required) ──────────────────────────────

def _filter_by_date_range(query, model, date_from: str, date_to: str):
    if date_from:
        try:
            query = query.filter(model.created_at >= datetime.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            end = datetime.fromisoformat(date_to).replace(hour=23, minute=59, second=59)
            query = query.filter(model.created_at <= end)
        except ValueError:
            pass
    return query


def _rows_to_csv(rows, columns: list[str], filename: str) -> Response:
    import csv
    import io
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for row in rows:
        data = row_to_dict(row)
        writer.writerow([data.get(c, "") if data.get(c) is not None else "" for c in columns])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


CONTACT_EXPORT_COLUMNS = ["id", "created_at", "name", "email", "phone", "subject", "message"]
CAREER_EXPORT_COLUMNS = ["id", "created_at", "name", "email", "phone", "position", "cover_letter", "cv_path"]
ADMISSION_EXPORT_COLUMNS = [
    "id", "created_at", "session", "child_name", "dob", "address",
    "applied_before", "previous_school", "previous_class", "has_report",
    "progress_report_path", "reason", "medical_info", "special_needs",
    "mother_name", "mother_profession", "mother_education", "mother_organization",
    "mother_email", "mother_phone", "mother_cnic",
    "father_name", "father_profession", "father_education", "father_organization",
    "father_email", "father_phone", "father_cnic",
    "sibling_name", "sibling_grade", "sibling_school",
    "emergency_name", "emergency_phone",
    "hear_about", "fit_response", "declaration", "signature",
]
KIVA_KAMP_EXPORT_COLUMNS = [
    "id", "created_at", "name", "child_class", "age", "school_name",
    "father_name", "mother_name", "father_contact", "mother_contact",
    "attended_past", "sibling", "group_registration", "referral",
]


@app.get("/api/submissions/contacts/export")
async def export_contacts_csv(
    date_from: str = "",
    date_to: str = "",
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    query = _filter_by_date_range(db.query(ContactSubmission), ContactSubmission, date_from, date_to)
    rows = query.order_by(ContactSubmission.created_at.desc()).all()
    return _rows_to_csv(rows, CONTACT_EXPORT_COLUMNS, "contacts-export.csv")


@app.get("/api/submissions/careers/export")
async def export_careers_csv(
    date_from: str = "",
    date_to: str = "",
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    query = _filter_by_date_range(db.query(CareerSubmission), CareerSubmission, date_from, date_to)
    rows = query.order_by(CareerSubmission.created_at.desc()).all()
    return _rows_to_csv(rows, CAREER_EXPORT_COLUMNS, "careers-export.csv")


@app.get("/api/submissions/admissions/export")
async def export_admissions_csv(
    date_from: str = "",
    date_to: str = "",
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    query = _filter_by_date_range(db.query(AdmissionSubmission), AdmissionSubmission, date_from, date_to)
    rows = query.order_by(AdmissionSubmission.created_at.desc()).all()
    return _rows_to_csv(rows, ADMISSION_EXPORT_COLUMNS, "admissions-export.csv")


@app.get("/api/submissions/kiva-kamps/export")
async def export_kiva_kamps_csv(
    date_from: str = "",
    date_to: str = "",
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    query = _filter_by_date_range(db.query(KivaKampSubmission), KivaKampSubmission, date_from, date_to)
    rows = query.order_by(KivaKampSubmission.created_at.desc()).all()
    return _rows_to_csv(rows, KIVA_KAMP_EXPORT_COLUMNS, "kiva-kamps-export.csv")


@app.get("/api/submissions/contacts")
async def list_contacts(
    page: int = 1,
    per_page: int = 25,
    sort: str = "created_at",
    order: str = "desc",
    search: str = "",
    date_from: str = "",
    date_to: str = "",
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
        search_filter, date_from, date_to,
    )


@app.get("/api/submissions/careers")
async def list_careers(
    page: int = 1,
    per_page: int = 25,
    sort: str = "created_at",
    order: str = "desc",
    search: str = "",
    date_from: str = "",
    date_to: str = "",
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
        search_filter, date_from, date_to,
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
    date_from: str = "",
    date_to: str = "",
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
        search_filter, date_from, date_to,
    )
    # Return summary fields only for the list view
    summary_keys = {"id", "session", "child_name", "dob", "mother_name", "father_name", "mother_phone", "father_phone", "created_at"}
    result["items"] = [{k: v for k, v in item.items() if k in summary_keys} for item in result["items"]]
    return result


@app.get("/api/submissions/kiva-kamps")
async def list_kiva_kamps(
    page: int = 1,
    per_page: int = 25,
    sort: str = "created_at",
    order: str = "desc",
    search: str = "",
    date_from: str = "",
    date_to: str = "",
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    search_filter = None
    if search:
        pattern = f"%{search}%"
        search_filter = or_(
            KivaKampSubmission.name.ilike(pattern),
            KivaKampSubmission.school_name.ilike(pattern),
            KivaKampSubmission.father_name.ilike(pattern),
            KivaKampSubmission.mother_name.ilike(pattern),
        )
    return paginated_query(
        db, KivaKampSubmission, page, per_page, sort, order,
        {"id", "name", "school_name", "age", "created_at"},
        search_filter, date_from, date_to,
    )


@app.get("/api/submissions/kiva-kamps/{submission_id}")
async def get_kiva_kamp(
    submission_id: int,
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    row = db.query(KivaKampSubmission).filter(KivaKampSubmission.id == submission_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Submission not found")
    return row_to_dict(row)


@app.put("/api/submissions/kiva-kamps/{submission_id}")
async def update_kiva_kamp(
    submission_id: int,
    body: KivaKampUpdate,
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    row = db.query(KivaKampSubmission).filter(KivaKampSubmission.id == submission_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Submission not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row_to_dict(row)


@app.delete("/api/submissions/kiva-kamps/{submission_id}")
async def delete_kiva_kamp(
    submission_id: int,
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    row = db.query(KivaKampSubmission).filter(KivaKampSubmission.id == submission_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Submission not found")
    db.delete(row)
    db.commit()
    return {"success": True}


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


@app.put("/api/submissions/admissions/{submission_id}")
async def update_admission(
    submission_id: int,
    body: AdmissionUpdate,
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    row = db.query(AdmissionSubmission).filter(AdmissionSubmission.id == submission_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Submission not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    data = row_to_dict(row)
    if data.get("progress_report_path"):
        data["progress_report_url"] = f"/api/submissions/admissions/{submission_id}/progress-report"
        del data["progress_report_path"]
    else:
        data["progress_report_url"] = None
        data.pop("progress_report_path", None)
    return data


@app.delete("/api/submissions/admissions/{submission_id}")
async def delete_admission(
    submission_id: int,
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    row = db.query(AdmissionSubmission).filter(AdmissionSubmission.id == submission_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Submission not found")
    if row.progress_report_path and os.path.exists(row.progress_report_path):
        os.remove(row.progress_report_path)
    progress = db.query(AdmissionProgress).filter(AdmissionProgress.admission_id == submission_id).first()
    if progress:
        db.delete(progress)
    db.delete(row)
    db.commit()
    return {"success": True}


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


@app.put("/api/submissions/contacts/{submission_id}")
async def update_contact(
    submission_id: int,
    body: ContactUpdate,
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    row = db.query(ContactSubmission).filter(ContactSubmission.id == submission_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Submission not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row_to_dict(row)


@app.delete("/api/submissions/contacts/{submission_id}")
async def delete_contact(
    submission_id: int,
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    row = db.query(ContactSubmission).filter(ContactSubmission.id == submission_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Submission not found")
    db.delete(row)
    db.commit()
    return {"success": True}


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


@app.put("/api/submissions/careers/{submission_id}", response_model=None)
async def update_career(
    submission_id: int,
    body: CareerUpdate,
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    row = db.query(CareerSubmission).filter(CareerSubmission.id == submission_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Submission not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    data = row_to_dict(row)
    if data.get("cv_path"):
        data["cv_url"] = f"/api/submissions/careers/{submission_id}/cv"
        del data["cv_path"]
    else:
        data["cv_url"] = None
        data.pop("cv_path", None)
    return data


@app.delete("/api/submissions/careers/{submission_id}")
async def delete_career(
    submission_id: int,
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    row = db.query(CareerSubmission).filter(CareerSubmission.id == submission_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Submission not found")
    if row.cv_path and os.path.exists(row.cv_path):
        os.remove(row.cv_path)
    db.delete(row)
    db.commit()
    return {"success": True}


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


# ── Progress API (CRUD, auth required) ────────────────────────────────────────

PROGRESS_FIELDS = [
    "child_name", "father_name", "father_phone", "mother_name", "mother_phone",
    "date_of_facilitation", "class_name", "form_status", "affiliation",
    "interview_applicable", "parent_status", "first_call_interview_assessment",
    "second_call_interview_assessment", "acceptance", "send_confirmation_date",
    "due_date_for_payment", "follow_up", "follow_up_2", "follow_up_3",
    "status", "remarks", "session",
]

# Fields that fall back to the admission record when NULL in progress
_ADMISSION_FALLBACKS = {
    "child_name": "child_name",
    "father_name": "father_name",
    "father_phone": "father_phone",
    "mother_name": "mother_name",
    "mother_phone": "mother_phone",
    "session": "session",
}


def _admission_progress_row(adm, prog) -> dict:
    """Build a combined dict from an AdmissionSubmission and optional AdmissionProgress."""
    d = {"admission_id": adm.id}
    if prog:
        d["progress_id"] = prog.id
        for f in PROGRESS_FIELDS:
            val = getattr(prog, f)
            if val is None and f in _ADMISSION_FALLBACKS:
                val = getattr(adm, _ADMISSION_FALLBACKS[f])
            d[f] = val
        d["updated_at"] = prog.updated_at.isoformat() if prog.updated_at else None
    else:
        d["progress_id"] = None
        for f in PROGRESS_FIELDS:
            if f in _ADMISSION_FALLBACKS:
                d[f] = getattr(adm, _ADMISSION_FALLBACKS[f])
            else:
                d[f] = None
        d["updated_at"] = None
    d["submitted_at"] = adm.created_at.isoformat() if adm.created_at else None
    return d


@app.get("/api/submissions/progress")
async def list_progress(
    page: int = 1,
    per_page: int = 25,
    sort: str = "created_at",
    order: str = "desc",
    search: str = "",
    date_from: str = "",
    date_to: str = "",
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    per_page = min(max(per_page, 1), 100)
    page = max(page, 1)

    query = db.query(AdmissionSubmission, AdmissionProgress).outerjoin(
        AdmissionProgress, AdmissionSubmission.id == AdmissionProgress.admission_id
    )

    if search:
        pattern = f"%{search}%"
        query = query.filter(or_(
            AdmissionSubmission.child_name.ilike(pattern),
            AdmissionSubmission.father_name.ilike(pattern),
            AdmissionSubmission.mother_name.ilike(pattern),
        ))
    if date_from:
        try:
            query = query.filter(AdmissionSubmission.created_at >= datetime.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            end = datetime.fromisoformat(date_to)
            end = end.replace(hour=23, minute=59, second=59)
            query = query.filter(AdmissionSubmission.created_at <= end)
        except ValueError:
            pass

    allowed_sorts = {"created_at", "child_name", "session"}
    sort_col = sort if sort in allowed_sorts else "created_at"
    col = getattr(AdmissionSubmission, sort_col)
    order_clause = col.asc() if order == "asc" else col.desc()

    total = query.count()
    rows = query.order_by(order_clause).offset((page - 1) * per_page).limit(per_page).all()

    return {
        "items": [_admission_progress_row(adm, prog) for adm, prog in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": math.ceil(total / per_page) if total else 1,
    }


@app.get("/api/submissions/progress/export")
async def export_progress_csv(
    date_from: str = "",
    date_to: str = "",
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    import csv
    import io

    query = db.query(AdmissionSubmission, AdmissionProgress).outerjoin(
        AdmissionProgress, AdmissionSubmission.id == AdmissionProgress.admission_id
    )

    if date_from:
        try:
            query = query.filter(AdmissionSubmission.created_at >= datetime.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            end = datetime.fromisoformat(date_to)
            end = end.replace(hour=23, minute=59, second=59)
            query = query.filter(AdmissionSubmission.created_at <= end)
        except ValueError:
            pass

    rows = query.order_by(AdmissionSubmission.created_at.desc()).all()

    headers = ["Submitted"] + [col for col in PROGRESS_FIELDS]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for adm, prog in rows:
        row_data = _admission_progress_row(adm, prog)
        submitted = adm.created_at.strftime("%Y-%m-%d") if adm.created_at else ""
        writer.writerow([submitted] + [row_data.get(f, "") or "" for f in PROGRESS_FIELDS])

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=progress-export.csv"},
    )


@app.get("/api/submissions/progress/{admission_id}")
async def get_progress(
    admission_id: int,
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    adm = db.query(AdmissionSubmission).filter(AdmissionSubmission.id == admission_id).first()
    if not adm:
        raise HTTPException(status_code=404, detail="Admission not found")
    prog = db.query(AdmissionProgress).filter(AdmissionProgress.admission_id == admission_id).first()
    return _admission_progress_row(adm, prog)


@app.put("/api/submissions/progress/{admission_id}")
async def upsert_progress(
    admission_id: int,
    body: ProgressUpdate,
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    adm = db.query(AdmissionSubmission).filter(AdmissionSubmission.id == admission_id).first()
    if not adm:
        raise HTTPException(status_code=404, detail="Admission not found")
    row = db.query(AdmissionProgress).filter(AdmissionProgress.admission_id == admission_id).first()
    if not row:
        row = AdmissionProgress(admission_id=admission_id)
        db.add(row)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return _admission_progress_row(adm, row)


# SPA catch-all: serve dashboard/index.html for any /dashboard/* sub-path
@app.get("/dashboard/{rest:path}")
async def dashboard_spa(rest: str):
    return FileResponse(os.path.join(STATIC_DIR, "dashboard", "index.html"))


# Serve Astro static build (admin UI is pre-built into dist/admin/)
# Must be last since it catches all routes
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
