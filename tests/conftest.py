from datetime import date

import pytest

from kestrel.data import load_catalog, load_orders

TODAY = date(2026, 12, 1)  # the brief's reference clock


@pytest.fixture(scope="session")
def orders():
    return load_orders()


@pytest.fixture(scope="session")
def catalog():
    return load_catalog()
