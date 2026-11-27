import pytest
from django.core.cache import cache
from rest_framework.test import APIClient


@pytest.fixture(autouse=True)
def _clear_cache():
    # Rate limits are counted in the cache; start every test with a clean count.
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture(autouse=True)
def _no_real_pushes(settings, monkeypatch):
    # Tests never reach Firebase, even when .env points at the real push key (slice 7).
    from notifications import push

    settings.FIREBASE_CREDENTIALS_PATH = ""
    monkeypatch.setattr(push, "_app", None)
