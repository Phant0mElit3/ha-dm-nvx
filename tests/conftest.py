"""Small fixtures using real Home Assistant classes and mocked device I/O."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.crestron_nvx.crestron_nvx_api import CrestronNVXDevice


@pytest.fixture
async def hass(tmp_path):
    instance = HomeAssistant(str(tmp_path))
    yield instance
    await instance.async_stop()


@pytest.fixture
def device():
    device = CrestronNVXDevice("192.0.2.10", "admin", "secret", MagicMock(), name="Display")
    device.device_mode = "Receiver"
    device.serial_number = "SERIAL123"
    return device


@pytest.fixture
def coordinator(device):
    return SimpleNamespace(
        device=device, data={}, last_update_success=True, async_request_refresh=AsyncMock()
    )
