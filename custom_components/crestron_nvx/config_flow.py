"""Configuration, reauthentication and options for Crestron DM NVX."""

from __future__ import annotations

import ipaddress
import logging
import re
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .const import CONF_ENABLE_PREVIEW_CAMERA, CONF_SCAN_INTERVAL, CONF_VERIFY_SSL, DOMAIN
from .crestron_nvx_api import CrestronNVXAuthError, CrestronNVXConnectionError, CrestronNVXDevice

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL_SCHEMA = vol.All(vol.Coerce(int), vol.Range(min=10, max=300))


def normalize_host(value: str) -> str:
    """Accept an IP or DNS name, never URLs, paths or embedded credentials."""
    host = value.strip().lower()
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        if len(host) > 253 or not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in host.rstrip(".").split(".")
        ):
            raise ValueError("Enter an IP address or hostname") from None
        return host.rstrip(".")
    return f"[{address}]" if address.version == 6 else str(address)


def device_schema(defaults: dict | None = None) -> vol.Schema:
    """Never populate a password back into a form."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "")): vol.All(
                str, vol.Length(min=1)
            ),
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): vol.All(
                str, vol.Length(min=1)
            ),
            vol.Required(CONF_PASSWORD): str,
            vol.Optional(CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, False)): bool,
            vol.Optional(
                CONF_SCAN_INTERVAL, default=defaults.get(CONF_SCAN_INTERVAL, 30)
            ): SCAN_INTERVAL_SCHEMA,
        }
    )


class CrestronNVXConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Manage one NVX device per config entry."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> CrestronNVXOptionsFlow:
        return CrestronNVXOptionsFlow()

    async def _validate(self, user_input: dict) -> tuple[dict, str | None]:
        data = {**user_input}
        try:
            data[CONF_HOST] = normalize_host(data[CONF_HOST])
        except ValueError:
            return data, "invalid_host"
        data[CONF_NAME] = data[CONF_NAME].strip()
        data[CONF_USERNAME] = data[CONF_USERNAME].strip()
        if not data[CONF_NAME] or not data[CONF_USERNAME]:
            return data, "invalid_name"
        session = async_create_clientsession(
            self.hass,
            verify_ssl=data.get(CONF_VERIFY_SSL, False),
            auto_cleanup=False,
            cookie_jar=aiohttp.CookieJar(unsafe=True),
        )
        device = CrestronNVXDevice(
            host=data[CONF_HOST],
            username=data[CONF_USERNAME],
            password=data[CONF_PASSWORD],
            session=session,
            verify_ssl=data.get(CONF_VERIFY_SSL, False),
        )
        try:
            await device.login()
            data.update(device_mode=device.device_mode, serial_number=device.serial_number)
            return data, None
        except CrestronNVXAuthError:
            return data, "invalid_auth"
        except CrestronNVXConnectionError:
            return data, "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error validating NVX device")
            return data, "unknown"
        finally:
            try:
                await device.logout()
            finally:
                await session.close()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors = {}
        if user_input is not None:
            data, error = await self._validate(user_input)
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(data.get("serial_number") or data[CONF_HOST])
                self._abort_if_unique_id_configured()
                # Older entries used the host itself as their config-entry ID.
                for entry in self._async_current_entries():
                    if any(
                        item[CONF_HOST].lower() == data[CONF_HOST]
                        for item in entry.data.get("devices", [])
                    ):
                        return self.async_abort(reason="already_configured")
                data["entity_id_prefix"] = data[CONF_HOST]
                return self.async_create_entry(
                    title=data[CONF_NAME],
                    data={
                        "devices": [data],
                        CONF_SCAN_INTERVAL: data.get(CONF_SCAN_INTERVAL, 30),
                    },
                )
        return self.async_show_form(
            step_id="user", data_schema=device_schema(user_input), errors=errors
        )

    async def async_step_reconfigure(self, user_input: dict | None = None) -> FlowResult:
        return await self._update_device("reconfigure", user_input)

    async def async_step_reauth(self, entry_data: dict) -> FlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict | None = None) -> FlowResult:
        return await self._update_device("reauth_confirm", user_input)

    async def _update_device(self, step_id: str, user_input: dict | None) -> FlowResult:
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None or len(entry.data.get("devices", [])) != 1:
            return self.async_abort(reason="unsupported_config")
        previous = entry.data["devices"][0]
        errors = {}
        if user_input is not None:
            data, error = await self._validate(user_input)
            if error:
                errors["base"] = error
            else:
                serial = previous.get("serial_number")
                if not serial and entry.unique_id != previous[CONF_HOST].lower():
                    serial = entry.unique_id
                if serial and serial != data.get("serial_number"):
                    return self.async_abort(reason="wrong_device")
                if not serial and data[CONF_HOST] != previous[CONF_HOST].lower():
                    return self.async_abort(reason="identity_unavailable")
                data["entity_id_prefix"] = previous.get("entity_id_prefix", previous[CONF_HOST])
                return self.async_update_reload_and_abort(
                    entry,
                    title=data[CONF_NAME],
                    data={
                        **entry.data,
                        "devices": [{**previous, **data}],
                        CONF_SCAN_INTERVAL: data.get(CONF_SCAN_INTERVAL, 30),
                    },
                    options={**entry.options, CONF_SCAN_INTERVAL: data.get(CONF_SCAN_INTERVAL, 30)},
                    reason="reauth_successful"
                    if step_id == "reauth_confirm"
                    else "reconfigure_successful",
                )
        defaults = {
            **previous,
            CONF_SCAN_INTERVAL: entry.options.get(
                CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, 30)
            ),
        }
        return self.async_show_form(
            step_id=step_id, data_schema=device_schema(user_input or defaults), errors=errors
        )


class CrestronNVXOptionsFlow(config_entries.OptionsFlow):
    """Options read from the config entry provided by Home Assistant."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title="", data={**self.config_entry.options, **user_input}
            )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_ENABLE_PREVIEW_CAMERA,
                        default=self.config_entry.options.get(CONF_ENABLE_PREVIEW_CAMERA, False),
                    ): bool,
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, self.config_entry.data.get(CONF_SCAN_INTERVAL, 30)
                        ),
                    ): SCAN_INTERVAL_SCHEMA,
                }
            ),
        )
