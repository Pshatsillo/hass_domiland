"""Application credentials platform for Domiland."""

from homeassistant.components.application_credentials import (
    AuthorizationServer,
    ClientCredential,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_entry_oauth2_flow import (
    AbstractOAuth2Implementation,
    LocalOAuth2Implementation,
)


async def async_get_authorization_server(hass: HomeAssistant) -> AuthorizationServer:
    """Возвращает OAuth2-сервер Яндекса."""
    return AuthorizationServer(
        authorize_url="https://oauth.yandex.ru/authorize",
        token_url="https://oauth.yandex.ru/token",
    )


async def async_get_description_placeholders(
    hass: HomeAssistant,
) -> dict[str, str]:
    """Плейсхолдеры для формы ввода client_id/client_secret."""
    return {
        "console_url": "https://oauth.yandex.ru/client/new",
    }


async def async_get_auth_implementation(
    hass: HomeAssistant,
    auth_domain: str,
    credential: ClientCredential,
) -> AbstractOAuth2Implementation:
    """Возвращает реализацию OAuth2 для Яндекса."""
    return LocalOAuth2Implementation(
        hass,
        auth_domain,
        credential.client_id,
        credential.client_secret,
        authorize_url="https://oauth.yandex.ru/authorize",
        token_url="https://oauth.yandex.ru/token",
    )
