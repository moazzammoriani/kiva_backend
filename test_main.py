import os
import io
import asyncio
from datetime import datetime
import bcrypt
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from cnic import normalize_cnic
from eligibility import (
    OUTSIDE_ELIGIBLE_RANGE,
    calculate_decimal_age,
    calculate_eligible_class,
    current_eligibility_year,
    eligible_class_for_age,
    format_age_on_date,
    format_age_on_july,
)
from main import app
from database import (
    Base,
    get_db,
    AdminUser,
    AdmissionSubmission,
    AdmissionProgress,
    ContactSubmission,
    SubmissionView,
    ensure_admission_submission_columns,
)
from normalize_cnics import normalize_database

# Use a separate test database
TEST_DATABASE_URL = "sqlite:///./test_kiva.db"
test_engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(scope="module", autouse=True)
def setup_database():
    """Create tables before tests."""
    Base.metadata.create_all(bind=test_engine)
    ensure_admission_submission_columns(test_engine)
    os.makedirs("uploads", exist_ok=True)
    yield


@pytest.fixture
def client():
    return TestClient(app)


class TestSubmissionViews:
    def test_contact_view_status_is_shared_and_preserves_first_view(self):
        db = TestSessionLocal()
        try:
            contact = ContactSubmission(
                name="View Status Contact",
                email="view-status@example.com",
                message="Test",
            )
            db.add(contact)
            db.commit()
            db.refresh(contact)

            initial = asyncio.run(main.list_contacts(username="admin-one", db=db))
            item = next(row for row in initial["items"] if row["id"] == contact.id)
            assert item["viewed"] is False
            assert item["viewed_at"] is None

            asyncio.run(main.get_contact(contact.id, username="admin-one", db=db))
            first_viewed_at = db.query(SubmissionView).filter(
                SubmissionView.submission_type == "contacts",
                SubmissionView.submission_id == contact.id,
            ).one().viewed_at

            asyncio.run(main.get_contact(contact.id, username="admin-two", db=db))
            second_viewed_at = db.query(SubmissionView).filter(
                SubmissionView.submission_type == "contacts",
                SubmissionView.submission_id == contact.id,
            ).one().viewed_at
            assert second_viewed_at == first_viewed_at

            shared_list = asyncio.run(main.list_contacts(username="admin-two", db=db))
            shared_item = next(row for row in shared_list["items"] if row["id"] == contact.id)
            assert shared_item["viewed"] is True
            assert shared_item["viewed_at"] == first_viewed_at.isoformat()

            asyncio.run(main.delete_contact(contact.id, username="admin-one", db=db))
            assert db.query(SubmissionView).filter(
                SubmissionView.submission_type == "contacts",
                SubmissionView.submission_id == contact.id,
            ).count() == 0
        finally:
            db.close()

    def test_admission_and_progress_view_statuses_are_separate(self):
        db = TestSessionLocal()
        try:
            admission = AdmissionSubmission(
                session="2026-2027",
                child_name="Separate View Status",
                dob="2020-01-01",
                address="Test Address",
                applied_before="no",
                special_needs="no",
                mother_name="Test Mother",
                father_name="Test Father",
                emergency_name="Test Emergency",
                emergency_phone="03000000000",
                declaration=True,
                signature="Test Signature",
            )
            db.add(admission)
            db.commit()
            db.refresh(admission)

            asyncio.run(main.get_admission(admission.id, username="admin", db=db))

            admissions = asyncio.run(main.list_admissions(username="admin", db=db))
            admission_item = next(
                row for row in admissions["items"] if row["id"] == admission.id
            )
            progress = asyncio.run(main.list_progress(username="admin", db=db))
            progress_item = next(
                row for row in progress["items"] if row["admission_id"] == admission.id
            )
            assert admission_item["viewed"] is True
            assert progress_item["viewed"] is False

            asyncio.run(main.get_progress(admission.id, username="admin", db=db))
            progress = asyncio.run(main.list_progress(username="admin", db=db))
            progress_item = next(
                row for row in progress["items"] if row["admission_id"] == admission.id
            )
            assert progress_item["viewed"] is True

            asyncio.run(main.delete_admission(admission.id, username="admin", db=db))
            assert db.query(SubmissionView).filter(
                SubmissionView.submission_id == admission.id,
                SubmissionView.submission_type.in_({"admissions", "progress"}),
            ).count() == 0
        finally:
            db.close()


class TestInstagramEndpoint:
    def test_missing_token_returns_profile_fallback(self, monkeypatch):
        monkeypatch.setattr(main, "IG_ACCESS_TOKEN", "")
        monkeypatch.setattr(main, "IG_USERNAME", "kivaschool")
        main._ig_cache.clear()

        data = asyncio.run(main.instagram_media(limit=3))

        assert data["profile"]["username"] == "kivaschool"
        assert data["posts"] == []
        assert data["error"] == "missing_token"

    def test_instagram_api_error_returns_profile_fallback(self, monkeypatch, caplog):
        class FakeInstagramClient:
            def __init__(self, timeout):
                self.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            async def get(self, url, params):
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "message": "API access blocked.",
                            "type": "OAuthException",
                            "code": 200,
                        }
                    },
                )

        monkeypatch.setattr(main, "IG_ACCESS_TOKEN", "test-token")
        monkeypatch.setattr(main, "IG_USERNAME", "kivaschool")
        monkeypatch.setattr(main.httpx, "AsyncClient", FakeInstagramClient)
        main._ig_cache.clear()
        caplog.set_level("WARNING", logger="kiva")

        data = asyncio.run(main.instagram_media(limit=3))

        assert data["profile"]["username"] == "kivaschool"
        assert data["posts"] == []
        assert data["error"] == "api_error"
        assert "API access blocked." in caplog.text

    def test_instagram_success_returns_posts(self, monkeypatch):
        class FakeInstagramClient:
            def __init__(self, timeout):
                self.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            async def get(self, url, params):
                if url.endswith("/media"):
                    return httpx.Response(
                        200,
                        json={
                            "data": [
                                {
                                    "id": "media-1",
                                    "caption": "First day of school",
                                    "media_type": "IMAGE",
                                    "media_url": "https://cdn.example/post.jpg",
                                    "permalink": "https://www.instagram.com/p/example/",
                                    "timestamp": "2026-01-01T00:00:00+0000",
                                }
                            ]
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "id": "user-1",
                        "username": "kivaschool",
                        "profile_picture_url": "https://cdn.example/avatar.jpg",
                        "media_count": 100,
                    },
                )

        monkeypatch.setattr(main, "IG_ACCESS_TOKEN", "test-token")
        monkeypatch.setattr(main, "IG_USERNAME", "kivaschool")
        monkeypatch.setattr(main.httpx, "AsyncClient", FakeInstagramClient)
        main._ig_cache.clear()

        data = asyncio.run(main.instagram_media(limit=3))

        assert data["profile"]["username"] == "kivaschool"
        assert data["profile"]["media_count"] == 100
        assert data["posts"] == [
            {
                "id": "media-1",
                "permalink": "https://www.instagram.com/p/example/",
                "thumbnail": "https://cdn.example/post.jpg",
                "caption": "First day of school",
                "media_type": "IMAGE",
                "timestamp": "2026-01-01T00:00:00+0000",
                "children_count": 0,
            }
        ]


class TestCnicNormalization:
    def test_normalize_cnic(self):
        assert normalize_cnic("12345-1234567-1") == "1234512345671"
        assert normalize_cnic(" 12345 / 1234567 / 1 ") == "1234512345671"
        assert normalize_cnic("") is None
        assert normalize_cnic(None) is None

    def test_cleanup_script_dry_run_and_apply(self, tmp_path):
        db_path = tmp_path / "cnic_cleanup.db"
        cleanup_engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=cleanup_engine)
        cleanup_session = sessionmaker(bind=cleanup_engine)()
        row = AdmissionSubmission(
            session="2026-2027",
            child_name="Cleanup Test",
            dob="2020-01-01",
            address="Test Address",
            applied_before="no",
            special_needs="no",
            mother_name="Test Mother",
            mother_cnic="12345-1234567-1",
            father_name="Test Father",
            father_cnic="not provided",
            emergency_name="Test Emergency",
            emergency_phone="03001234567",
            declaration=True,
            signature="Test Parent",
        )
        cleanup_session.add(row)
        cleanup_session.commit()
        row_id = row.id
        cleanup_session.close()
        cleanup_engine.dispose()

        dry_run = normalize_database(db_path)
        assert dry_run == {
            "records_scanned": 1,
            "records_changed": 1,
            "fields_changed": 2,
            "would_be_empty": 1,
            "invalid_length": 0,
        }

        verify_engine = create_engine(f"sqlite:///{db_path}")
        verify_session = sessionmaker(bind=verify_engine)()
        unchanged = verify_session.query(AdmissionSubmission).filter_by(id=row_id).one()
        assert unchanged.mother_cnic == "12345-1234567-1"
        assert unchanged.father_cnic == "not provided"
        verify_session.close()
        verify_engine.dispose()

        applied = normalize_database(db_path, apply=True)
        assert applied["records_changed"] == 1
        assert applied["fields_changed"] == 2

        result_engine = create_engine(f"sqlite:///{db_path}")
        result_session = sessionmaker(bind=result_engine)()
        normalized = result_session.query(AdmissionSubmission).filter_by(id=row_id).one()
        assert normalized.mother_cnic == "1234512345671"
        assert normalized.father_cnic is None
        result_session.close()
        result_engine.dispose()


class TestAdmissionEligibility:
    @pytest.mark.parametrize(
        ("age", "expected"),
        [
            (1.4999, OUTSIDE_ELIGIBLE_RANGE),
            (1.5, "Play Group"),
            (2.4999, "Play Group"),
            (2.5, "Pre-Nursery"),
            (3.5, "Nursery"),
            (4.5, "Prep"),
            (5.5, "I"),
            (6.5, "II"),
            (7.5, "III"),
            (8.5, "IV"),
            (9.5, "V"),
            (10.4999, "V"),
            (10.5, "VI"),
            (11.4999, "VI"),
            (11.5, OUTSIDE_ELIGIBLE_RANGE),
        ],
    )
    def test_continuous_decimal_age_ranges(self, age, expected):
        assert eligible_class_for_age(age) == expected

    def test_decimal_age_uses_july_first_and_average_gregorian_year(self):
        assert calculate_decimal_age("2021-07-01", 2026) == pytest.approx(5.0, abs=0.01)
        assert calculate_eligible_class("2021-07-01", 2026) == "Prep"
        assert calculate_eligible_class("not-a-date", 2026) is None

    def test_formatted_age_uses_july_first_cutoff(self):
        assert format_age_on_july("2022-09-08", 2026) == "3 years, 9 months, 23 days"
        assert format_age_on_july("2021-07-01", 2026) == "5 years, 0 months, 0 days"
        assert format_age_on_july("not-a-date", 2026) is None

    def test_formatted_age_can_use_current_date(self):
        assert format_age_on_date("2022-09-08", datetime(2026, 6, 25)) == (
            "3 years, 9 months, 17 days"
        )

    def test_admission_response_calculates_class_without_model_columns(self):
        admission = AdmissionSubmission(
            dob="2021-07-01",
            created_at=datetime(2026, 1, 1),
        )

        data = main.row_to_dict(admission)

        assert data["eligible_class"] == "Prep"
        assert data["current_age"] is not None
        assert data["age_on_july"] == "5 years, 0 months, 0 days"
        assert data["eligibility_year"] == 2026
        assert "eligible_class" not in AdmissionSubmission.__table__.columns
        assert "current_age" not in AdmissionSubmission.__table__.columns
        assert "age_on_july" not in AdmissionSubmission.__table__.columns
        assert "eligibility_year" not in AdmissionSubmission.__table__.columns

    def test_progress_defaults_to_calculated_class(self):
        admission = AdmissionSubmission(
            id=123,
            dob="2021-07-01",
            created_at=datetime(2026, 1, 1),
        )

        data = main._admission_progress_row(admission, None)

        assert data["class_name"] == "Prep"
        assert data["current_age"] is not None
        assert data["age_on_july"] == "5 years, 0 months, 0 days"


class TestContactEndpoint:
    def test_submit_contact_success(self, client):
        """Test successful contact form submission."""
        response = client.post(
            "/api/contact",
            data={
                "name": "John Doe",
                "email": "john@example.com",
                "subject": "Inquiry",
                "phone": "1234567890",
                "message": "Hello, I have a question.",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "id" in data

    def test_submit_contact_minimal(self, client):
        """Test contact form with only required fields."""
        response = client.post(
            "/api/contact",
            data={
                "name": "Jane Doe",
                "email": "jane@example.com",
                "message": "Just a message.",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_submit_contact_missing_required(self, client):
        """Test contact form with missing required fields."""
        response = client.post(
            "/api/contact",
            data={
                "name": "Test User",
                "email": "test@example.com",
                # Missing 'message'
            },
        )
        assert response.status_code == 422


class TestCareerEndpoint:
    def test_submit_career_success(self, client):
        """Test successful career form submission."""
        response = client.post(
            "/api/careers",
            data={
                "name": "Alice Smith",
                "email": "alice@example.com",
                "phone": "9876543210",
                "position": "Teacher",
                "coverLetter": "I am passionate about education...",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "id" in data

    def test_submit_career_minimal(self, client):
        """Test career form with only required fields."""
        response = client.post(
            "/api/careers",
            data={
                "name": "Bob Johnson",
                "email": "bob@example.com",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_submit_career_with_cv(self, client):
        """Test career form with CV file upload."""
        cv_content = b"This is a fake CV content"
        response = client.post(
            "/api/careers",
            data={
                "name": "Carol White",
                "email": "carol@example.com",
                "position": "Administrator",
            },
            files={
                "cv": ("resume.pdf", io.BytesIO(cv_content), "application/pdf"),
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


class TestAdmissionEndpoint:
    def test_submit_admission_success(self, client):
        """Test successful admission form submission."""
        response = client.post(
            "/api/admission",
            data={
                # Session
                "session": "2024-2025",
                # Child's Information
                "childName": "Emma Wilson",
                "dob": "2018-05-15",
                "address": "123 Main Street, Karachi",
                "appliedBefore": "no",
                "previousSchool": "ABC Daycare",
                "previousClass": "Playgroup",
                "hasReport": "yes",
                "reason": "Looking for better education",
                "medicalInfo": "No allergies",
                "specialNeeds": "yes",
                "specialNeedsDetails": "Requires speech therapy support.",
                # Mother's Details
                "motherName": "Sarah Wilson",
                "motherProfession": "Doctor",
                "motherEducation": "MBBS",
                "motherInstitution": "Dow University",
                "motherOrganization": "City Hospital",
                "motherEmail": "sarah@example.com",
                "motherPhone": "1111111111",
                "motherCnic": "12345-1234567-1",
                # Father's Details
                "fatherName": "David Wilson",
                "fatherProfession": "Engineer",
                "fatherEducation": "BSc Engineering",
                "fatherInstitution": "NED University",
                "fatherOrganization": "Tech Corp",
                "fatherEmail": "david@example.com",
                "fatherPhone": "2222222222",
                "fatherCnic": "12345-1234567-2",
                # Sibling Information
                "siblingName": "Tom Wilson",
                "siblingGrade": "Grade 3",
                "siblingSchool": "Kiva School",
                # Emergency Contact
                "emergencyName": "Grandma Wilson",
                "emergencyPhone": "3333333333",
                # How did you hear about us
                "hearAbout": ["website", "friends-family"],
                # Why Kiva
                "fitResponse": "We love the Montessori approach.",
                # Declaration
                "declaration": "true",
                "signature": "David Wilson",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "id" in data
        db = TestSessionLocal()
        submission = db.query(AdmissionSubmission).filter_by(id=data["id"]).one()
        assert submission.mother_cnic == "1234512345671"
        assert submission.father_cnic == "1234512345672"
        assert submission.special_needs_details == "Requires speech therapy support."
        assert submission.mother_institution == "Dow University"
        assert submission.father_institution == "NED University"
        assert not hasattr(submission, "eligible_class")
        assert not hasattr(submission, "eligibility_year")
        db.close()

    def test_submit_admission_minimal(self, client):
        """Test admission form with only required fields."""
        response = client.post(
            "/api/admission",
            data={
                "session": "2025-2026",
                "childName": "Liam Brown",
                "dob": "2019-03-20",
                "address": "456 Oak Avenue",
                "appliedBefore": "no",
                "specialNeeds": "no",
                "motherName": "Emily Brown",
                "fatherName": "Michael Brown",
                "emergencyName": "Uncle Brown",
                "emergencyPhone": "4444444444",
                "declaration": "true",
                "signature": "Emily Brown",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_submit_admission_requires_special_needs_details_when_yes(self, client):
        """Test special educational needs details are required when special needs is yes."""
        response = client.post(
            "/api/admission",
            data={
                "session": "2025-2026",
                "childName": "No Details",
                "dob": "2019-03-20",
                "address": "456 Oak Avenue",
                "appliedBefore": "no",
                "specialNeeds": "yes",
                "motherName": "Emily Brown",
                "fatherName": "Michael Brown",
                "emergencyName": "Uncle Brown",
                "emergencyPhone": "4444444444",
                "declaration": "true",
                "signature": "Emily Brown",
            },
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "Special educational needs details are required."

    def test_submit_admission_with_progress_report(self, client):
        """Test admission form with progress report file upload."""
        report_content = b"Progress report content"
        response = client.post(
            "/api/admission",
            data={
                "session": "2024-2025",
                "childName": "Olivia Green",
                "dob": "2017-08-10",
                "address": "789 Pine Road",
                "appliedBefore": "yes",
                "specialNeeds": "no",
                "motherName": "Anna Green",
                "fatherName": "James Green",
                "emergencyName": "Aunt Green",
                "emergencyPhone": "5555555555",
                "declaration": "true",
                "signature": "Anna Green",
            },
            files={
                "progressReport": ("report.pdf", io.BytesIO(report_content), "application/pdf"),
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_submit_admission_missing_required(self, client):
        """Test admission form with missing required fields."""
        response = client.post(
            "/api/admission",
            data={
                "session": "2024-2025",
                "childName": "Test Child",
                # Missing many required fields
            },
        )
        assert response.status_code == 422


def _sample_kiva_kamp_payload(name: str = "Sara Ahmed") -> dict:
    return {
        "name": name,
        "class": "Grade 2",
        "age": "7",
        "schoolName": "Greenwood Elementary",
        "fatherName": "Ahmed Khan",
        "motherName": "Fatima Ahmed",
        "fatherContact": "03001234567",
        "motherContact": "03007654321",
        "attendedPast": "No",
        "sibling": "Yes",
        "group": "No",
        "referral": "Social Media",
    }


class TestKivaKampEndpoint:
    def test_submit_kiva_kamp_success(self, client):
        """Test successful Kiva Kamps registration submission."""
        response = client.post("/api/kiva-kamps", data=_sample_kiva_kamp_payload())
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "id" in data

    def test_submit_kiva_kamp_missing_required(self, client):
        """Test Kiva Kamps registration with missing required fields."""
        response = client.post(
            "/api/kiva-kamps",
            data={"name": "Only Name"},
        )
        assert response.status_code == 422

    def test_list_kiva_kamps_requires_auth(self, client):
        """Listing without a token is rejected."""
        response = client.get("/api/submissions/kiva-kamps")
        assert response.status_code == 401

    def test_list_and_detail_kiva_kamps(self, client, auth_token):
        """Submission appears in list and detail view."""
        submit = client.post("/api/kiva-kamps", data=_sample_kiva_kamp_payload("Listing Check"))
        assert submit.status_code == 200
        submission_id = submit.json()["id"]

        headers = {"Authorization": f"Bearer {auth_token}"}
        list_resp = client.get("/api/submissions/kiva-kamps", headers=headers)
        assert list_resp.status_code == 200
        assert list_resp.json()["total"] >= 1

        detail = client.get(f"/api/submissions/kiva-kamps/{submission_id}", headers=headers)
        assert detail.status_code == 200
        row = detail.json()
        assert row["name"] == "Listing Check"
        assert row["child_class"] == "Grade 2"
        assert row["group_registration"] == "No"

    def test_export_kiva_kamps_csv(self, client, auth_token):
        """Export returns CSV with the expected header row."""
        headers = {"Authorization": f"Bearer {auth_token}"}
        response = client.get("/api/submissions/kiva-kamps/export", headers=headers)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        first_line = response.text.splitlines()[0]
        assert "name" in first_line
        assert "child_class" in first_line
        assert "referral" in first_line

    def test_update_and_delete_kiva_kamp(self, client, auth_token):
        """Admin can update and delete a submission."""
        submit = client.post("/api/kiva-kamps", data=_sample_kiva_kamp_payload("Delete Me"))
        submission_id = submit.json()["id"]

        headers = {"Authorization": f"Bearer {auth_token}"}
        updated = client.put(
            f"/api/submissions/kiva-kamps/{submission_id}",
            json={"referral": "Word of Mouth"},
            headers=headers,
        )
        assert updated.status_code == 200
        assert updated.json()["referral"] == "Word of Mouth"

        deleted = client.delete(f"/api/submissions/kiva-kamps/{submission_id}", headers=headers)
        assert deleted.status_code == 200

        missing = client.get(f"/api/submissions/kiva-kamps/{submission_id}", headers=headers)
        assert missing.status_code == 404


@pytest.fixture
def admin_user():
    """Create a test admin user and clean up after."""
    db = TestSessionLocal()
    db.query(AdminUser).filter(AdminUser.username == "testadmin").delete()
    db.commit()
    password_hash = bcrypt.hashpw(b"testpass123", bcrypt.gensalt(rounds=4)).decode()
    user = AdminUser(username="testadmin", password_hash=password_hash)
    db.add(user)
    db.commit()
    db.refresh(user)
    yield user
    db.query(AdminUser).filter(AdminUser.id == user.id).delete()
    db.commit()
    db.close()


@pytest.fixture
def auth_token(client, admin_user):
    """Get a valid JWT token for the test admin user."""
    response = client.post(
        "/api/auth/login",
        json={"username": "testadmin", "password": "testpass123"},
    )
    return response.json()["token"]


class TestAuthEndpoints:
    def test_login_success(self, client, admin_user):
        """Test successful login returns a JWT token."""
        response = client.post(
            "/api/auth/login",
            json={"username": "testadmin", "password": "testpass123"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "token" in data

    def test_login_invalid_password(self, client, admin_user):
        """Test login with wrong password returns 401."""
        response = client.post(
            "/api/auth/login",
            json={"username": "testadmin", "password": "wrongpassword"},
        )
        assert response.status_code == 401

    def test_login_nonexistent_user(self, client):
        """Test login with nonexistent user returns 401."""
        response = client.post(
            "/api/auth/login",
            json={"username": "nobody", "password": "whatever"},
        )
        assert response.status_code == 401

    def test_me_valid_token(self, client, auth_token):
        """Test /me with valid token returns user info."""
        response = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 200
        assert response.json()["username"] == "testadmin"

    def test_me_no_token(self, client):
        """Test /me without token returns 401."""
        response = client.get("/api/auth/me")
        assert response.status_code == 401

    def test_me_invalid_token(self, client):
        """Test /me with invalid token returns 401."""
        response = client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer invalidtoken"},
        )
        assert response.status_code == 401

    def test_rebuild_requires_auth(self, client):
        """Test rebuild without token returns 401."""
        response = client.post("/api/rebuild")
        assert response.status_code == 401


class TestSubmissionsEndpoints:
    """Test the GET /api/submissions/* endpoints."""

    def test_list_contacts_requires_auth(self, client):
        response = client.get("/api/submissions/contacts")
        assert response.status_code == 401

    def test_list_contacts_success(self, client, auth_token):
        response = client.get(
            "/api/submissions/contacts",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "per_page" in data
        assert "pages" in data
        assert data["page"] == 1
        # Submissions from earlier tests should be present
        assert data["total"] >= 2

    def test_list_contacts_pagination(self, client, auth_token):
        response = client.get(
            "/api/submissions/contacts?per_page=1&page=2",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        data = response.json()
        assert data["page"] == 2
        assert data["per_page"] == 1
        assert len(data["items"]) <= 1

    def test_list_contacts_search(self, client, auth_token):
        response = client.get(
            "/api/submissions/contacts?search=John",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        data = response.json()
        assert data["total"] >= 1
        assert all("John" in item["name"] or "john" in item["email"] for item in data["items"])

    def test_list_careers_success(self, client, auth_token):
        response = client.get(
            "/api/submissions/careers",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 2
        # cv_path should be replaced with cv_url
        for item in data["items"]:
            assert "cv_path" not in item
            assert "cv_url" in item

    def test_list_admissions_summary(self, client, auth_token):
        response = client.get(
            "/api/submissions/admissions",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 2
        # Should only have summary fields
        item = data["items"][0]
        assert "child_name" in item
        assert "session" in item
        assert "eligible_class" in item
        assert "eligibility_year" in item
        assert "address" not in item
        assert "mother_cnic" not in item

    def test_update_contact_success(self, client, auth_token):
        create = client.post(
            "/api/contact",
            data={"name": "Edit Me", "email": "edit@example.com", "message": "original"},
        )
        cid = create.json()["id"]
        response = client.put(
            f"/api/submissions/contacts/{cid}",
            json={"message": "updated", "subject": "Changed"},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "updated"
        assert data["subject"] == "Changed"
        assert data["email"] == "edit@example.com"
        get_resp = client.get(
            f"/api/submissions/contacts/{cid}",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert get_resp.json()["message"] == "updated"

    def test_update_contact_not_found(self, client, auth_token):
        response = client.put(
            "/api/submissions/contacts/999999",
            json={"name": "X"},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 404

    def test_update_contact_requires_auth(self, client):
        assert client.put("/api/submissions/contacts/1", json={}).status_code == 401

    def test_delete_contact_success(self, client, auth_token):
        create = client.post(
            "/api/contact",
            data={"name": "Delete Me", "email": "del@example.com", "message": "bye"},
        )
        cid = create.json()["id"]
        response = client.delete(
            f"/api/submissions/contacts/{cid}",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True
        assert client.get(
            f"/api/submissions/contacts/{cid}",
            headers={"Authorization": f"Bearer {auth_token}"},
        ).status_code == 404

    def test_delete_contact_not_found(self, client, auth_token):
        response = client.delete(
            "/api/submissions/contacts/999999",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 404

    def test_delete_contact_requires_auth(self, client):
        assert client.delete("/api/submissions/contacts/1").status_code == 401

    def test_update_career_success(self, client, auth_token):
        create = client.post(
            "/api/careers",
            data={"name": "Career Edit", "email": "c@example.com", "position": "Teacher"},
        )
        cid = create.json()["id"]
        response = client.put(
            f"/api/submissions/careers/{cid}",
            json={"position": "Principal", "cover_letter": "Updated letter"},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["position"] == "Principal"
        assert data["cover_letter"] == "Updated letter"
        assert "cv_url" in data
        assert "cv_path" not in data

    def test_update_career_not_found(self, client, auth_token):
        response = client.put(
            "/api/submissions/careers/999999",
            json={"name": "X"},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 404

    def test_update_career_requires_auth(self, client):
        assert client.put("/api/submissions/careers/1", json={}).status_code == 401

    def test_delete_career_removes_cv_file(self, client, auth_token):
        cv_content = b"CV to be deleted"
        create = client.post(
            "/api/careers",
            data={"name": "Del Career", "email": "dc@example.com"},
            files={"cv": ("cv.pdf", io.BytesIO(cv_content), "application/pdf")},
        )
        cid = create.json()["id"]
        # Find the cv_path via the DB session
        db = TestSessionLocal()
        from database import CareerSubmission as _CS
        row = db.query(_CS).filter(_CS.id == cid).first()
        cv_path = row.cv_path
        db.close()
        assert cv_path and os.path.exists(cv_path)

        response = client.delete(
            f"/api/submissions/careers/{cid}",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 200
        assert not os.path.exists(cv_path)
        assert client.get(
            f"/api/submissions/careers/{cid}",
            headers={"Authorization": f"Bearer {auth_token}"},
        ).status_code == 404

    def test_delete_career_without_cv(self, client, auth_token):
        create = client.post(
            "/api/careers",
            data={"name": "No CV", "email": "nocv@example.com"},
        )
        cid = create.json()["id"]
        response = client.delete(
            f"/api/submissions/careers/{cid}",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_delete_career_not_found(self, client, auth_token):
        response = client.delete(
            "/api/submissions/careers/999999",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 404

    def test_delete_career_requires_auth(self, client):
        assert client.delete("/api/submissions/careers/1").status_code == 401

    def test_admission_detail_success(self, client, auth_token):
        # First get the list to find an ID
        list_resp = client.get(
            "/api/submissions/admissions",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        first_id = list_resp.json()["items"][0]["id"]

        response = client.get(
            f"/api/submissions/admissions/{first_id}",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        # Detail should include all fields
        assert "child_name" in data
        assert "address" in data
        assert "mother_name" in data
        assert "father_name" in data
        assert "emergency_name" in data

    def test_admission_detail_not_found(self, client, auth_token):
        response = client.get(
            "/api/submissions/admissions/99999",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 404

    def test_update_admission_success(self, client, auth_token, admission_id):
        response = client.put(
            f"/api/submissions/admissions/{admission_id}",
            json={
                "child_name": "Renamed Child",
                "dob": "2020-07-01",
                "mother_email": "new@example.com",
                "mother_cnic": "12345-1234567-1",
                "father_cnic": "12345 1234567 2",
            },
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["child_name"] == "Renamed Child"
        assert data["mother_email"] == "new@example.com"
        assert data["mother_cnic"] == "1234512345671"
        assert data["father_cnic"] == "1234512345672"
        assert data["eligible_class"] == calculate_eligible_class(
            "2020-07-01", data["eligibility_year"]
        )
        get_resp = client.get(
            f"/api/submissions/admissions/{admission_id}",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert get_resp.json()["child_name"] == "Renamed Child"

    def test_update_admission_not_found(self, client, auth_token):
        response = client.put(
            "/api/submissions/admissions/999999",
            json={"child_name": "X"},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 404

    def test_update_admission_requires_details_when_special_needs_changes_to_yes(
        self, client, auth_token, admission_id
    ):
        response = client.put(
            f"/api/submissions/admissions/{admission_id}",
            json={"special_needs": "yes", "special_needs_details": "  "},
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == (
            "Special educational needs details are required."
        )

    def test_update_admission_accepts_special_needs_details(
        self, client, auth_token, admission_id
    ):
        response = client.put(
            f"/api/submissions/admissions/{admission_id}",
            json={
                "special_needs": "yes",
                "special_needs_details": "  Requires classroom support.  ",
            },
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 200
        assert response.json()["special_needs_details"] == (
            "Requires classroom support."
        )

    def test_update_admission_allows_unrelated_legacy_edit_without_details(
        self, client, auth_token, admission_id
    ):
        db = TestSessionLocal()
        try:
            admission = db.query(AdmissionSubmission).filter(
                AdmissionSubmission.id == admission_id
            ).first()
            admission.special_needs = "yes"
            admission.special_needs_details = None
            db.commit()
        finally:
            db.close()

        response = client.put(
            f"/api/submissions/admissions/{admission_id}",
            json={"child_name": "Updated Legacy Admission"},
            headers={"Authorization": f"Bearer {auth_token}"},
        )

        assert response.status_code == 200
        assert response.json()["child_name"] == "Updated Legacy Admission"
        assert response.json()["special_needs_details"] is None

    def test_update_admission_requires_auth(self, client):
        assert client.put("/api/submissions/admissions/1", json={}).status_code == 401

    def test_cv_download_not_found(self, client, auth_token):
        response = client.get(
            "/api/submissions/careers/99999/cv",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 404

    def test_query_token_auth(self, client, auth_token):
        """Test that ?token= query param works for file download endpoints."""
        response = client.get(
            f"/api/submissions/careers/99999/cv?token={auth_token}",
        )
        # Should get 404 (not found), not 401 (unauthorized)
        assert response.status_code == 404

    def test_admission_pdf_export(self):
        db = TestSessionLocal()
        try:
            row = AdmissionSubmission(
                session="2025-2026",
                child_name="PDF Test Child",
                dob="2022-09-08",
                address="Test Address",
                applied_before="no",
                special_needs="no",
                mother_name="Test Mother",
                father_name="Test Father",
                emergency_name="Test Emergency",
                emergency_phone="03001234567",
                declaration=True,
                signature="Test Parent",
                created_at=datetime(2026, 1, 1),
            )
            db.add(row)
            db.commit()
            db.refresh(row)

            response = asyncio.run(
                main.download_admission_pdf(row.id, username="testadmin", db=db)
            )
            inline_response = asyncio.run(
                main.download_admission_pdf(row.id, inline=True, username="testadmin", db=db)
            )
            admission_id = row.id
        finally:
            db.close()

        assert response.media_type == "application/pdf"
        assert response.headers["content-disposition"] == (
            f'attachment; filename="admission_{admission_id}.pdf"'
        )
        assert response.body.startswith(b"%PDF")
        assert inline_response.headers["content-disposition"] == (
            f'inline; filename="admission_{admission_id}.pdf"'
        )
        assert inline_response.body.startswith(b"%PDF")


@pytest.fixture
def admission_id(client):
    """Create a test admission and return its ID."""
    response = client.post(
        "/api/admission",
        data={
            "session": "2025-2026",
            "childName": "Progress Test Child",
            "dob": "2019-01-01",
            "address": "Test Address",
            "appliedBefore": "no",
            "specialNeeds": "no",
            "motherName": "Test Mother",
            "fatherName": "Test Father",
            "emergencyName": "Test Emergency",
            "emergencyPhone": "9999999999",
            "declaration": "true",
            "signature": "Test Sig",
            "motherPhone": "1112223333",
            "fatherPhone": "4445556666",
        },
    )
    return response.json()["id"]


class TestProgressEndpoints:
    def test_list_shows_all_admissions(self, client, auth_token, admission_id):
        """Test that the progress list includes all admissions, even without progress records."""
        response = client.get(
            "/api/submissions/progress",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert data["total"] >= 1
        # Find our admission in the list
        item = next((i for i in data["items"] if i["admission_id"] == admission_id), None)
        assert item is not None
        assert item["child_name"] == "Progress Test Child"
        assert item["progress_id"] is None  # no progress record yet

    def test_list_progress_search(self, client, auth_token, admission_id):
        """Test searching progress list by child name."""
        response = client.get(
            "/api/submissions/progress?search=Progress+Test",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 200
        assert response.json()["total"] >= 1

    def test_get_progress_by_admission_id(self, client, auth_token, admission_id):
        """Test getting progress detail by admission ID (no progress record yet)."""
        response = client.get(
            f"/api/submissions/progress/{admission_id}",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["child_name"] == "Progress Test Child"
        assert data["progress_id"] is None
        assert data["class_name"] == calculate_eligible_class(
            "2019-01-01", current_eligibility_year()
        )

    def test_get_progress_not_found(self, client, auth_token):
        response = client.get(
            "/api/submissions/progress/999999",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 404

    def test_upsert_creates_progress(self, client, auth_token, admission_id):
        """Test PUT auto-creates a progress record if none exists."""
        response = client.put(
            f"/api/submissions/progress/{admission_id}",
            json={"class_name": "Nursery", "form_status": "Complete"},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["class_name"] == "Nursery"
        assert data["form_status"] == "Complete"
        assert data["progress_id"] is not None
        assert data["child_name"] == "Progress Test Child"

    def test_upsert_updates_existing(self, client, auth_token, admission_id):
        """Test PUT updates an existing progress record."""
        # Create first
        client.put(
            f"/api/submissions/progress/{admission_id}",
            json={"class_name": "Nursery"},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        # Update
        response = client.put(
            f"/api/submissions/progress/{admission_id}",
            json={"class_name": "III", "acceptance": "Accepted", "remarks": "Great student"},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["class_name"] == "III"
        assert data["acceptance"] == "Accepted"
        assert data["remarks"] == "Great student"

    def test_progress_pdf_export(self):
        db = TestSessionLocal()
        try:
            row = AdmissionSubmission(
                session="2025-2026",
                child_name="Progress PDF Test Child",
                dob="2022-09-08",
                address="Test Address",
                applied_before="no",
                special_needs="no",
                mother_name="Test Mother",
                father_name="Test Father",
                emergency_name="Test Emergency",
                emergency_phone="03001234567",
                declaration=True,
                signature="Test Parent",
                created_at=datetime(2026, 1, 1),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            progress = AdmissionProgress(
                admission_id=row.id,
                class_name="Nursery",
                remarks="Ready for review",
            )
            db.add(progress)
            db.commit()

            response = asyncio.run(
                main.download_progress_pdf(row.id, username="testadmin", db=db)
            )
            inline_response = asyncio.run(
                main.download_progress_pdf(row.id, inline=True, username="testadmin", db=db)
            )
            admission_id = row.id
        finally:
            db.close()

        assert response.media_type == "application/pdf"
        assert response.headers["content-disposition"] == (
            f'attachment; filename="admission_progress_{admission_id}.pdf"'
        )
        assert response.body.startswith(b"%PDF")
        assert inline_response.headers["content-disposition"] == (
            f'inline; filename="admission_progress_{admission_id}.pdf"'
        )
        assert inline_response.body.startswith(b"%PDF")

    def test_upsert_not_found(self, client, auth_token):
        response = client.put(
            "/api/submissions/progress/999999",
            json={"class_name": "IV"},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 404

    def test_delete_admission_removes_admission_and_progress(self, client, auth_token, admission_id):
        """Deleting an admission also removes its progress record."""
        client.put(
            f"/api/submissions/progress/{admission_id}",
            json={"class_name": "II"},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        response = client.delete(
            f"/api/submissions/admissions/{admission_id}",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True
        assert client.get(
            f"/api/submissions/admissions/{admission_id}",
            headers={"Authorization": f"Bearer {auth_token}"},
        ).status_code == 404
        list_resp = client.get(
            "/api/submissions/progress",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert all(item["admission_id"] != admission_id for item in list_resp.json()["items"])

    def test_delete_admission_without_progress(self, client, auth_token, admission_id):
        """Deleting an admission that has no progress record still succeeds."""
        response = client.delete(
            f"/api/submissions/admissions/{admission_id}",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True
        assert client.get(
            f"/api/submissions/admissions/{admission_id}",
            headers={"Authorization": f"Bearer {auth_token}"},
        ).status_code == 404

    def test_delete_admission_not_found(self, client, auth_token):
        """Deleting an unknown admission id returns 404."""
        response = client.delete(
            "/api/submissions/admissions/999999",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 404

    def test_delete_admission_requires_auth(self, client):
        """Delete without auth is rejected."""
        assert client.delete("/api/submissions/admissions/1").status_code == 401

    def test_progress_requires_auth(self, client):
        """Test that all progress endpoints require authentication."""
        assert client.get("/api/submissions/progress").status_code == 401
        assert client.get("/api/submissions/progress/1").status_code == 401
        assert client.put("/api/submissions/progress/1", json={}).status_code == 401
