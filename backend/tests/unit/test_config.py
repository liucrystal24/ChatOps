import pytest

from app.core.config import ConfigurationError, Settings, load_settings

pytestmark = pytest.mark.unit
SECRET = "private-password-never-echo"


def config(**overrides):
    return Settings(
        **{
            "database_url": f"mysql+asyncmy://app:{SECRET}@localhost/test?charset=utf8mb4",
            "qdrant_url": "http://localhost:6333",
            "qdrant_api_key": "k" * 32,
            "auth_secret": "a" * 64,
            "auth_token_ttl_seconds": 1800,
            **overrides,
        }
    )


def test_valid_config_and_secrets_hidden():
    settings = config()
    assert settings.connect_timeout == 3
    assert SECRET not in repr(settings)
    assert "k" * 32 not in repr(settings)


@pytest.mark.parametrize(
    "overrides",
    [
        {"database_url": "sqlite:///test.db"},
        {"database_url": f"mysql+asyncmy://root:{SECRET}@localhost/test?charset=utf8mb4"},
        {"database_url": f"mysql+asyncmy://app:{SECRET}@localhost/test"},
        {"qdrant_url": f"http://app:{SECRET}@localhost:6333"},
        {"qdrant_url": "http://localhost:6333/?api-key=private"},
        {"qdrant_api_key": "short"},
        {"health_timeout_seconds": 0},
        {"auth_secret": "short"},
        {"auth_token_ttl_seconds": 86400},
    ],
)
def test_invalid_config_is_rejected(overrides):
    with pytest.raises(ValueError) as exc:
        config(**overrides)
    assert SECRET not in str(exc.value)


def test_startup_missing_config_is_clear_and_redacted(monkeypatch):
    for key in ["DATABASE_URL", "QDRANT_URL", "QDRANT_API_KEY"]:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ConfigurationError, match="DATABASE_URL") as exc:
        load_settings()
    assert "QDRANT_API_KEY" in str(exc.value)
    monkeypatch.setenv("DATABASE_URL", f"invalid://{SECRET}")
    with pytest.raises(ConfigurationError) as exc:
        load_settings()
    assert SECRET not in str(exc.value)
