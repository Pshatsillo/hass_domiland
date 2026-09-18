"""Инициализация интеграции Domyland."""

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_entry_oauth2_flow, config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DomylandApiClient, DomylandApiError
from .const import DOMAIN
from .coordinator import DomylandDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["sensor", "number"]

SERVICE_SUBMIT_METERING = "submit_metering"

SUBMIT_METERING_SCHEMA = vol.Schema(
    {
        vol.Required("group_id"): cv.positive_int,
        vol.Required("device_id"): cv.positive_int,
        vol.Required("values"): vol.All(
            dict,
            vol.Schema({cv.positive_int: cv.string}),
        ),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Настройка Domyland из записи конфигурации."""
    # Создаём OAuth2-сессию HA (она сама обновляет Яндекс-токен)
    implementation = (
        await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
    )
    oauth_session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)

    session = async_get_clientsession(hass)

    # API-клиент использует токен Домиленда (он уже в entry.data)
    api = DomylandApiClient(
        session=session,
        token=entry.data["domyland_token"],
        place_id=entry.data["place_id"],
        building_id=entry.data["building_id"],
    )

    coordinator = DomylandDataUpdateCoordinator(hass, api)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "oauth_session": oauth_session,
    }

    # Регистрируем сервис
    if not hass.services.has_service(DOMAIN, SERVICE_SUBMIT_METERING):

        async def _handle_submit_metering(call: ServiceCall) -> None:
            """Обработчик сервиса submit_metering."""
            group_id = call.data["group_id"]
            device_id = call.data["device_id"]
            values = {int(k): v for k, v in call.data["values"].items()}

            for entry_data in hass.data[DOMAIN].values():
                coord = entry_data.get("coordinator")
                if not coord:
                    continue
                groups = coord.data.get("metering_groups", [])
                if any(g["id"] == group_id for g in groups):
                    try:
                        await coord.api.submit_metering(
                            metering_group_id=group_id,
                            metering_device_id=device_id,
                            values=values,
                        )
                        await coord.async_request_refresh()
                    except DomylandApiError as ex:
                        _LOGGER.error("Service submit_metering failed: %s", ex)
                    return
            _LOGGER.error("No coordinator found for group_id=%s", group_id)

        hass.services.async_register(
            DOMAIN,
            SERVICE_SUBMIT_METERING,
            _handle_submit_metering,
            schema=SUBMIT_METERING_SCHEMA,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Выгрузка записи конфигурации."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
