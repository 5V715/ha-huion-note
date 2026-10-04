"""Config flow: discovered over Bluetooth (or picked from nearby devices)."""
from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
    async_last_service_info,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_COOLDOWN,
    CONF_DELETE_AFTER_SYNC,
    CONF_LINE_WIDTH,
    CONF_OUTPUT_DIR,
    CONF_PIN,
    DEFAULT_COOLDOWN,
    DOMAIN,
)
from .coordinator import default_output_dir
from .protocol.render import DEFAULT_LINE_WIDTH_MM


DEFAULT_NAME = "Huion Note X10"
MAX_NEARBY = 50
_ADDRESS_RE = re.compile(r"[0-9A-F]{2}(:[0-9A-F]{2}){5}")


def is_huion(info: BluetoothServiceInfoBleak) -> bool:
    return "huion" in (info.name or "").lower()


def normalize_address(value: str) -> str | None:
    """'aa-bb-cc-dd-ee-ff' / 'aabbccddeeff' / 'AA:BB:…' -> 'AA:BB:CC:DD:EE:FF'."""
    raw = value.strip().upper().replace("-", ":")
    if ":" not in raw and len(raw) == 12:
        raw = ":".join(raw[i : i + 2] for i in range(0, 12, 2))
    return raw if _ADDRESS_RE.fullmatch(raw) else None


def _label(info: BluetoothServiceInfoBleak) -> str:
    """The advertised name, or a hint when there is none (HA then uses the address)."""
    if info.name and normalize_address(info.name) is None:
        return info.name
    return "no name"


class HuionNoteConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovery: BluetoothServiceInfoBleak | None = None
        self._devices: dict[str, str] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        if not is_huion(discovery_info):
            return self.async_abort(reason="not_supported")
        self._discovery = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._discovery is not None
        if user_input is not None:
            return self.async_create_entry(
                title=self._discovery.name,
                data={CONF_ADDRESS: self._discovery.address},
            )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": self._discovery.name},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer the Huion devices Home Assistant can see, or manual entry."""
        current = self._async_current_ids(include_ignore=False)
        for info in async_discovered_service_info(self.hass, connectable=True):
            if info.address not in current and is_huion(info):
                self._devices[info.address] = info.name
        if not self._devices:
            return await self.async_step_manual()
        return self.async_show_menu(step_id="user", menu_options=["pick_device", "manual"])

    async def async_step_pick_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=self._devices[address], data={CONF_ADDRESS: address}
            )
        return self.async_show_form(
            step_id="pick_device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {a: f"{n} ({a})" for a, n in self._devices.items()}
                    )
                }
            ),
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add the notebook by Bluetooth address — for when Home Assistant doesn't
        see its name (e.g. the name is only in the scan response and the adapter or
        proxy scans passively). The notebook needn't be in range right now."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = normalize_address(user_input[CONF_ADDRESS])
            if address is None:
                errors[CONF_ADDRESS] = "invalid_address"
            else:
                await self.async_set_unique_id(address, raise_on_progress=False)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=self._name_for(address), data={CONF_ADDRESS: address}
                )

        current = self._async_current_ids(include_ignore=False)
        nearby = sorted(
            (
                i
                for i in async_discovered_service_info(self.hass, connectable=True)
                if i.address not in current
            ),
            key=lambda i: i.rssi,
            reverse=True,
        )[:MAX_NEARBY]
        options = [
            SelectOptionDict(
                value=i.address,
                label=f"{i.address} — {_label(i)} ({i.rssi} dBm)",
            )
            for i in nearby
        ]
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ADDRESS, default=(user_input or {}).get(CONF_ADDRESS, "")
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            custom_value=True,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            errors=errors,
        )

    def _name_for(self, address: str) -> str:
        info = async_last_service_info(self.hass, address, connectable=True)
        if info and info.name and normalize_address(info.name) is None:
            return info.name
        return DEFAULT_NAME

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HuionNoteOptionsFlow()


class HuionNoteOptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            pin = user_input.get(CONF_PIN, "")
            if pin and (len(pin) != 6 or not pin.isdigit()):
                errors[CONF_PIN] = "invalid_pin"
            else:
                return self.async_create_entry(data=user_input)

        opts = {**self.config_entry.options, **(user_input or {})}
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_DELETE_AFTER_SYNC,
                    default=opts.get(CONF_DELETE_AFTER_SYNC, False),
                ): bool,
                vol.Optional(
                    CONF_COOLDOWN, default=opts.get(CONF_COOLDOWN, DEFAULT_COOLDOWN)
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
                vol.Optional(
                    CONF_LINE_WIDTH,
                    default=opts.get(CONF_LINE_WIDTH, DEFAULT_LINE_WIDTH_MM),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.05, max=2.0)),
                vol.Optional(
                    CONF_OUTPUT_DIR,
                    default=opts.get(CONF_OUTPUT_DIR) or default_output_dir(self.hass),
                ): str,
                vol.Optional(
                    CONF_PIN, description={"suggested_value": opts.get(CONF_PIN, "")}
                ): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
