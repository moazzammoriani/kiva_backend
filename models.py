from pydantic import BaseModel, EmailStr
from typing import Optional


class ContactForm(BaseModel):
    name: str
    email: EmailStr
    subject: Optional[str] = None
    phone: Optional[str] = None
    message: str


class CareerForm(BaseModel):
    name: str
    email: EmailStr
    phone: Optional[str] = None
    position: Optional[str] = None
    coverLetter: Optional[str] = None


class AdmissionForm(BaseModel):
    # Session
    session: str

    # Child's Information
    childName: str
    dob: str
    address: str
    appliedBefore: str
    previousSchool: Optional[str] = None
    previousClass: Optional[str] = None
    hasReport: Optional[str] = None
    reason: Optional[str] = None
    medicalInfo: Optional[str] = None
    specialNeeds: str

    # Mother's Details
    motherName: str
    motherProfession: Optional[str] = None
    motherEducation: Optional[str] = None
    motherOrganization: Optional[str] = None
    motherEmail: Optional[EmailStr] = None
    motherPhone: Optional[str] = None
    motherCnic: Optional[str] = None

    # Father's Details
    fatherName: str
    fatherProfession: Optional[str] = None
    fatherEducation: Optional[str] = None
    fatherOrganization: Optional[str] = None
    fatherEmail: Optional[EmailStr] = None
    fatherPhone: Optional[str] = None
    fatherCnic: Optional[str] = None

    # Sibling Information
    siblingName: Optional[str] = None
    siblingGrade: Optional[str] = None
    siblingSchool: Optional[str] = None

    # Emergency Contact
    emergencyName: str
    emergencyPhone: str

    # How did you hear about us
    hearAbout: Optional[list[str]] = None

    # Why Kiva is a good fit
    fitResponse: Optional[str] = None

    # Declaration
    declaration: bool
    signature: str
