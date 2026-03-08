"""Import contact form submissions from old website CSV exports into the database.

Works with both the main contact form and the Kiva Square contact form CSVs.
The script auto-detects which format is being imported based on the CSV headers.

Usage:
    uv run python import_contacts.py --csv ../data_from_their_old_website/contact-form-2026-03-07.csv
    uv run python import_contacts.py --csv ../data_from_their_old_website/kiva-square-contact-form-2026-03-07.csv
"""

import argparse
import csv
from datetime import datetime

from database import engine, SessionLocal, ContactSubmission, Base


# Column name mappings for each CSV format
COLUMN_MAPS = {
    "contact": {
        "name": "Name",
        "email": "Email",
        "subject": "Subject",
        "phone": "Phone/Mobile",
        "message": "Message",
        "created_at": "created_at",
    },
    "kiva_square": {
        "name": "Your Name",
        "email": "Email",
        "subject": "You want to know about",
        "phone": "Phone/Mobile",
        "message": "Message",
        "created_at": "created_at",
    },
}


def detect_format(headers: list[str]) -> dict[str, str]:
    if "Your Name" in headers:
        return COLUMN_MAPS["kiva_square"]
    return COLUMN_MAPS["contact"]


def parse_datetime(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")


def main():
    parser = argparse.ArgumentParser(description="Import contact form CSV into database")
    parser.add_argument("--csv", required=True, help="Path to CSV file")
    parser.add_argument("--db", default="kiva.db", help="SQLite database path (default: kiva.db)")
    args = parser.parse_args()

    # Ensure tables exist
    Base.metadata.create_all(bind=engine)

    with open(args.csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        col_map = detect_format(reader.fieldnames or [])

        rows = list(reader)

    db = SessionLocal()
    imported = 0
    skipped = 0

    try:
        for row in rows:
            name = row[col_map["name"]].strip()
            email = row[col_map["email"]].strip()
            created_at = parse_datetime(row[col_map["created_at"]])

            # Dedup check
            existing = (
                db.query(ContactSubmission)
                .filter(
                    ContactSubmission.name == name,
                    ContactSubmission.email == email,
                    ContactSubmission.created_at == created_at,
                )
                .first()
            )
            if existing:
                skipped += 1
                continue

            submission = ContactSubmission(
                name=name,
                email=email,
                subject=row[col_map["subject"]].strip() or None,
                phone=row[col_map["phone"]].strip() or None,
                message=row[col_map["message"]].strip(),
                created_at=created_at,
            )
            db.add(submission)
            imported += 1

        db.commit()
    finally:
        db.close()

    print(f"Imported {imported} contacts, skipped {skipped} duplicates (from {len(rows)} rows)")


if __name__ == "__main__":
    main()
