from math import ceil

from pydantic import Field, HttpUrl, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    # 配置由进程环境注入，不隐式读取工作目录里的私有 .env。
    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)

    database_url: SecretStr
    qdrant_url: HttpUrl
    qdrant_api_key: SecretStr = Field(min_length=32)
    auth_secret: SecretStr = Field(min_length=32)
    auth_token_ttl_seconds: int = Field(default=1800, ge=300, le=7200)
    health_timeout_seconds: float = Field(default=3, ge=0.1, le=10)

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
            valid = (
                url.drivername == "mysql+asyncmy"
                and url.host
                and url.database
                and url.username
                and url.username != "root"
                and url.password
                and url.query.get("charset") == "utf8mb4"
            )
            if not valid:
                raise ValueError
        except Exception:
            raise ValueError(
                "需要 mysql+asyncmy、独立账号、密码、数据库及 charset=utf8mb4"
            ) from None
        return value

    @field_validator("qdrant_url")
    @classmethod
    def validate_qdrant_url(cls, value: HttpUrl) -> HttpUrl:
        if value.username or value.password or value.query or value.fragment or value.path != "/":
            raise ValueError("QDRANT_URL 只接受无凭据、无路径和查询参数的服务地址")
        return value

    @property
    def connect_timeout(self) -> int:
        return ceil(self.health_timeout_seconds)


class ConfigurationError(RuntimeError):
    pass


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as error:
        # Pydantic 的输入与上下文可能带连接串；启动错误仅列字段及类型。
        fields = ", ".join(f"{item['loc'][0].upper()} ({item['type']})" for item in error.errors())
        raise ConfigurationError(f"配置无效：{fields}；请按 .env.example 设置环境变量") from None
