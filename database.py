from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import sessionmaker, declarative_base, relationship

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


class KivaKampSubmission(Base):
    __tablename__ = "kiva_kamp_submissions"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    child_class = Column(String, nullable=False)
    age = Column(String, nullable=False)
    school_name = Column(String, nullable=False)
    father_name = Column(String, nullable=False)
    mother_name = Column(String, nullable=False)
    father_contact = Column(String, nullable=False)
    mother_contact = Column(String, nullable=False)
    attended_past = Column(String, nullable=False)
    sibling = Column(String, nullable=False)
    group_registration = Column(String, nullable=False)
    referral = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AdmissionProgress(Base):
    __tablename__ = "admission_progress"

    id = Column(Integer, primary_key=True, index=True)
    admission_id = Column(Integer, ForeignKey("admission_submissions.id"), unique=True, nullable=False, index=True)

    # Editable overrides (fall back to admission values when NULL)
    child_name = Column(String, nullable=True)
    father_name = Column(String, nullable=True)
    father_phone = Column(String, nullable=True)
    mother_name = Column(String, nullable=True)
    mother_phone = Column(String, nullable=True)

    date_of_facilitation = Column(String, nullable=True)
    class_name = Column(String, nullable=True)
    form_status = Column(String, nullable=True)
    affiliation = Column(String, nullable=True)
    interview_applicable = Column(String, nullable=True)
    parent_status = Column(String, nullable=True)
    first_call_interview_assessment = Column(String, nullable=True)
    second_call_interview_assessment = Column(String, nullable=True)
    acceptance = Column(String, nullable=True)
    send_confirmation_date = Column(String, nullable=True)
    due_date_for_payment = Column(String, nullable=True)
    follow_up = Column(Text, nullable=True)
    follow_up_2 = Column(Text, nullable=True)
    follow_up_3 = Column(Text, nullable=True)
    status = Column(String, nullable=True)
    remarks = Column(Text, nullable=True)
    session = Column(String, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    admission = relationship("AdmissionSubmission", backref="progress")


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
