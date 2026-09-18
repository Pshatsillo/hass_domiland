"""Координатор данных для интеграции Domyland."""

from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.unit_conversion import EnergyConverter, VolumeConverter

from .api import DomylandApiClient, DomylandApiError, DomylandAuthError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Маппинг единиц → unit_class (для статистики HA)
UNIT_TO_CLASS = {
    "м³": VolumeConverter.UNIT_CLASS,
    "m³": VolumeConverter.UNIT_CLASS,
    "m3": VolumeConverter.UNIT_CLASS,
    "куб.м": VolumeConverter.UNIT_CLASS,
    "кВт·ч": EnergyConverter.UNIT_CLASS,
    "kWh": EnergyConverter.UNIT_CLASS,
}

# Маппинг единиц → эталонные константы HA
UNIT_MAP = {
    "м³": UnitOfVolume.CUBIC_METERS,
    "m³": UnitOfVolume.CUBIC_METERS,
    "m3": UnitOfVolume.CUBIC_METERS,
    "куб.м": UnitOfVolume.CUBIC_METERS,
    "кВт·ч": UnitOfEnergy.KILO_WATT_HOUR,
    "kWh": UnitOfEnergy.KILO_WATT_HOUR,
}


class DomylandDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Координатор для опроса API Domyland."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: DomylandApiClient,
        entry: ConfigEntry,
        oauth_session: config_entry_oauth2_flow.OAuth2Session,
    ) -> None:
        """Инициализация координатора."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.api = api
        self.entry = entry
        self.oauth_session = oauth_session
        # Кэш импортированных statistic_id, чтобы не импортировать повторно
        self._imported_history: set[str] = set()

    async def _refresh_domyland_token(self) -> bool:
        """Переобменять Яндекс-токен на новый токен Домиленда.

        Возвращает True, если удалось.
        """
        _LOGGER.info("Refreshing Domyland token via Yandex OAuth")

        # HA сам обновит Яндекс-токен через refresh_token, если нужно
        await self.oauth_session.async_ensure_token_valid()
        yandex_token = self.oauth_session.token["access_token"]

        result = await self.api.exchange_yandex_token(
            yandex_token, self.entry.data["customer_ext_id"]
        )

        if result is None:
            _LOGGER.error("Failed to exchange Yandex token for Domyland token")
            return False

        # Обновляем токен в API-клиенте и в entry.data
        self.api.set_token(result["domyland_token"])
        new_data = {
            **self.entry.data,
            "domyland_token": result["domyland_token"],
        }
        self.hass.config_entries.async_update_entry(self.entry, data=new_data)
        _LOGGER.info("Domyland token refreshed successfully")
        return True

    async def _async_update_data(self) -> dict[str, Any]:
        """Запрашивает данные из API."""
        try:
            return await self._fetch_all_data()
        except DomylandAuthError:
            # Токен Домиленда истёк — пробуем переобменять
            _LOGGER.warning("Domyland token expired, attempting refresh")
            success = await self._refresh_domyland_token()
            if not success:
                # Переобмен не удался — HA запустит reauth
                raise ConfigEntryAuthFailed(
                    translation_domain=DOMAIN,
                    translation_key="reauth_required",
                )
            # Повторяем запрос с новым токеном
            try:
                return await self._fetch_all_data()
            except DomylandAuthError as ex:
                # Даже с новым токеном 401 — что-то серьёзное
                raise ConfigEntryAuthFailed(
                    translation_domain=DOMAIN,
                    translation_key="reauth_required",
                ) from ex
            except DomylandApiError as ex:
                raise UpdateFailed(f"API error after refresh: {ex}") from ex
        except DomylandApiError as ex:
            raise UpdateFailed(f"API error: {ex}") from ex

    async def _fetch_all_data(self) -> dict[str, Any]:
        """Собирает все данные из API."""
        customer = await self.api.get_current_customer()
        dashboard_items = await self.api.get_metering_dashboard()
        invoices = await self.api.get_invoices()
        orders = await self.api.get_orders()

        # Собираем группы счётчиков из dashboard
        metering_groups: list[dict[str, Any]] = []
        for section in dashboard_items:
            for group in section.get("meteringGroups", []):
                metering_groups.append(group)

        # Формы и истории для каждой группы
        metering_forms: dict[int, dict[str, Any]] = {}
        metering_histories: dict[int, list[dict[str, Any]]] = {}

        for group in metering_groups:
            group_id = group["id"]

            # Форма с деталями счётчика
            try:
                form = await self.api.get_metering_form(group_id)
                metering_forms[group_id] = form.get("data", {})
            except DomylandApiError as ex:
                _LOGGER.warning("Failed to fetch form for group %s: %s", group_id, ex)

            # История показаний
            try:
                history = await self.api.get_metering_history(
                    metering_group_id=group_id,
                    from_row=0,
                    period="all",
                )
                items = history.get("data", {}).get("items", [])
                metering_histories[group_id] = items
            except DomylandApiError as ex:
                _LOGGER.warning(
                    "Failed to fetch history for group %s: %s", group_id, ex
                )
                metering_histories[group_id] = []

        # Импорт статистики в HA
        await self._import_statistics(metering_forms, metering_histories)

        return {
            "customer": customer.get("data", {}),
            "metering_groups": metering_groups,
            "metering_forms": metering_forms,
            "metering_histories": metering_histories,
            "invoices": invoices.get("data", {}),
            "orders": orders.get("data", {}),
        }

    async def _import_statistics(
        self,
        metering_forms: dict[int, dict[str, Any]],
        metering_histories: dict[int, list[dict[str, Any]]],
    ) -> None:
        """Импортирует историю показаний в статистику HA."""
        for group_id, form in metering_forms.items():
            items = form.get("items", [])
            if not items:
                continue

            item = items[0]
            device_id = item.get("meteringDeviceId")
            if device_id is None:
                continue

            # Определяем количество тарифов
            value_titles = [
                item.get("valueTitle1") or "",
                item.get("valueTitle2") or "",
                item.get("valueTitle3") or "",
            ]
            tariff_count = 1
            for i in (1, 2):
                value = item.get(f"value{i + 1}") or "0.0000"
                if value_titles[i] or value not in ("0.0000", "", None):
                    tariff_count = i + 1

            history = metering_histories.get(group_id, [])
            if not history:
                continue

            # Единица измерения: сначала маппинг в эталон HA, потом в unit_class
            raw_unit = item.get("measureUnitTitle") or "м³"
            unit = UNIT_MAP.get(raw_unit, raw_unit)
            unit_class = UNIT_TO_CLASS.get(raw_unit)

            for tariff_idx in range(1, tariff_count + 1):
                tariff_suffix = f"_t{tariff_idx}" if tariff_count > 1 else ""
                statistic_id = f"{DOMAIN}:{group_id}_{device_id}{tariff_suffix}"

                # Пропускаем, если уже импортировали
                if statistic_id in self._imported_history:
                    continue

                stats: list[StatisticData] = []
                cumulative_sum = 0.0
                last_value: float | None = None

                # Идём от старых к новым
                for month_group in reversed(history):
                    for rec in month_group.get("items", []):
                        if rec.get("deviceId") != device_id:
                            continue

                        # Значение по тарифу
                        values = rec.get("values", [])
                        if tariff_idx > len(values):
                            continue
                        value = values[tariff_idx - 1].get("value")
                        if value is None:
                            continue

                        # Парсим дату и округляем до начала часа
                        created_at = rec.get("createdAt")
                        if not created_at:
                            continue
                        try:
                            dt = datetime.strptime(
                                created_at, "%Y-%m-%d %H:%M:%S"
                            ).replace(
                                tzinfo=timezone.utc,
                                minute=0,
                                second=0,
                                microsecond=0,
                            )
                        except ValueError, TypeError:
                            continue

                        # Накопительная сумма (прирост)
                        if last_value is not None:
                            delta = value - last_value
                            if delta >= 0:
                                cumulative_sum += delta
                        else:
                            cumulative_sum = 0.0
                        last_value = value

                        stats.append(
                            StatisticData(
                                start=dt,
                                state=float(value),
                                sum=cumulative_sum,
                            )
                        )

                if not stats:
                    continue

                meta = StatisticMetaData(
                    has_mean=False,
                    has_sum=True,
                    mean_type=StatisticMeanType.NONE,
                    name=(
                        f"Домиленд {group_id} устройство {device_id} тариф {tariff_idx}"
                    ),
                    source=DOMAIN,
                    statistic_id=statistic_id,
                    unit_of_measurement=unit,
                    unit_class=unit_class,
                )

                try:
                    async_add_external_statistics(self.hass, meta, stats)
                    self._imported_history.add(statistic_id)
                    _LOGGER.info(
                        "Imported %s statistics (%s points)",
                        statistic_id,
                        len(stats),
                    )
                except Exception as ex:  # noqa: BLE001
                    _LOGGER.error(
                        "Failed to import statistics for %s: %s",
                        statistic_id,
                        ex,
                    )
