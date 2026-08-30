import logging

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("beidou.config")

# 众所周知的弱默认值（P0-2 安全加固）：仅作本地开发兜底，生产必须覆盖。
_WEAK_SECRET_KEY = "beidou-dev-secret-change-me"
_WEAK_ADMIN_PASSWORD = "admin123"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    secret_key: str = _WEAK_SECRET_KEY
    access_token_expire_hours: int = 24 * 7

    data_dir: str = "./data"
    database_url: str = ""  # 默认由 data_dir 推导

    admin_username: str = "admin"
    admin_password: str = _WEAK_ADMIN_PASSWORD

    static_dir: str = ""  # 前端构建产物目录，空则仅提供 API

    # SSO 联邦登录（P1-3）：天宫签发 SSO JWT 的签名密钥（环境变量 TIANGONG_SSO_SECRET）。
    # 未配置时 /sso/launch 返回 501「SSO 未配置」。
    tiangong_sso_secret: str = ""

    # 部署环境："production"/"prod" 时强制强凭据、拒绝弱默认（对齐天宫 local-auth-router 的加固）。
    beidou_env: str = "development"

    @property
    def is_production(self) -> bool:
        return self.beidou_env.strip().lower() in {"production", "prod"}

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        import os
        os.makedirs(self.data_dir, exist_ok=True)
        return f"sqlite+aiosqlite:///{self.data_dir.rstrip('/')}/beidou.db"


def _enforce_strong_credentials(s: Settings) -> None:
    """生产环境拒绝弱默认凭据；开发/测试环境仅告警，保证本地与测试可直接运行。"""
    problems = []
    if s.secret_key == _WEAK_SECRET_KEY:
        problems.append("SECRET_KEY 仍是开发默认值，请设置为强随机串（如 `openssl rand -hex 32`）")
    if s.admin_password == _WEAK_ADMIN_PASSWORD:
        problems.append("ADMIN_PASSWORD 仍是弱默认值 admin123，请设置 ≥8 位强密码")
    if len(s.admin_password) < 8:
        problems.append("ADMIN_PASSWORD 长度至少 8 位")
    if not problems:
        return
    if s.is_production:
        raise RuntimeError(
            "北斗生产环境检测到弱凭据，拒绝启动：" + "；".join(problems)
            + "。请在 .env / 环境变量中覆盖后重启。"
        )
    for msg in problems:
        logger.warning("[security] 非生产环境使用弱凭据：%s", msg)


settings = Settings()
_enforce_strong_credentials(settings)
