from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    secret_key: str = "beidou-dev-secret-change-me"
    access_token_expire_hours: int = 24 * 7

    data_dir: str = "./data"
    database_url: str = ""  # 默认由 data_dir 推导

    admin_username: str = "admin"
    admin_password: str = "admin123"

    static_dir: str = ""  # 前端构建产物目录，空则仅提供 API

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        import os
        os.makedirs(self.data_dir, exist_ok=True)
        return f"sqlite+aiosqlite:///{self.data_dir.rstrip('/')}/beidou.db"


settings = Settings()
