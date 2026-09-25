"""Live model calls are opt-in and never load .env during default test runs."""

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="Call the model configured in the repository .env",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "live: calls the explicitly configured external model"
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-live"):
        skip = pytest.mark.skip(
            reason="requires --run-live and explicit model credentials"
        )
        for item in items:
            if "live" in item.keywords:
                item.add_marker(skip)
