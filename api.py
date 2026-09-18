"""API-клиент для Domyland."""

import logging
from typing import Any

import aiohttp

from .const import API_BASE_URL, AUTH_HEADERS

_LOGGER = logging.getLogger(__name__)


class DomylandApiError(Exception):
    """Общая ошибка API Domyland."""


class DomylandAuthError(DomylandApiError):
    """Ошибка авторизации (401)."""


class DomylandApiClient:
    """Клиент для работы с API Domyland."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token: str | None = None,
        place_id: str | None = None,
        building_id: str | None = None,
    ) -> None:
        """Инициализация клиента."""
        self._session = session
        self._token = token
        self._place_id = place_id
        self._building_id = building_id

    @property
    def token(self) -> str | None:
        """Текущий токен Домиленда."""
        return self._token

    @property
    def place_id(self) -> str | None:
        """Текущий place_id."""
        return self._place_id

    @property
    def building_id(self) -> str | None:
        """Текущий building_id."""
        return self._building_id

    def set_token(self, token: str) -> None:
        """Устанавливает токен после обмена."""
        self._token = token

    def set_place(self, place_id: str, building_id: str) -> None:
        """Устанавливает текущее помещение."""
        self._place_id = place_id
        self._building_id = building_id

    def _auth_headers(self) -> dict[str, str]:
        """Заголовки для запросов авторизации."""
        return {
            **AUTH_HEADERS,
            "Content-Type": "application/json",
        }

    def _headers(self) -> dict[str, str]:
        """Заголовки для обычных API-запросов."""
        headers = {
            "AppName": "superdom-android",
            "Authorization": self._token,
            "Content-Type": "application/json",
        }
        if self._place_id:
            headers["placeId"] = self._place_id
        if self._building_id:
            headers["buildingId"] = self._building_id
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Выполняет обычный запрос к API."""
        url = f"{API_BASE_URL}{path}"

        _LOGGER.debug(
            "API request: %s %s | params=%s | json=%s | headers=%s",
            method,
            url,
            params,
            json_data,
            {
                k: ("<hidden>" if k == "Authorization" else v)
                for k, v in self._headers().items()
            },
        )

        try:
            async with self._session.request(
                method,
                url,
                headers=self._headers(),
                params=params,
                json=json_data,
            ) as resp:
                _LOGGER.debug(
                    "API response: %s %s → %s",
                    method,
                    url,
                    resp.status,
                )

                if resp.status == 401:
                    raise DomylandAuthError("Token expired or invalid")

                if resp.status != 200:
                    text = await resp.text()
                    _LOGGER.error(
                        "API error %s for %s %s: %s",
                        resp.status,
                        method,
                        url,
                        text,
                    )
                    raise DomylandApiError(f"API error {resp.status}: {text}")

                data = await resp.json()

                _LOGGER.debug(
                    "API response body for %s %s: %s",
                    method,
                    url,
                    data,
                )

                return data
        except aiohttp.ClientError as ex:
            raise DomylandApiError(f"Connection error: {ex}") from ex

    # --- Авторизация ---

    async def exchange_yandex_token(
        self, yandex_token: str, customer_ext_id: str
    ) -> dict[str, Any] | None:
        """Обменивает Яндекс-токен на токен Домиленда."""
        url = f"{API_BASE_URL}/auth/yandex/scenario"
        headers = {
            **self._auth_headers(),
            "customerExtId": customer_ext_id,
        }
        payload = {"token": yandex_token}

        _LOGGER.debug("Token exchange request: POST %s", url)

        try:
            async with self._session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    _LOGGER.error(
                        "Token exchange failed (%s): %s",
                        resp.status,
                        await resp.text(),
                    )
                    return None

                data = await resp.json()
                _LOGGER.debug("Token exchange response: %s", data)

                auth_data = data.get("data", {}).get("payload", {}).get("authData", {})
                token = auth_data.get("token")
                if not token:
                    _LOGGER.error("No token in response: %s", data)
                    return None

                return {
                    "domyland_token": token,
                    "customer_id": auth_data.get("customerId"),
                    "login_message": auth_data.get("loginMessage"),
                }
        except aiohttp.ClientError as ex:
            _LOGGER.error("Connection error during token exchange: %s", ex)
            return None

    async def fetch_places_for_setup(self) -> dict[str, Any] | None:
        """Запрашивает места для config_flow."""
        if not self._token:
            return None

        url = f"{API_BASE_URL}/current-customer/places"
        headers = {
            "AppName": "superdom-android",
            "Authorization": self._token,
        }

        _LOGGER.debug("Places fetch request: GET %s", url)

        try:
            async with self._session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    _LOGGER.error(
                        "Places fetch failed (%s): %s",
                        resp.status,
                        await resp.text(),
                    )
                    return None

                data = await resp.json()
                _LOGGER.debug("Places fetch response: %s", data)

                payload = data.get("data", {})
                places = payload.get("places", [])
                if not places:
                    _LOGGER.error("No places in response: %s", data)
                    return None

                first = places[0]
                place_id = first.get("id")
                building_id = first.get("buildingId")

                if place_id is None or building_id is None:
                    _LOGGER.error("placeId or buildingId missing in place: %s", first)
                    return None

                return {
                    "place_id": str(place_id),
                    "building_id": str(building_id),
                    "address": first.get("address"),
                    "company_title": first.get("companyTitle"),
                    "customer_short_name": payload.get("customerShortName"),
                }
        except aiohttp.ClientError as ex:
            _LOGGER.error("Connection error during places fetch: %s", ex)
            return None

    # --- Обычные API-методы ---

    async def get_current_customer(self) -> dict[str, Any]:
        """Получить данные текущего пользователя."""
        return await self._request("GET", "/current-customer")

    async def get_places(self) -> dict[str, Any]:
        """Получить список помещений пользователя."""
        return await self._request("GET", "/current-customer/places")

    async def get_metering_dashboard(
        self, is_general: bool = False
    ) -> list[dict[str, Any]]:
        """Получить дашборд счётчиков (только items)."""
        response = await self._request(
            "GET",
            "/meteringdata/dashboard",
            params={"isGeneral": str(is_general).lower()},
        )
        return response.get("data", {}).get("items", [])

    async def get_metering_form(self, metering_group_id: int) -> dict[str, Any]:
        """Получить форму для группы счётчиков."""
        return await self._request(
            "GET",
            "/meteringdata/form",
            params={"meteringGroupId": metering_group_id},
        )

    async def get_metering_history(
        self,
        metering_group_id: int,
        from_row: int = 0,
        period: str = "year",
    ) -> dict[str, Any]:
        """Получить историю показаний для группы счётчиков."""
        return await self._request(
            "GET",
            "/meteringdata/history",
            params={
                "meteringGroupId": metering_group_id,
                "fromRow": from_row,
                "period": period,
            },
        )

    async def submit_metering(
        self,
        metering_group_id: int,
        metering_device_id: int,
        values: dict[int, str],
    ) -> dict[str, Any]:
        """Передать показания счётчика.

        values: {1: "1750", 2: "800"} — тариф → значение.
        """
        payload: dict[str, Any] = {
            "meteringDeviceId": metering_device_id,
        }
        for i in range(1, 4):
            payload[f"value{i}"] = values.get(i, "0.0000")

        return await self._request(
            "POST",
            "/meteringdata/bunate",
            params={"meteringGroupId": metering_group_id},
            json_data={"meteringData": [payload]},
        )

    async def get_invoices(self) -> dict[str, Any]:
        """Получить счета к оплате."""
        return await self._request("GET", "/invoices/one-button-pay")

    async def get_invoices_init(self) -> dict[str, Any]:
        """Получить инициализацию раздела счетов."""
        return await self._request("GET", "/sections/invoices/init")

    async def get_home_init(self) -> dict[str, Any]:
        """Получить данные главного экрана."""
        return await self._request("GET", "/sections/home/init")

    async def get_profile_init(self) -> dict[str, Any]:
        """Получить данные профиля."""
        return await self._request("GET", "/sections/profile/init")

    async def get_orders(
        self,
        from_row: int = 0,
        search_query: str = "",
        order: str = "createdAt",
        sort: str = "desc",
        is_rate_needed: bool = False,
    ) -> dict[str, Any]:
        """Получить список заявок."""
        return await self._request(
            "GET",
            "/orders",
            params={
                "fromRow": from_row,
                "searchQuery": search_query,
                "order": order,
                "sort": sort,
                "isRateNeeded": str(is_rate_needed).lower(),
            },
        )

    async def get_cameras(self) -> dict[str, Any]:
        """Получить список камер."""
        return await self._request("GET", "/cameras")

    async def get_notifications(
        self, from_row: int = 0, row_limit: int = 20, order: str = "desc"
    ) -> dict[str, Any]:
        """Получить уведомления."""
        return await self._request(
            "GET",
            "/notifications",
            params={
                "fromRow": from_row,
                "rowLimit": row_limit,
                "order": order,
            },
        )
