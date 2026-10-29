from pathlib import Path
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[3] / ".env", env_file_encoding="utf-8", extra="ignore")
    app_name: str = "KAVACHNET"
    environment: str = "development"
    secret_key: str = "CHANGE-ME-using-a-random-secret-before-deployment"
    database_url: str = "sqlite:///./kavachnet.db"
    access_token_minutes: int = 30
    cookie_secure: bool = False
    registration_enabled: bool = False
    allowed_origins: str = "http://localhost:5173"
    incident_correlation_window_seconds: int = Field(default=900, ge=60, le=86400)

    @property
    def resolved_database_url(self) -> str:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix) or self.database_url == "sqlite:///:memory:":
            return self.database_url
        raw_path = self.database_url[len(prefix):]
        path = Path(raw_path)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[2] / path
        return f"{prefix}{path.resolve().as_posix()}"

    @model_validator(mode="after")
    def require_production_secret(self):
        if self.environment.lower() in {"production", "prod"} and (not self.secret_key or self.secret_key.startswith("CHANGE-ME")):
            raise ValueError("SECRET_KEY must be set to a strong secret in production")
        return self

settings = Settings()
