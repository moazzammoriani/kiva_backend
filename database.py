from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = "sqlite:///./kiva.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class ContactSubmission(Base):
    __tablename__ = "contact_submissions"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    subject = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class CareerSubmission(Base):
    __tablename__ = "career_submissions"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    position = Column(String, nullable=True)
    cover_letter = Column(Text, nullable=True)
    cv_path = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AdmissionSubmission(Base):
    __tablename__ = "admission_submissions"

    id = Column(Integer, primary_key=True, index=True)

    # Session
    session = Column(String, nullable=False)

    # Child's Information
    child_name = Column(String, nullable=False)
    dob = Column(String, nullable=False)
    address = Column(Text, nullable=False)
    applied_before = Column(String, nullable=False)
    previous_school = Column(String, nullable=True)
    previous_class = Column(String, nullable=True)
    has_report = Column(String, nullable=True)
    progress_report_path = Column(String, nullable=True)
    reason = Column(Text, nullable=True)
    medical_info = Column(Text, nullable=True)
    special_needs = Column(String, nullable=False)

    # Mother's Details
    mother_name = Column(String, nullable=False)
    mother_profession = Column(String, nullable=True)
    mother_education = Column(String, nullable=True)
    mother_organization = Column(String, nullable=True)
    mother_email = Column(String, nullable=True)
    mother_phone = Column(String, nullable=True)
    mother_cnic = Column(String, nullable=True)

    # Father's Details
    father_name = Column(String, nullable=False)
    father_profession = Column(String, nullable=True)
    father_education = Column(String, nullable=True)
    father_organization = Column(String, nullable=True)
    father_email = Column(String, nullable=True)
    father_phone = Column(String, nullable=True)
    father_cnic = Column(String, nullable=True)

    # Sibling Information
    sibling_name = Column(String, nullable=True)
    sibling_grade = Column(String, nullable=True)
    sibling_school = Column(String, nullable=True)

    # Emergency Contact
    emergency_name = Column(String, nullable=False)
    emergency_phone = Column(String, nullable=False)

    # How did you hear about us (stored as comma-separated)
    hear_about = Column(String, nullable=True)

    # Why Kiva is a good fit
    fit_response = Column(Text, nullable=True)

    # Declaration
    declaration = Column(Boolean, nullable=False)
    signature = Column(String, nullable=False)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AdminUser(Base):
    __tablename__ = "admin_users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
