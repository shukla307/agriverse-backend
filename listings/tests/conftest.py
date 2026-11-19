import pytest

from accounts.models import Address, User
from catalog.models import MachineType

BUCKET = "agriverse-main.firebasestorage.app"


def storage_url(path: str) -> str:
    return f"https://firebasestorage.googleapis.com/v0/b/{BUCKET}/o/{path.replace('/', '%2F')}?alt=media&token=t"


def photo(uid="uid-owner", draft="draft1", name="a"):
    full = f"listings/{uid}/{draft}/{name}_full.jpg"
    return {
        "url": storage_url(full),
        "thumb_url": storage_url(full.replace("_full.jpg", "_thumb.jpg")),
        "storage_path": full,
    }


def mt(code) -> MachineType:
    return MachineType.objects.get(code=code)


@pytest.fixture
def owner(db):
    user = User.objects.create_user(phone="+919876543210", firebase_uid="uid-owner", name="Ramesh")
    home = Address.objects.create(
        user=user, label="home", lat="25.438000", lng="81.905000", village="झूंसी", state="उत्तर प्रदेश",
        landmark="मंदिर के पास",
    )
    user.home_address = home
    user.save()
    return user


@pytest.fixture
def stranger(db):
    return User.objects.create_user(phone="+919812345678", firebase_uid="uid-stranger", name="Suresh")


@pytest.fixture
def me(api, owner):
    api.force_authenticate(owner)
    return api


def tractor_payload(**extra):
    """The roadmap's example: a tractor with 2 attachments."""
    body = {
        "machine_type_id": mt("tractor").id,
        "brand": "Mahindra", "model": "575 DI", "hp": 45, "year": 2019,
        "diesel_included": False, "transport_included": True, "gap_hours": 1,
        "options": [
            {"implement_id": None, "price_unit": "hour", "price": "600", "min_hours": 2},
            {"implement_id": mt("rotavator").id, "price_unit": "hour", "price": "1000", "min_hours": 2},
            {"implement_id": mt("rotavator").id, "price_unit": "acre", "price": "1500"},
            {"implement_id": mt("cultivator").id, "price_unit": "hour", "price": "800"},
        ],
    }
    body.update(extra)
    return body


@pytest.fixture
def listing_id(me):
    res = me.post("/api/listings/", tractor_payload(), format="json")
    assert res.status_code == 201, res.json()
    return res.json()["id"]
