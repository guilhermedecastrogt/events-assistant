import importlib

import pytest

import events_assistant

SUBPACKAGES = ["domain", "db", "mcp_server", "agent", "jobs", "api", "senders"]


def test_package_has_version():
    assert events_assistant.__version__


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackages_import(name):
    assert importlib.import_module(f"events_assistant.{name}")
