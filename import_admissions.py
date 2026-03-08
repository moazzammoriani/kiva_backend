"""Import admission application form submissions from old website CSV export.

The CSV has duplicate column headers (Organization, Last Education, Email Address, etc.)
so this script uses column indices rather than header names.

Usage:
    uv run python import_admissions.py --csv ../data_from_their_old_website/admission-application-form-2026-03-07.csv
"""

import argparse
import csv
from datetime import datetime

from database import engine, SessionLocal, AdmissionSubmission, Base

# Column indices (verified from CSV header)
COL = {
    "session": 0,
    "child_name": 1,
    "dob": 2,
    "applied_before": 5,
    "previous_school": 6,
    "previous_class": 7,
    "has_report": 8,
    "medical_info": 10,
    "special_needs_yesno": 11,
    "special_needs_details": 12,
    "address": 13,
    "mother_name": 14,
    "mother_profession": 15,
    "mother_organization": 16,
    "mother_education": 17,
    "mother_email": 19,
    "mother_phone": 20,
    "mother_cnic": 21,
    "father_name": 22,
    "father_profession": 23,
    "father_organization": 24,
    "father_education": 25,
    "father_email": 27,
    "father_phone": 28,
    "father_cnic": 29,
    "sibling_name": 30,
    "sibling_grade": 31,
    "sibling_school": 32,
    "emergency_name": 33,
    "emergency_phone": 34,
    "hear_about": 35,
    "fit_response": 36,
    "reason": 37,
    "declaration": 38,
    "signature": 39,
    "created_at": 60,
}


def parse_datetime(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")


def col(row: list[str], key: str) -> str:
    """Get column value by key, stripped. Returns empty string if index out of range."""
    idx = COL[key]
    if idx >= len(row):
        return ""
    return row[idx].strip()


def main():
    parser = argparse.ArgumentParser(description="Import admission CSV into database")
    parser.add_argument("--csv", required=True, help="Path to CSV file")
    parser.add_argument("--db", default="kiva.db", help="SQLite database path (default: kiva.db)")
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)

    with open(args.csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        rows = list(reader)

    db = SessionLocal()
    imported = 0
    skipped = 0
    errors = 0

    try:
        for i, row in enumerate(rows, start=2):  # line 2+ (1-indexed, after header)
            try:
                child_name = col(row, "child_name")
                created_at_str = col(row, "created_at")

                if not child_name or not created_at_str:
                    errors += 1
                    continue

                created_at = parse_datetime(created_at_str)

                # Dedup check
                existing = (
                    db.query(AdmissionSubmission)
                    .filter(
                        AdmissionSubmission.child_name == child_name,
                        AdmissionSubmission.created_at == created_at,
                    )
                    .first()
                )
                if existing:
                    skipped += 1
                    continue

                # Combine special needs yes/no with details
                special_needs_yn = col(row, "special_needs_yesno")
                special_needs_details = col(row, "special_needs_details")
                if special_needs_yn.lower() == "yes" and special_needs_details:
                    special_needs = special_needs_details
                else:
                    special_needs = special_needs_yn or "no"

                submission = AdmissionSubmission(
                    session=col(row, "session") or "Unknown",
                    child_name=child_name,
                    dob=col(row, "dob") or "Unknown",
                    address=col(row, "address") or "Not provided",
                    applied_before=col(row, "applied_before") or "Unknown",
                    previous_school=col(row, "previous_school") or None,
                    previous_class=col(row, "previous_class") or None,
                    has_report=col(row, "has_report") or None,
                    progress_report_path=None,
                    reason=col(row, "reason") or None,
                    medical_info=col(row, "medical_info") or None,
                    special_needs=special_needs,
                    mother_name=col(row, "mother_name") or "Not provided",
                    mother_profession=col(row, "mother_profession") or None,
                    mother_education=col(row, "mother_education") or None,
                    mother_organization=col(row, "mother_organization") or None,
                    mother_email=col(row, "mother_email") or None,
                    mother_phone=col(row, "mother_phone") or None,
                    mother_cnic=col(row, "mother_cnic") or None,
                    father_name=col(row, "father_name") or "Not provided",
                    father_profession=col(row, "father_profession") or None,
                    father_education=col(row, "father_education") or None,
                    father_organization=col(row, "father_organization") or None,
                    father_email=col(row, "father_email") or None,
                    father_phone=col(row, "father_phone") or None,
                    father_cnic=col(row, "father_cnic") or None,
                    sibling_name=col(row, "sibling_name") or None,
                    sibling_grade=col(row, "sibling_grade") or None,
                    sibling_school=col(row, "sibling_school") or None,
                    emergency_name=col(row, "emergency_name") or "Not provided",
                    emergency_phone=col(row, "emergency_phone") or "Not provided",
                    hear_about=col(row, "hear_about") or None,
                    fit_response=col(row, "fit_response") or None,
                    declaration=col(row, "declaration").lower() == "accepted",
                    signature=col(row, "signature") or "Not provided",
                    created_at=created_at,
                )
                db.add(submission)
                imported += 1

            except Exception as e:
                print(f"Error on row {i}: {e}")
                errors += 1

        db.commit()
    finally:
        db.close()

    print(f"Imported {imported} admissions, skipped {skipped} duplicates, {errors} errors (from {len(rows)} rows)")


if __name__ == "__main__":
    main()
