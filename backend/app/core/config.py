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
    network_max_payload_bytes: int = Field(default=65536, gt=0, le=1048576)
    network_message_ttl_seconds: int = Field(default=604800, gt=0)
    network_retry_limit: int = Field(default=5, gt=0, le=20)
    network_retry_base_seconds: float = Field(default=5.0, gt=0)
    network_retry_max_seconds: float = Field(default=300.0, gt=0)
    network_worker_poll_seconds: float = Field(default=1.0, ge=0.25, le=60)
    network_latency_reference_ms: float = Field(default=500.0, gt=0)
    network_bandwidth_reference_mbps: float = Field(default=100.0, gt=0)
    network_healthy_threshold: float = Field(default=70.0, ge=0, le=100)
    network_health_latency_weight: float = Field(default=0.35, ge=0, le=1)
    network_health_delivery_weight: float = Field(default=0.40, ge=0, le=1)
    network_health_bandwidth_weight: float = Field(default=0.25, ge=0, le=1)
    network_measurement_enabled: bool = False
    network_measurement_target_url: str = Field(default="", max_length=2048)
    network_measurement_target_label: str = Field(default="", max_length=120)
    network_measurement_timeout_seconds: float = Field(default=3.0, gt=0, le=10)
    network_measurement_interval_seconds: float = Field(default=1.0, ge=0.5, le=10)
    network_measurement_max_samples: int = Field(default=20, ge=1, le=100)
    network_ack_test_url: str = Field(default="", max_length=2048)
    network_throughput_test_url: str = Field(default="", max_length=2048)
    network_throughput_max_bytes: int = Field(default=1048576, ge=1024, le=1048576)
    network_throughput_max_seconds: float = Field(default=5.0, gt=0, le=15)
    ripe_atlas_enabled: bool = False
    ripe_atlas_measurement_id: str = Field(default="", max_length=20)
    ripe_atlas_timeout_seconds: float = Field(default=10.0, gt=0, le=30)
    ripe_atlas_max_results: int = Field(default=500, ge=1, le=5000)
    ripe_atlas_lookback_hours: int = Field(default=24, ge=1, le=720)

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
        weights=(self.network_health_latency_weight,self.network_health_delivery_weight,self.network_health_bandwidth_weight)
        if abs(sum(weights)-1.0)>1e-6:
            raise ValueError("Network health score weights must sum to 1")
        if self.network_retry_max_seconds < self.network_retry_base_seconds:
            raise ValueError("NETWORK_RETRY_MAX_SECONDS must be at least NETWORK_RETRY_BASE_SECONDS")
        return self

settings = Settings()
