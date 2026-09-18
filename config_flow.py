"""Настройка интеграции Domyland через OAuth2."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DomylandApiClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class DomylandOAuth2FlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Config flow для авторизации через Яндекс OAuth2."""

    DOMAIN = DOMAIN

    @property
    def logger(self) -> logging.Logger:
        """Логгер."""
        return _LOGGER

    async def async_oauth_create_entry(self, data: dict) -> FlowResult:
        """Вызывается после успешной OAuth2-авторизации.

        data содержит 'token' (dict с access_token, refresh_token и т.д.).
        Здесь мы обмениваем Яндекс-токен на токен Домиленда.
        """
        yandex_token = data["token"]["access_token"]
        _LOGGER.debug("Got Yandex OAuth token, exchanging for Domyland token")

        session = async_get_clientsession(self.hass)
        api = DomylandApiClient(session)

        # Генерируем customer_ext_id
        import uuid

        customer_ext_id = uuid.uuid4().hex

        # Обмен Яндекс-токена на токен Домиленда
        token_result = await api.exchange_yandex_token(yandex_token, customer_ext_id)

        if token_result is None:
            return self.async_abort(reason="cannot_connect")

        # Получаем places
        api.set_token(token_result["domyland_token"])
        places_result = await api.fetch_places_for_setup()

        if places_result is None:
            return self.async_abort(reason="no_places")

        address = places_result.get("address") or "без адреса"
        title = f"Domyland — {address}"

        # Сохраняем всё в entry.data
        entry_data = {
            **data,  # OAuth2-токены (access_token, refresh_token, expires_at)
            "domyland_token": token_result["domyland_token"],
            "customer_ext_id": customer_ext_id,
            "customer_id": token_result.get("customer_id"),
            "place_id": places_result["place_id"],
            "building_id": places_result["building_id"],
            "address": places_result["address"],
            "company_title": places_result["company_title"],
            "customer_short_name": places_result["customer_short_name"],
        }

        return self.async_create_entry(title=title, data=entry_data)
