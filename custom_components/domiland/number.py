"""Number-сущности для интеграции Domyland."""

import asyncio
import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import DomylandApiError
from .const import DOMAIN
from .coordinator import DomylandDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Задержка перед отправкой (debounce), в секундах
SUBMIT_DEBOUNCE_SECONDS = 10


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Настройка Number-сущностей Domyland."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: DomylandDataUpdateCoordinator = data["coordinator"]

    entities: list[NumberEntity] = []

    for group in coordinator.data.get("metering_groups", []):
        group_id = group["id"]
        group_title = group.get("title", f"Группа {group_id}")

        form = coordinator.data.get("metering_forms", {}).get(group_id, {})
        items = form.get("items", [])
        if not items:
            continue

        item = items[0]
        device_id = item.get("meteringDeviceId")
        if device_id is None:
            continue

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

        for i in range(tariff_count):
            tariff_idx = i + 1
            tariff_title = value_titles[i] or (
                "" if tariff_count == 1 else f"Т{tariff_idx}"
            )

            entities.append(
                DomylandMeteringNumber(
                    coordinator,
                    entry,
                    group_id,
                    group_title,
                    device_id,
                    tariff_idx,
                    tariff_title,
                )
            )

    async_add_entities(entities)


class DomylandMeteringNumber(CoordinatorEntity, NumberEntity):
    """Number с автоотправкой, debounce и проверкой минимума."""

    _attr_mode = NumberMode.BOX
    _attr_native_step = 1
    _attr_icon = "mdi:counter"

    def __init__(
        self,
        coordinator: DomylandDataUpdateCoordinator,
        entry: ConfigEntry,
        group_id: int,
        group_title: str,
        device_id: int,
        tariff_idx: int,
        tariff_title: str,
    ) -> None:
        """Инициализация Number-сущности."""
        super().__init__(coordinator)
        self._entry = entry
        self._group_id = group_id
        self._group_title = group_title
        self._device_id = device_id
        self._tariff_idx = tariff_idx
        self._tariff_title = tariff_title

        # Черновик и таймер debounce
        self._pending_value: float | None = None
        self._debounce_task: asyncio.Task | None = None

        suffix = f" {tariff_title}" if tariff_title else ""
        self._attr_unique_id = f"{DOMAIN}_number_{group_id}_{device_id}_t{tariff_idx}"
        self._attr_name = f"{group_title}{suffix} — ввод показания"

        form = coordinator.data.get("metering_forms", {}).get(group_id, {})
        items = form.get("items", [])
        if items:
            self._attr_native_unit_of_measurement = (
                items[0].get("measureUnitTitle") or "м³"
            )

    @property
    def device_info(self):
        """Информация об устройстве."""
        return {
            "identifiers": {(DOMAIN, self._entry.data["place_id"])},
            "name": f"Домиленд — {self._entry.data.get('address', '')}",
            "manufacturer": self._entry.data.get("company_title"),
            "model": "Домиленд+",
        }

    def _item(self) -> dict | None:
        """Текущий item формы."""
        form = self.coordinator.data.get("metering_forms", {}).get(self._group_id, {})
        items = form.get("items", [])
        return items[0] if items else None

    def _current_value(self) -> float | None:
        """Текущее показание из формы (value1) — минимум."""
        item = self._item()
        if not item:
            return None
        value = item.get(f"value{self._tariff_idx}")
        try:
            return float(value)
        except ValueError, TypeError:
            return None

    def _max_value(self) -> float:
        """Максимум: current + delta * 10, либо current + 1000."""
        current = self._current_value()
        if current is None:
            return 9999999

        item = self._item()
        if item:
            delta_raw = item.get(f"delta{self._tariff_idx}")
            try:
                delta = float(delta_raw)
                if delta > 0:
                    return current + delta * 10
            except ValueError, TypeError:
                pass

        return current + 1000

    @property
    def native_min_value(self) -> float:
        """Минимум = текущее показание."""
        current = self._current_value()
        return current if current is not None else 0

    @property
    def native_max_value(self) -> float:
        """Максимум с защитой от лишнего нуля."""
        return self._max_value()

    @property
    def native_value(self) -> float | None:
        """Значение поля: черновик или текущее показание."""
        if self._pending_value is not None:
            return self._pending_value
        return self._current_value()

    async def async_set_native_value(self, value: float) -> None:
        """Сохраняет значение и запускает debounce-таймер."""
        current = self._current_value()
        if current is not None and value < current:
            _LOGGER.warning(
                "Rejected value %s for group %s (below current %s)",
                value,
                self._group_id,
                current,
            )
            self.async_write_ha_state()
            return

        max_val = self._max_value()
        if value > max_val:
            _LOGGER.warning(
                "Rejected value %s for group %s (above max %s)",
                value,
                self._group_id,
                max_val,
            )
            self.async_write_ha_state()
            return

        self._pending_value = value
        _LOGGER.debug(
            "Pending value for group %s tariff %s: %s",
            self._group_id,
            self._tariff_idx,
            value,
        )
        self.async_write_ha_state()

        if self._debounce_task is not None:
            self._debounce_task.cancel()

        self._debounce_task = self.hass.async_create_task(self._debounced_submit())

    async def _debounced_submit(self) -> None:
        """Ждёт SUBMIT_DEBOUNCE_SECONDS и отправляет значение."""
        try:
            await asyncio.sleep(SUBMIT_DEBOUNCE_SECONDS)
        except asyncio.CancelledError:
            # Таймер отменён — новое значение пришло раньше
            return

        if self._pending_value is None:
            return

        value = self._pending_value
        _LOGGER.info(
            "Debounce elapsed, submitting group %s tariff %s: %s",
            self._group_id,
            self._tariff_idx,
            value,
        )

        try:
            await self.coordinator.api.submit_metering(
                metering_group_id=self._group_id,
                metering_device_id=self._device_id,
                values={self._tariff_idx: str(value)},
            )
            _LOGGER.info(
                "Submitted metering for group %s tariff %s: %s",
                self._group_id,
                self._tariff_idx,
                value,
            )
            # Сбрасываем черновик — данные придут из API
            self._pending_value = None
            await self.coordinator.async_request_refresh()
        except DomylandApiError as ex:
            _LOGGER.error(
                "Failed to submit metering for group %s: %s",
                self._group_id,
                ex,
            )

    async def async_will_remove_from_hass(self) -> None:
        """Отменяет таймер при удалении сущности."""
        if self._debounce_task is not None:
            self._debounce_task.cancel()
        await super().async_will_remove_from_hass()
