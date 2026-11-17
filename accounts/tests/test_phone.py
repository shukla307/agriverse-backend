import pytest

from accounts.phone import is_indian_mobile


@pytest.mark.parametrize("phone", ["+919876543210", "+916000000000", "+917123456789", "+918999999999"])
def test_accepts_indian_mobiles(phone):
    assert is_indian_mobile(phone)


@pytest.mark.parametrize(
    "phone",
    [
        None, "", "9876543210",        # no +91
        "+915876543210",               # starts with 5
        "+91987654321",                # 9 digits
        "+9198765432101",              # 11 digits
        "+14155550100",                # US number
        "+91 9876543210",              # space (the app cleans this, the server doesn't guess)
    ],
)
def test_refuses_everything_else(phone):
    assert not is_indian_mobile(phone)
