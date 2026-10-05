import random

import pytest

import ax25build


def pytest_report_header(config):
    """Record exactly which OBC code these results are for."""
    commit, dirty = ax25build.obc_revision()
    note = " (lib/ax25 has UNCOMMITTED changes)" if dirty else ""
    return [f"OBC repo: {ax25build.OBC_REPO}", f"OBC commit: {commit}{note}"]


@pytest.fixture
def rng():
    """Seeded so any failure reproduces."""
    return random.Random(0xA25)


@pytest.fixture(scope="session")
def sender():
    """Path to the C sender, built once per test run against the OBC repo."""
    return ax25build.build_sender()
