import logging

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("beidou.config")

# 众所周知的弱默认值（P0-2 安全加固）：仅作本地开发兜底，生产必须覆盖。
_WEAK_SECRET_KEY = "beidou-dev-secret-change-me"
_WEAK_ADMIN_PASSWORD = "admin123"

# 【致命】已随仓库公开的密钥值：源码/diff/文档里人人可见，等同未设密钥。
# 命中即拒绝启动（除非显式设 ALLOW_WEAK_CREDS=1 —— 仅供历史部署临时保命）。
_PUBLIC_SECRET_VALUES: set[str] = {
    "",                                          # 留空 = 未设置
    _WEAK_SECRET_KEY,                            # 内置开发默认
    "please-change-me-to-a-long-random-string",  # docker-compose 旧兜底值
    "changeme",
    "secret",
    "password",
}

# 【致命】常见弱口令（含 compose 旧兜底 admin123）
_PUBLIC_PASSWORD_VALUES: set[str] = {
    "",
    _WEAK_ADMIN_PASSWORD,
    "password",
    "password123",
    "12345678",
    "123456789",
    "qwerty",
    "changeme",
    "admin12345",
}

_MIN_SECRET_LEN = 32  # 按字符数计；`openssl rand -hex 32` 得 64 字符
_MIN_SECRET_DIVERSITY = 8  # 字符种类下限（熵的粗略代理，防 "aaaa…" 这类可猜串）
_MIN_PASSWORD_LEN = 12  # 按字符数计（推荐 16+）


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

    # SSO 协议 v2（EdDSA/Ed25519）：天宫 JWKS 公钥端点（环境变量 TIANGONG_JWKS_URL）。
    # 配置后 /sso/launch 优先走 v2 验签（拉 JWKS 按 kid 匹配公钥验 EdDSA 票）；
    # 未配置时回退 v1（TIANGONG_SSO_SECRET 共享密钥 HS256）。
    tiangong_jwks_url: str = ""

    # 部署环境："production"/"prod" 时强制强凭据、拒绝弱默认（对齐天宫 local-auth-router 的加固）。
    beidou_env: str = "development"

    # 弱凭据放行开关（默认关闭）：接受 1/true/yes/on（大小写不敏感），其它值
    # （含空串）一律视为"未启用"。故意用 str 而非 bool 接收——否则写到 compose 里
    # 的 `ALLOW_WEAK_CREDS: ${ALLOW_WEAK_CREDS:-}`（透传可选变量的标准写法）会传进
    # 空串，让 pydantic 在**模块导入期**抛 ValidationError，报错与凭据毫无关系、极难定位。
    allow_weak_creds: str = ""

    @property
    def weak_creds_allowed(self) -> bool:
        return self.allow_weak_creds.strip().lower() in {"1", "true", "yes", "on"}

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


def _evaluate_credentials(s: Settings) -> tuple[list[str], list[str], bool]:
    """评估凭据强度，返回 (fatal, weak, empty)。

    - **fatal**：空值 / 已随仓库公开的内置示例值 / 常见弱口令——等同未设密钥或人尽可知，
      属"必须修"；
    - **weak**：自定但偏短——属"可以更好"，不应因此阻断启动；
    - **empty**：SECRET_KEY 或 ADMIN_PASSWORD 剥掉首尾空白后为空——**任何环境都不允许**：
      空口令 admin 一旦经首次启动落库（db.py 引导逻辑），本地实例即形同裸奔，dev 也不例外。

    比较一律用 strip 后的值：纯空白（如 `"   "`）按空值对待，不得因含空格而绕过致命集。
    长度按**字符数**计（非字节）：`openssl rand -hex 32` 得 64 字符，base64 32 字节得 44 字符，
    两者都满足 SECRET_KEY 下限。
    """
    fatal: list[str] = []
    weak: list[str] = []
    sk = s.secret_key.strip()
    pw = s.admin_password.strip()

    if sk in _PUBLIC_SECRET_VALUES:
        fatal.append(
            "SECRET_KEY 为空或命中已公开的内置/示例值（请用 `openssl rand -hex 32` 生成）"
        )
    elif len(sk) < _MIN_SECRET_LEN:
        weak.append(
            f"SECRET_KEY 仅 {len(sk)} 个字符，建议 ≥{_MIN_SECRET_LEN}（`openssl rand -hex 32` 得 64）"
        )
    elif len(set(sk)) < _MIN_SECRET_DIVERSITY:
        # 熵的粗略代理：`"a"*64` 之类虽够长却毫无随机性，暴力可枚举。
        # 归入"偏弱"（告警不阻断）——它是自定值而非仓库公开值，按混合尺度不拒绝启动。
        weak.append(
            f"SECRET_KEY 字符多样性过低（仅 {len(set(sk))} 种字符），疑似重复/可猜串，请用真随机值"
        )

    if pw in _PUBLIC_PASSWORD_VALUES:
        fatal.append(
            "ADMIN_PASSWORD 为空或命中弱口令黑名单（admin123 / password / 12345678 等）"
        )
    elif len(pw) < _MIN_PASSWORD_LEN:
        weak.append(
            f"ADMIN_PASSWORD 仅 {len(pw)} 个字符，建议 ≥{_MIN_PASSWORD_LEN}（推荐 16+）"
        )

    return fatal, weak, (not sk or not pw)


def _enforce_strong_credentials(s: Settings) -> None:
    """凭据门禁——**混合尺度**（2026-10-09 用户拍板）：

    - **致命项**（空值 / 已公开的示例值 / 弱口令）：
      · 生产环境**默认拒绝启动**（这是本门禁存在的意义：堵住"出厂公开密钥"）；
      · 显式设 `ALLOW_WEAK_CREDS=1` 可放行，但持续打 ERROR 日志——给历史部署留保命通道；
      · 开发/测试环境仅告警，保证本地与 CI 可直接运行。
    - **空凭据**（含纯空白）：**任何环境都拒绝启动**——空口令 admin 一旦经首次启动落库，
      本地实例即裸奔，这与"混合尺度容忍自定短值"是两回事（空 = 没设，不是"自定但弱"）。
    - **偏弱项**（自定但偏短）：任何环境都**只告警、不拒绝**——避免"自定短密钥"的历史部署
      在升级后意外起不来（这是混合尺度与"一律 fail-fast"的区别）。
    """
    fatal, weak, empty = _evaluate_credentials(s)

    # 生产环境按 ERROR 记录（供监控捞取）；非生产降为 warning——本地/CI 用内置默认值属预期行为
    log = logger.error if s.is_production else logger.warning
    for msg in fatal:
        log("[security] 凭据致命问题：%s", msg)
    for msg in weak:
        log("[security] 凭据偏弱（不阻断启动，建议尽快加强）：%s", msg)

    if not fatal:
        return

    if (empty or s.is_production) and not s.weak_creds_allowed:
        raise RuntimeError(
            "北斗检测到未设置/已公开的凭据，拒绝启动："
            + "；".join(fatal)
            + "。请设置强随机 SECRET_KEY 与强 ADMIN_PASSWORD 后重启"
            + ("" if s.is_production else "（开发环境同样不接受空凭据——空口令 admin 会直接落库）。")
            + "若确属历史遗留、确需临时保命，可显式设 ALLOW_WEAK_CREDS=1（会持续打 ERROR 日志）。"
        )

    if s.weak_creds_allowed:
        logger.error(
            "[security] ⚠ ALLOW_WEAK_CREDS 已启用：上述致命凭据问题被放行——请尽快更换为强随机值并撤掉该变量"
        )


settings = Settings()
_enforce_strong_credentials(settings)
