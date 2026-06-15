from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


PAKISTAN_TIMEZONE = ZoneInfo("Asia/Karachi")
OUTSIDE_ELIGIBLE_RANGE = "Outside eligible class range"

CLASS_AGE_RANGES = (
    ("Play Group", 1.5, 2.5),
    ("Pre-Nursery", 2.5, 3.5),
    ("Nursery", 3.5, 4.5),
    ("Prep", 4.5, 5.5),
    ("I", 5.5, 6.5),
    ("II", 6.5, 7.5),
    ("III", 7.5, 8.5),
    ("IV", 8.5, 9.5),
    ("V", 9.5, 10.5),
)


def parse_dob(value: str | date | datetime | None) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None

    stripped = value.strip()
    try:
        return date.fromisoformat(stripped[:10])
    except ValueError:
        pass

    for date_format in ("%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(stripped, date_format).date()
        except ValueError:
            continue
    return None


def current_eligibility_year() -> int:
    return datetime.now(PAKISTAN_TIMEZONE).year


def eligibility_year_for_submission(created_at: datetime | None) -> int:
    if not created_at:
        return current_eligibility_year()
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return created_at.astimezone(PAKISTAN_TIMEZONE).year


def calculate_decimal_age(dob: str | date | datetime | None, eligibility_year: int) -> float | None:
    birth_date = parse_dob(dob)
    if not birth_date:
        return None
    cutoff = date(eligibility_year, 7, 1)
    return (cutoff - birth_date).days / 365.2425


def eligible_class_for_age(age: float) -> str:
    for class_name, minimum_age, maximum_age in CLASS_AGE_RANGES:
        if minimum_age <= age < maximum_age:
            return class_name
    return OUTSIDE_ELIGIBLE_RANGE


def calculate_eligible_class(dob: str | date | datetime | None, eligibility_year: int) -> str | None:
    age = calculate_decimal_age(dob, eligibility_year)
    if age is None:
        return None
    return eligible_class_for_age(age)
