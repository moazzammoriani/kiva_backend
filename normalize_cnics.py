"""Normalize existing admission CNIC values to digits only.

Dry-run is the default. Pass --apply to commit changes.

Usage:
    uv run python normalize_cnics.py
    uv run python normalize_cnics.py --db /path/to/kiva.db --apply
"""

import argparse
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cnic import normalize_cnic
from database import AdmissionSubmission


CNIC_FIELDS = ("mother_cnic", "father_cnic")


def normalize_database(db_path: str | Path, apply: bool = False) -> dict[str, int]:
    engine = create_engine(
        f"sqlite:///{Path(db_path)}",
        connect_args={"check_same_thread": False},
    )
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = session_factory()
    changed_record_ids: set[int] = set()
    fields_changed = 0
    would_be_empty = 0
    invalid_length = 0

    try:
        rows = db.query(AdmissionSubmission).all()
        for row in rows:
            for field in CNIC_FIELDS:
                current = getattr(row, field)
                normalized = normalize_cnic(current)

                if normalized and len(normalized) != 13:
                    invalid_length += 1

                if normalized == current:
                    continue

                changed_record_ids.add(row.id)
                fields_changed += 1
                if current and normalized is None:
                    would_be_empty += 1
                if apply:
                    setattr(row, field, normalized)

        if apply:
            db.commit()
        else:
            db.rollback()

        return {
            "records_scanned": len(rows),
            "records_changed": len(changed_record_ids),
            "fields_changed": fields_changed,
            "would_be_empty": would_be_empty,
            "invalid_length": invalid_length,
        }
    finally:
        db.close()
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove non-digit characters from existing admission CNIC values",
    )
    parser.add_argument("--db", default="kiva.db", help="SQLite database path (default: kiva.db)")
    parser.add_argument("--apply", action="store_true", help="Commit normalized CNIC values")
    args = parser.parse_args()

    stats = normalize_database(args.db, apply=args.apply)
    mode = "APPLIED" if args.apply else "DRY RUN"
    print(f"CNIC normalization: {mode}")
    print(f"Records scanned: {stats['records_scanned']}")
    print(f"Records requiring changes: {stats['records_changed']}")
    print(f"Fields requiring changes: {stats['fields_changed']}")
    print(f"Values that would become empty: {stats['would_be_empty']}")
    print(f"Normalized values not exactly 13 digits: {stats['invalid_length']}")
    if not args.apply:
        print("No changes committed. Re-run with --apply to update the database.")


if __name__ == "__main__":
    main()
