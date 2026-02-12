import asyncio
import os
import secrets
import uuid
import aiofiles
import bcrypt
import jwt
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Depends, Header, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.requests import Request
from starlette.responses import Response
from typing import Optional
import httpx

from database import init_db, get_db, ContactSubmission, CareerSubmission, AdmissionSubmission, AdminUser

UPLOAD_DIR = "uploads"
KIVA_DIR = Path(__file__).parent.parent / "kiva"
STATIC_DIR = KIVA_DIR / "dist"

JWT_SECRET = os.environ.get("KIVA_JWT_SECRET")
if not JWT_SECRET:
    JWT_SECRET = secrets.token_urlsafe(32)
    print("WARNING: KIVA_JWT_SECRET not set — using a random secret (tokens won't survive restarts)")


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

    return {"success": True, "id": submission.id}


@app.post("/api/careers")
async def submit_career(
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

    return {"success": True, "id": submission.id}


@app.post("/api/admission")
async def submit_admission(
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


# Serve Astro static build (must be last - catches all routes)
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
