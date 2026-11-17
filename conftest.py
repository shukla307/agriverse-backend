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
