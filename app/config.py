from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    environment: str = "development"
    mongo_uri: str = "mongodb://mongo:27017"
    mongo_database: str = "tasko"
    redis_url: str = "redis://redis:6379/0"
    allowed_origins: list[str] = ["http://localhost:3000"]
    cookie_secure: bool = True
    session_hours: int = Field(default=24, ge=1, le=168)
    auth_rate_limit: int = Field(default=5, ge=1)
    api_rate_limit: int = Field(default=120, ge=1)
    cache_seconds: int = Field(default=30, ge=1, le=300)
    create_indexes_on_startup: bool = True

    @model_validator(mode="after")
    def production_safety(self):
        if "*" in self.allowed_origins:
            raise ValueError("Explicit frontend origins are required")
        if self.environment == "production" and not self.cookie_secure:
            raise ValueError("Production requires secure cookies")
        return self
