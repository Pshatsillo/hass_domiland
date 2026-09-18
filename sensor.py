"""Сенсоры для интеграции Domyland."""

from datetime import datetime, timezone
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfVolume, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DomylandDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

UNIT_MAP = {
    "м³": UnitOfVolume.CUBIC_METERS,
    "m³": UnitOfVolume.CUBIC_METERS,
    "куб.м": UnitOfVolume.CUBIC_METERS,
    "кВт·ч": UnitOfEnergy.KILO_WATT_HOUR,
    "кВт⋅ч": UnitOfEnergy.KILO_WATT_HOUR,
    "kWh": UnitOfEnergy.KILO_WATT_HOUR,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Настройка сенсоров Domyland."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: DomylandDataUpdateCoordinator = data["coordinator"]

    entities: list[SensorEntity] = []

    # Сенсоры счётчиков
    for group in coordinator.data.get("metering_groups", []):
        group_id = group["id"]
        group_title = group.get("title", f"Группа {group_id}")

        # Сколько тарифов у этой группы?
        form = coordinator.data.get("metering_forms", {}).get(group_id, {})
        items = form.get("items", [])
        if not items:
            continue

        item = items[0]

        # Определяем количество тарифов по valueTitle1/2/3
        # Если все пустые — считаем 1 тариф (value1)
        value_titles = [
            item.get("valueTitle1") or "",
            item.get("valueTitle2") or "",
            item.get("valueTitle3") or "",
        ]
        tariff_count = 1
        for i in (1, 2):
            title = value_titles[i]
            value = item.get(f"value{i + 1}") or "0.0000"
            if title or value not in ("0.0000", "", None):
                tariff_count = i + 1

        _LOGGER.debug(
            "Group %s (%s): %s tariffs, titles=%s",
            group_id,
            group_title,
            tariff_count,
            value_titles,
        )

        # Единица измерения
        raw_unit = item.get("measureUnitTitle") or "м³"
        unit = UNIT_MAP.get(raw_unit, raw_unit)

        # Для каждого тарифа — сенсоры
        for i in range(tariff_count):
            tariff_idx = i + 1  # 1, 2, 3
            tariff_title = value_titles[i] or (
                "" if tariff_count == 1 else f"Т{tariff_idx}"
            )

            # Текущее показание
            entities.append(
                DomylandMeteringValueSensor(
                    coordinator,
                    entry,
                    group_id,
                    group_title,
                    tariff_idx,
                    tariff_title,
                    unit,
                )
            )
            # Предыдущее показание
            entities.append(
                DomylandMeteringPreviousSensor(
                    coordinator,
                    entry,
                    group_id,
                    group_title,
                    tariff_idx,
                    tariff_title,
                    unit,
                )
            )
            # Расход
            entities.append(
                DomylandMeteringDeltaSensor(
                    coordinator,
                    entry,
                    group_id,
                    group_title,
                    tariff_idx,
                    tariff_title,
                    unit,
                )
            )

        # Один общий сенсор на группу: дата последней передачи
        entities.append(
            DomylandMeteringSubmittedSensor(
                coordinator,
                entry,
                group_id,
                group_title,
            )
        )
        # Один общий сенсор на группу: дата следующей поверки
        entities.append(
            DomylandMeteringCalibrationSensor(
                coordinator,
                entry,
                group_id,
                group_title,
            )
        )

    # Общие сенсоры
    entities.append(DomylandInvoicesSensor(coordinator, entry))
    entities.append(DomylandOrdersSensor(coordinator, entry))

    async_add_entities(entities)


class DomylandBaseSensor(CoordinatorEntity, SensorEntity):
    """Базовый класс для сенсоров Domyland."""

    def __init__(
        self,
        coordinator: DomylandDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Инициализация."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.data["place_id"])},
            "name": f"Домиленд — {entry.data.get('address', '')}",
            "manufacturer": entry.data.get("company_title"),
            "model": "Домиленд+",
        }

    def _group(self, group_id: int) -> dict[str, Any] | None:
        for group in self.coordinator.data.get("metering_groups", []):
            if group["id"] == group_id:
                return group
        return None

    def _form(self, group_id: int) -> dict[str, Any] | None:
        return self.coordinator.data.get("metering_forms", {}).get(group_id)

    def _item(self, group_id: int) -> dict[str, Any] | None:
        form = self._form(group_id)
        if not form:
            return None
        items = form.get("items", [])
        return items[0] if items else None


class DomylandMeteringBaseSensor(DomylandBaseSensor):
    """Базовый класс для сенсоров счётчика."""

    def __init__(
        self,
        coordinator: DomylandDataUpdateCoordinator,
        entry: ConfigEntry,
        group_id: int,
        group_title: str,
        tariff_idx: int,
        tariff_title: str,
        unit: str,
    ) -> None:
        """Инициализация."""
        super().__init__(coordinator, entry)
        self._group_id = group_id
        self._group_title = group_title
        self._tariff_idx = tariff_idx
        self._tariff_title = tariff_title
        self._attr_native_unit_of_measurement = unit
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING

    def _value_field(self, prefix: str) -> str:
        """Возвращает имя поля: value1/value2/value3, previousValue1/2/3, delta1/2/3."""
        return f"{prefix}{self._tariff_idx}"

    def _name_suffix(self) -> str:
        return f" {self._tariff_title}" if self._tariff_title else ""


class DomylandMeteringValueSensor(DomylandMeteringBaseSensor):
    """Текущее показание счётчика."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._attr_unique_id = (
            f"{DOMAIN}_metering_{self._group_id}_value{self._tariff_idx}"
        )
        self._attr_name = f"{self._group_title}{self._name_suffix()} — показание"
        self._attr_icon = "mdi:gauge"

    @property
    def native_value(self) -> float | None:
        item = self._item(self._group_id)
        if not item:
            return None
        try:
            return float(item.get(self._value_field("value"), 0))
        except ValueError, TypeError:
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        item = self._item(self._group_id)
        attrs = {"group_id": self._group_id, "tariff": self._tariff_idx}
        if item:
            attrs.update(
                {
                    "device_number": item.get("meteringDeviceNumber"),
                    "device_label": item.get("meteringDeviceLabel"),
                    "metering_device_id": item.get("meteringDeviceId"),
                    "measure_unit": item.get("measureUnitTitle"),
                }
            )
        return attrs


class DomylandMeteringPreviousSensor(DomylandMeteringBaseSensor):
    """Предыдущее показание счётчика."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._attr_unique_id = (
            f"{DOMAIN}_metering_{self._group_id}_previous{self._tariff_idx}"
        )
        self._attr_name = f"{self._group_title}{self._name_suffix()} — предыдущее"
        self._attr_icon = "mdi:history"
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self) -> float | None:
        item = self._item(self._group_id)
        if not item:
            return None
        try:
            return float(item.get(self._value_field("previousValue"), 0))
        except ValueError, TypeError:
            return None


class DomylandMeteringDeltaSensor(DomylandMeteringBaseSensor):
    """Расход счётчика."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._attr_unique_id = (
            f"{DOMAIN}_metering_{self._group_id}_delta{self._tariff_idx}"
        )
        self._attr_name = f"{self._group_title}{self._name_suffix()} — расход"
        self._attr_icon = "mdi:water-minus"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        item = self._item(self._group_id)
        if not item:
            return None
        try:
            return float(item.get(self._value_field("delta"), 0))
        except ValueError, TypeError:
            return None


class DomylandMeteringSubmittedSensor(DomylandBaseSensor):
    """Дата последней передачи показаний."""

    def __init__(
        self,
        coordinator: DomylandDataUpdateCoordinator,
        entry: ConfigEntry,
        group_id: int,
        group_title: str,
    ) -> None:
        """Инициализация."""
        super().__init__(coordinator, entry)
        self._group_id = group_id
        self._attr_unique_id = f"{DOMAIN}_metering_{group_id}_submitted"
        self._attr_name = f"{group_title} — дата передачи"
        self._attr_icon = "mdi:calendar-check"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @staticmethod
    def _format_timestamp(ts: int | None) -> datetime | None:
        if ts is None:
            return None
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except ValueError, OSError, TypeError:
            return None

    @property
    def native_value(self) -> datetime | None:
        item = self._item(self._group_id)
        if not item:
            return None
        # createdAt — дата создания текущей записи (последняя передача)
        # prevSubmittedAt — дата предыдущей передачи (кладём в атрибуты)
        return self._format_timestamp(item.get("createdAt"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        item = self._item(self._group_id)
        if not item:
            return {"group_id": self._group_id}
        return {
            "group_id": self._group_id,
            "prev_submitted_at": self._format_timestamp(item.get("prevSubmittedAt")),
            "created_at": self._format_timestamp(item.get("createdAt")),
            "submitted_at": self._format_timestamp(item.get("submittedAt")),
            "updated_at": self._format_timestamp(item.get("updatedAt")),
        }


class DomylandMeteringCalibrationSensor(DomylandBaseSensor):
    """Дата следующей поверки."""

    def __init__(
        self,
        coordinator: DomylandDataUpdateCoordinator,
        entry: ConfigEntry,
        group_id: int,
        group_title: str,
    ) -> None:
        """Инициализация."""
        super().__init__(coordinator, entry)
        self._group_id = group_id
        self._attr_unique_id = f"{DOMAIN}_metering_{group_id}_calibration"
        self._attr_name = f"{group_title} — поверка"
        self._attr_icon = "mdi:calendar-clock"

    @property
    def native_value(self) -> str | None:
        item = self._item(self._group_id)
        if not item:
            return None
        return item.get("meteringDeviceNextCalibrationDate")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        item = self._item(self._group_id)
        attrs = {"group_id": self._group_id}
        if item:
            attrs["is_expired_verification"] = item.get("isExpiredVerification")
            attrs["last_calibration_date"] = item.get("lastCalibrationDate")
        return attrs


class DomylandInvoicesSensor(DomylandBaseSensor):
    """Сенсор «Счета к оплате»."""

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{DOMAIN}_invoices"
        self._attr_name = "Счета к оплате"
        self._attr_icon = "mdi:receipt"
        self._attr_native_unit_of_measurement = "₽"

    @property
    def native_value(self) -> float | None:
        invoices = self.coordinator.data.get("invoices", {})
        total = invoices.get("totalSum") or invoices.get("total") or invoices.get("sum")
        try:
            return float(total) if total is not None else None
        except ValueError, TypeError:
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"raw": self.coordinator.data.get("invoices", {})}


class DomylandOrdersSensor(DomylandBaseSensor):
    """Сенсор «Заявки»."""

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{DOMAIN}_orders"
        self._attr_name = "Заявки"
        self._attr_icon = "mdi:clipboard-list"

    @property
    def native_value(self) -> int | None:
        orders = self.coordinator.data.get("orders", {})
        count = (
            orders.get("totalCount")
            or orders.get("total")
            or len(orders.get("items", []))
        )
        return count

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"raw": self.coordinator.data.get("orders", {})}
