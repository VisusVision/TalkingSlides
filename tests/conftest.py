import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def clear_default_cache_between_tests():
    cache.clear()
    yield
    cache.clear()
