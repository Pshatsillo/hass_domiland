"""Constants for the domiland integration."""

DOMAIN = "domiland"

# TODO Update with your own urls
OAUTH2_AUTHORIZE = "https://www.example.com/auth/authorize"
OAUTH2_TOKEN = "https://www.example.com/auth/token"


# Базовый URL API
API_BASE_URL = "https://customer-api.domyland.ru"

# Интервал опроса (в секундах)
DEFAULT_SCAN_INTERVAL = 3600

AUTH_HEADERS = {
    "AppName": "superdom-android",
    "OriginalAppName": "superdom-android",
    "AppVersion": "4.23.1",
    "AppTheme": "dark",
    "TimeZone": "Europe/Moscow",
}
