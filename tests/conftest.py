"""All tests isolate application files from the user's real configured device."""

import pytest

from typhoon import config


@pytest.fixture(autouse=True)
def isolated_application_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "config_path", lambda: tmp_path / "Typhoon/device.json")
