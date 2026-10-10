"""P0-3 凭据门禁回归测试（**混合尺度**，2026-10-09 用户拍板）。

背景：北斗原 `docker-compose.yml` 的兜底值 `please-change-me-to-a-long-random-string`
是**公开值**——任何人拿到源码即可据此伪造管理员 JWT；而 `BEIDOU_ENV` 从未设为
production，使既有的弱凭据门禁只告警、从不拦截。

P0-3 修复后按「混合尺度」把关：
  · **致命项**（空值 / 已公开的内置或示例值 / 常见弱口令）：
    生产环境**拒绝启动**；显式设 `ALLOW_WEAK_CREDS=1` 可放行（持续打 ERROR 日志）；
    开发/测试环境仅告警。
  · **偏弱项**（自定但偏短 / 字符多样性过低）：任何环境都**只告警、不阻断**——
    避免"自定短密钥"的历史部署在升级后意外起不来。

本文件另钉住一条曾造成**全量部署阻断**的回归：
`docker-compose.yml` 必须始终是**合法 YAML**——它曾因 `${VAR:?消息}` 内含 ": "
（冒号+空格）被 YAML 当作映射指示符，导致所有 compose 部署在解析阶段就失败。
"""

import logging
from pathlib import Path

import pytest
import yaml

from app.config import Settings, _evaluate_credentials, _enforce_strong_credentials

# 仓库根：tests/ 的上一级是 backend/，再上一级才是仓库根
_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"

_STRONG_SECRET = "9f2c7a41b8e35d60af14c92e7b3d85a60cf1e28d47b93a5c0e6d81f4b27a3c95"  # 64 hex
_STRONG_PASSWORD = "BeiDou-Str0ng!2026"


def _settings(**overrides) -> Settings:
    """构造隔离的 Settings（显式入参优先于环境变量），不触碰全局 settings 单例。"""
    base = {
        "secret_key": _STRONG_SECRET,
        "admin_password": _STRONG_PASSWORD,
        "beidou_env": "production",
        "allow_weak_creds": "",
    }
    base.update(overrides)
    return Settings(**base)


# ---------- 一、致命项：生产环境拒绝启动 ----------


@pytest.mark.parametrize(
    "secret,password,reason",
    [
        ("please-change-me-to-a-long-random-string", _STRONG_PASSWORD, "compose 旧公开兜底值"),
        ("beidou-dev-secret-change-me", _STRONG_PASSWORD, "内置开发默认值"),
        ("", _STRONG_PASSWORD, "SECRET_KEY 留空 = 未设置"),
        (_STRONG_SECRET, "admin123", "弱口令 admin123"),
        (_STRONG_SECRET, "password123", "弱口令 password123"),
        (_STRONG_SECRET, "", "ADMIN_PASSWORD 留空"),
    ],
)
def test_fatal_credentials_refuse_start_in_production(secret, password, reason):
    """公开/空/弱口令凭据在生产环境必须拒绝启动（这是本门禁存在的意义）。"""
    s = _settings(secret_key=secret, admin_password=password)
    with pytest.raises(RuntimeError) as ei:
        _enforce_strong_credentials(s)
    assert "拒绝启动" in str(ei.value), f"应为拒绝启动，实际：{ei.value}"


def test_explicit_escape_hatch_allows_start_but_logs_error(caplog):
    """显式 ALLOW_WEAK_CREDS=1 时放行历史部署，但必须持续打 ERROR 日志（可被监控捞到）。"""
    s = _settings(
        secret_key="please-change-me-to-a-long-random-string",
        admin_password="admin123",
        allow_weak_creds="1",
    )
    with caplog.at_level(logging.ERROR, logger="beidou.config"):
        _enforce_strong_credentials(s)  # 不抛异常
    assert any("ALLOW_WEAK_CREDS 已启用" in r.message for r in caplog.records), (
        f"逃生口放行时必须打 ERROR 警示，实际日志：{[r.message for r in caplog.records]}"
    )


def test_fatal_credentials_only_warn_in_development(caplog):
    """开发/测试环境只告警——保证本地与 CI 能直接跑起来。"""
    s = _settings(
        beidou_env="development",
        secret_key="beidou-dev-secret-change-me",
        admin_password="admin123",
    )
    with caplog.at_level(logging.WARNING, logger="beidou.config"):
        _enforce_strong_credentials(s)  # 不抛异常
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


# ---------- 二、偏弱项：绝不阻断启动（混合尺度的核心） ----------


def test_empty_credentials_refuse_start_even_in_development():
    """空凭据在**任何环境**都拒绝启动（含开发）。

    回归背景（对抗式审查 L2 后追加）：db.py 首次启动引导会用 settings.admin_password
    直接建管理员——开发模式下空 ADMIN_PASSWORD 会造出**空口令 admin** 并落库，
    verify_password("", hash) 实测为 True。空 = 没设，不属于"自定但弱"的容忍范围。
    """
    with pytest.raises(RuntimeError):
        _enforce_strong_credentials(
            _settings(beidou_env="development", secret_key="", admin_password=_STRONG_PASSWORD)
        )
    with pytest.raises(RuntimeError):
        _enforce_strong_credentials(
            _settings(beidou_env="development", secret_key=_STRONG_SECRET, admin_password="")
        )


def test_whitespace_only_credentials_are_treated_as_empty():
    """纯空白凭据必须按空值对待（strip 后判定），不得绕过致命集。

    回归背景：`"   "` 不在黑名单集合里，旧写法会落到"偏弱→仅告警"分支，
    生产环境带着一个空白密钥就启动了。
    """
    s = _settings(secret_key="   ")
    fatal, _weak, empty = _evaluate_credentials(s)
    assert empty is True and fatal, "纯空白 SECRET_KEY 应判为空凭据且致命"
    with pytest.raises(RuntimeError):
        _enforce_strong_credentials(s)
    # 开发环境同样拒绝（空凭据全环境拒启）
    with pytest.raises(RuntimeError):
        _enforce_strong_credentials(_settings(beidou_env="development", secret_key="   "))


def test_surrounding_whitespace_credentials_are_stripped_for_strong_values():
    """强凭据首尾带空白不误伤：strip 后合法即放行（避免复制粘贴多空格导致拒启）。"""
    _enforce_strong_credentials(
        _settings(secret_key=f"  {_STRONG_SECRET}  ", admin_password=f" {_STRONG_PASSWORD} ")
    )  # 不得抛异常


def test_short_custom_secret_does_not_refuse_in_production(caplog):
    """自定但偏短的密钥**不**拒绝启动（否则历史部署升级即停机会很痛）。

    这是混合尺度与"一律 fail-fast"的关键差别——回归钉子。
    """
    s = _settings(secret_key="my-own-secret-20ch")  # 18 字符，够自定但不够长
    with caplog.at_level(logging.ERROR, logger="beidou.config"):
        _enforce_strong_credentials(s)  # 不得抛异常
    assert any("偏弱" in r.message for r in caplog.records), "偏弱项应告警但不阻断"


def test_short_custom_password_does_not_refuse_in_production():
    """自定但偏短的管理员口令同样只告警。"""
    s = _settings(admin_password="MyPass1234")  # 10 字符
    _enforce_strong_credentials(s)  # 不得抛异常


def test_low_diversity_secret_does_not_refuse_but_warns(caplog):
    """`"a"*64` 够长却毫无随机性——按混合尺度归入偏弱（告警），不拒绝启动。"""
    s = _settings(secret_key="a" * 64)
    with caplog.at_level(logging.ERROR, logger="beidou.config"):
        _enforce_strong_credentials(s)  # 不得抛异常
    assert any("多样性过低" in r.message for r in caplog.records)


def test_strong_credentials_pass_without_fatal_or_weak():
    """强凭据应完全无声通过（不产生任何问题项，也不触发空凭据标记）。"""
    fatal, weak, empty = _evaluate_credentials(_settings())
    assert fatal == [] and weak == [] and empty is False


# ---------- 三、ALLOW_WEAK_CREDS 取值健壮性 ----------


@pytest.mark.parametrize("raw,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True), (" On ", True),
    ("0", False), ("false", False), ("no", False), ("", False), ("2", False), ("abc", False),
])
def test_allow_weak_creds_parsing_is_robust(raw, expected):
    """空串/非法值必须被安全地视为"未启用"，**不得**抛 pydantic ValidationError。

    回归背景：compose 里透传可选变量的标准写法 `ALLOW_WEAK_CREDS: ${ALLOW_WEAK_CREDS:-}`
    会传进空串；若该字段是 `bool` 类型，pydantic 会在**模块导入期**抛
    ValidationError，报错内容与凭据毫无关系、极难定位。
    """
    s = _settings(allow_weak_creds=raw)
    assert s.weak_creds_allowed is expected


def test_empty_allow_weak_creds_still_enforces_gate():
    """空串（未启用）时门禁仍必须生效——不能因取值健壮化而把门禁放空。"""
    s = _settings(secret_key="beidou-dev-secret-change-me", allow_weak_creds="")
    with pytest.raises(RuntimeError):
        _enforce_strong_credentials(s)


# ---------- 四、部署文件回归：compose 必须是合法 YAML ----------


def test_docker_compose_is_valid_yaml():
    """docker-compose.yml 必须可被 YAML 解析。

    回归背景（Critical）：曾因 `SECRET_KEY: ${SECRET_KEY:?...(generate with: openssl ...)}`
    中 `with:` 后的 ": " 被 YAML 当作映射指示符 → `ScannerError: mapping values are not
    allowed here`，导致 **所有** compose 部署在解析阶段即失败，连密钥设对的用户也一起挂。
    """
    text = _COMPOSE.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:  # pragma: no cover - 失败即回归
        pytest.fail(f"docker-compose.yml 不是合法 YAML：{exc}")
    assert isinstance(data, dict) and "services" in data, "compose 顶层结构异常"


def test_docker_compose_has_no_public_fallback_and_wires_escape_hatch():
    """compose 不得再带公开占位兜底；且必须透传 ALLOW_WEAK_CREDS（否则逃生口对 compose 无效）。"""
    env = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))["services"]["beidou"]["environment"]

    secret = env["SECRET_KEY"]
    assert "please-change-me" not in secret, "不得再把公开占位值写进 compose"
    assert ":?" in secret, "SECRET_KEY 必须为必填（缺失即报错），不允许静默兜底"

    # 部署环境必须固化为生产，否则门禁只告警不拦截
    assert env["BEIDOU_ENV"] == "production"

    # 逃生口必须真的透传到容器，否则注释里承诺的"临时保命"根本无法使用
    assert "ALLOW_WEAK_CREDS" in env, "ALLOW_WEAK_CREDS 必须透传，否则逃生口对 compose 部署无效"
