from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    postgres_host: str = "127.0.0.1"
    postgres_port: int = Field(default=55432, ge=1, le=65535)
    postgres_db: str = "performance_lab"
    postgres_user: str = "lab"
    postgres_password: SecretStr = SecretStr("lab-local-password")
    redis_host: str = "127.0.0.1"
    redis_port: int = Field(default=56379, ge=1, le=65535)

    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=5, ge=0)
    db_pool_timeout: float = Field(default=5, gt=0)
    db_statement_timeout_ms: int = Field(default=30_000, ge=1)
    healthcheck_timeout: float = Field(default=2, gt=0)
    lab_mode: bool = False

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )
