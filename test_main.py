import os
import io
import bcrypt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from main import app
from database import Base, get_db, AdminUser

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
    os.makedirs("uploads", exist_ok=True)
    yield


@pytest.fixture
def client():
    return TestClient(app)


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
                "specialNeeds": "no",
                # Mother's Details
                "motherName": "Sarah Wilson",
                "motherProfession": "Doctor",
                "motherEducation": "MBBS",
                "motherOrganization": "City Hospital",
                "motherEmail": "sarah@example.com",
                "motherPhone": "1111111111",
                "motherCnic": "12345-1234567-1",
                # Father's Details
                "fatherName": "David Wilson",
                "fatherProfession": "Engineer",
                "fatherEducation": "BSc Engineering",
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


@pytest.fixture
def admin_user():
    """Create a test admin user and clean up after."""
    db = TestSessionLocal()
    password_hash = bcrypt.hashpw(b"testpass123", bcrypt.gensalt()).decode()
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
        assert "address" not in item
        assert "mother_cnic" not in item

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
