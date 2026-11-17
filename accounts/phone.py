"""
Indian mobile numbers (decision §15).

We store exactly one format: +91 followed by 10 digits, the first being 6-9.
The app already cleans what the farmer types; the server checks again because
a check in the app alone can always be skipped.
"""

import re

INDIAN_MOBILE = re.compile(r"^\+91[6-9]\d{9}$")


def is_indian_mobile(phone: str | None) -> bool:
    return bool(phone) and bool(INDIAN_MOBILE.match(phone))
