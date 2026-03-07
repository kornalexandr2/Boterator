import json
import time
from dataclasses import dataclass
from hashlib import sha256
from hmac import compare_digest, new as hmac_new
from urllib.parse import parse_qsl


class TwaAuthError(ValueError):
    """Raised when Telegram WebApp auth data is invalid."""


@dataclass(slots=True)
class TwaUserContext:
    telegram_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    language_code: str | None
    auth_date: int
    init_data: str
    raw: dict[str, str]


def validate_init_data(init_data: str, bot_token: str, max_age_seconds: int = 86400) -> TwaUserContext:
    if not bot_token:
        raise TwaAuthError("BOT token is not configured")
    if not init_data:
        raise TwaAuthError("Telegram init data is missing")

    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", "")
    if not received_hash:
        raise TwaAuthError("Telegram init data hash is missing")

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(parsed.items()))
    secret_key = hmac_new(b"WebAppData", bot_token.encode("utf-8"), sha256).digest()
    calculated_hash = hmac_new(secret_key, data_check_string.encode("utf-8"), sha256).hexdigest()
    if not compare_digest(calculated_hash, received_hash):
        raise TwaAuthError("Telegram init data signature is invalid")

    auth_date = int(parsed.get("auth_date", "0") or 0)
    now = int(time.time())
    if auth_date and now - auth_date > max_age_seconds:
        raise TwaAuthError("Telegram init data is expired")

    try:
        user = json.loads(parsed.get("user", "{}"))
    except json.JSONDecodeError as exc:
        raise TwaAuthError("Telegram init data user payload is invalid") from exc

    telegram_id = int(user.get("id") or 0)
    if not telegram_id:
        raise TwaAuthError("Telegram user id is missing")

    return TwaUserContext(
        telegram_id=telegram_id,
        username=user.get("username"),
        first_name=user.get("first_name"),
        last_name=user.get("last_name"),
        language_code=user.get("language_code"),
        auth_date=auth_date,
        init_data=init_data,
        raw=parsed,
    )


