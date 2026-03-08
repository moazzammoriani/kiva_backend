# Data Migration from Old Website

The old Kiva School website (WordPress with FluentForms) exported form submission data as CSV files. Two Python scripts import this historical data into the SQLite database used by the FastAPI backend.

## CSV files → Database tables

| CSV file | Target table | Rows |
|----------|-------------|------|
| `contact-form-2026-03-07.csv` | `contact_submissions` | ~72 |
| `kiva-square-contact-form-2026-03-07.csv` | `contact_submissions` | ~11 |
| `admission-application-form-2026-03-07.csv` | `admission_submissions` | ~973 |
| `summer-camp-2026-03-07.csv` | _(skipped — no matching table)_ | ~22 |

The JSON files in the same directory are FluentForms form definitions (not submission data) and can be ignored.

## Prerequisites

```bash
cd kiva_backend
uv sync  # ensure dependencies are installed
```

## Running the imports

### Contact forms

`import_contacts.py` handles both contact form formats. It auto-detects which CSV format is being imported based on the headers.

```bash
uv run python import_contacts.py --csv ../data_from_their_old_website/contact-form-2026-03-07.csv
uv run python import_contacts.py --csv ../data_from_their_old_website/kiva-square-contact-form-2026-03-07.csv
```

**Column mappings:**

| Contact form CSV | Kiva Square CSV | DB column |
|-----------------|----------------|-----------|
| `Name` | `Your Name` | `name` |
| `Email` | `Email` | `email` |
| `Subject` | `You want to know about` | `subject` |
| `Phone/Mobile` | `Phone/Mobile` | `phone` |
| `Message` | `Message` | `message` |
| `created_at` | `created_at` | `created_at` |

### Admission applications

`import_admissions.py` uses column indices (not header names) because the CSV has duplicate column headers (multiple "Organization", "Last Education", "Email Address" columns for mother vs father).

```bash
uv run python import_admissions.py --csv ../data_from_their_old_website/admission-application-form-2026-03-07.csv
```

Notable behavior:
- The `special_needs` field combines the yes/no answer with the details field (if "yes", stores the details text; otherwise stores "no")
- `declaration` maps "Accepted" → `True`
- `progress_report_path` is always NULL (file uploads aren't in the CSV export)
- Required fields that are empty in the CSV get a "Not provided" or "Unknown" placeholder

## Idempotency

Both scripts are safe to re-run. They check for duplicates before inserting:
- **Contacts:** dedup by `name` + `email` + `created_at`
- **Admissions:** dedup by `child_name` + `created_at`

Re-running will report "skipped N duplicates" and import 0 new rows.

## Verifying the import

```bash
uv run python -c "
from database import *
init_db()
db = SessionLocal()
print('contacts:', db.query(ContactSubmission).count())
print('admissions:', db.query(AdmissionSubmission).count())
"
```
