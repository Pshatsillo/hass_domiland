"""Кнопки для интеграции Domyland."""

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import DomylandApiError
from .const import DOMAIN
from .coordinator import DomylandDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Настройка кнопок Domyland."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: DomylandDataUpdateCoordinator = data["coordinator"]

    entities: list[ButtonEntity] = []

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

        # Одна кнопка на группу, отправляет все тарифы
        entities.append(
            DomylandSubmitMeteringButton(
                coordinator,
                entry,
                group_id,
                group_title,
                device_id,
                tariff_count,
            )
        )

    async_add_entities(entities)


class DomylandSubmitMeteringButton(CoordinatorEntity, ButtonEntity):
    """Кнопка отправки показаний."""

    def __init__(
        self,
        coordinator: DomylandDataUpdateCoordinator,
        entry: ConfigEntry,
        group_id: int,
        group_title: str,
        device_id: int,
        tariff_count: int,
    ) -> None:
        """Инициализация кнопки."""
        super().__init__(coordinator)
        self._entry = entry
        self._group_id = group_id
        self._group_title = group_title
        self._device_id = device_id
        self._tariff_count = tariff_count

        self._attr_unique_id = f"{DOMAIN}_submit_{group_id}_{device_id}"
        self._attr_name = f"{group_title} — передать показания"
        self._attr_icon = "mdi:upload"

    @property
    def device_info(self):
        """Информация об устройстве."""
        return {
            "identifiers": {(DOMAIN, self._entry.data["place_id"])},
            "name": f"Домиленд — {self._entry.data.get('address', '')}",
            "manufacturer": self._entry.data.get("company_title"),
            "model": "Домиленд+",
        }

    def _number_entity(self, tariff_idx: int):
        """Находит Number-сущность для этого тарифа."""
        entity_id = (
            f"number.{DOMAIN}_number_{self._group_id}_{self._device_id}_t{tariff_idx}"
        )
        return self.hass.states.get(entity_id)

    async def async_press(self) -> None:
        """Отправляет показания (из Number или из формы)."""
        # Собираем значения по тарифам
        values: dict[int, str] = {}
        for tariff_idx in range(1, self._tariff_count + 1):
            # Ищем Number-сущность для этого тарифа
            entity_id = (
                f"number.{DOMAIN}_number_{self._group_id}_"
                f"{self._device_id}_t{tariff_idx}"
            )
            state = self.hass.states.get(entity_id)

            if state and state.state not in ("unknown", "unavailable"):
                values[tariff_idx] = state.state
            else:
                # Fallback — берём из формы
                form = self.coordinator.data.get("metering_forms", {}).get(
                    self._group_id, {}
                )
                items = form.get("items", [])
                if items:
                    values[tariff_idx] = str(items[0].get(f"value{tariff_idx}", "0"))

        if not values:
            _LOGGER.error("No values to submit for group %s", self._group_id)
            return

        # Отправляем
        try:
            await self.coordinator.api.submit_metering(
                metering_group_id=self._group_id,
                metering_device_id=self._device_id,
                values=values,
            )
            _LOGGER.info(
                "Submitted metering for group %s: %s",
                self._group_id,
                values,
            )
            await self.coordinator.async_request_refresh()
        except DomylandApiError as ex:
            _LOGGER.error(
                "Failed to submit metering for group %s: %s",
                self._group_id,
                ex,
            )
