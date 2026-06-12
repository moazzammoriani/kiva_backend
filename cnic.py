import re
from typing import Optional


def normalize_cnic(value: Optional[str]) -> Optional[str]:
    """Return a digits-only CNIC, or None when the value contains no digits."""
    if value is None:
        return None
    digits = re.sub(r"[^0-9]", "", str(value))
    return digits or None
