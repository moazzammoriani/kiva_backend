from datetime import date, datetime, timedelta, timezone
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
    ("VI", 10.5, 11.5),
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


def calculate_age_parts(
    dob: str | date | datetime | None,
    eligibility_year: int,
) -> tuple[int, int, int] | None:
    return calculate_age_parts_on_date(dob, date(eligibility_year, 7, 1))


def calculate_age_parts_on_date(
    dob: str | date | datetime | None,
    as_of: date | datetime,
) -> tuple[int, int, int] | None:
    birth_date = parse_dob(dob)
    if not birth_date:
        return None

    cutoff = as_of.date() if isinstance(as_of, datetime) else as_of
    if birth_date > cutoff:
        return None

    years = cutoff.year - birth_date.year
    months = cutoff.month - birth_date.month
    days = cutoff.day - birth_date.day

    if days < 0:
        previous_month_last_day = cutoff.replace(day=1) - timedelta(days=1)
        days += previous_month_last_day.day
        months -= 1

    if months < 0:
        years -= 1
        months += 12

    return years, months, days


def format_age_parts(parts: tuple[int, int, int] | None) -> str | None:
    if parts is None:
        return None

    years, months, days = parts

    def unit(value: int, singular: str) -> str:
        suffix = "" if value == 1 else "s"
        return f"{value} {singular}{suffix}"

    return ", ".join((unit(years, "year"), unit(months, "month"), unit(days, "day")))


def format_age_on_date(
    dob: str | date | datetime | None,
    as_of: date | datetime,
) -> str | None:
    return format_age_parts(calculate_age_parts_on_date(dob, as_of))


def format_current_age(dob: str | date | datetime | None) -> str | None:
    return format_age_on_date(dob, datetime.now(PAKISTAN_TIMEZONE).date())


def format_age_on_july(dob: str | date | datetime | None, eligibility_year: int) -> str | None:
    return format_age_parts(calculate_age_parts(dob, eligibility_year))


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
