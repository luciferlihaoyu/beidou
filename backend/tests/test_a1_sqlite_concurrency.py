"""A1：SQLite 并发设置——必须 WAL + 30s 等锁。

背景（实测，不是推测）：2026-11-26 上线容器的近 1 小时日志里出现 **5 次 500**，
全部是 `sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) database is locked`，
端点为 `PUT /api/ai/configs/2`（4 次）与 `POST /api/ai-factory/projects/3/jobs/49/reset`。
当时库的 `journal_mode` 实测为 **delete**（回滚日志：同一时刻只允许一个写入者），
而库里同时有 2 章处于 `writing`（夜间生成）——用户保存设置就撞锁，等 5s 拿不到即 500。

本文件钉两件事：① 引擎连接自带 30s 等锁（不再用驱动默认 5s）；② 启动时把库切实到 WAL，
并且**以实读回的模式为准**（防止"设了但没生效"，那正是本仓历史上最贵的误绿形态）。
"""

import asyncio

import app.db as db


def _run(coro):
    return asyncio.run(coro)


def test_engine_connection_waits_30s_instead_of_driver_default(tmp_path):
    """驱动默认 5s 会把"排队"变成"500"。这条钉住 connect_args 真的传到了驱动。"""
    eng = db._make_engine(f"sqlite+aiosqlite:///{tmp_path / 'lock.db'}")

    async def go():
        async with eng.connect() as conn:
            return (await conn.exec_driver_sql("PRAGMA busy_timeout")).scalar()

    try:
        busy = _run(go())
        assert busy == 30000, f"等锁上限不是 30s 而是 {busy!r}ms"
    finally:
        _run(eng.dispose())


def test_pragmas_switch_to_wal_and_report_measured_mode(tmp_path):
    """WAL 必须真的生效，且函数返回的是**实测**值而不是它"以为设成了什么"。"""
    eng = db._make_engine(f"sqlite+aiosqlite:///{tmp_path / 'wal.db'}")

    async def go():
        applied = await db._apply_sqlite_pragmas(eng)
        async with eng.connect() as conn:  # 另开一条连接回读，防"只在这一条连接上生效"
            readback = (await conn.exec_driver_sql("PRAGMA journal_mode")).scalar()
        return applied, readback

    try:
        applied, readback = _run(go())
        assert applied["journal_mode"].lower() == "wal", f"函数回报 {applied!r}"
        assert applied["busy_timeout"] == 30000
        assert readback.lower() == "wal", f"新连接读回仍是 {readback!r}"
    finally:
        _run(eng.dispose())


def test_wal_failure_is_visible_as_warning(caplog):
    """切不成 WAL 时必须能在日志里被看见。

    这条守的是**"失败无声"**：本仓没有配置 application logger 级别（默认 WARNING），
    所以成功走 INFO 在 docker logs 里根本看不到。若失败也走 INFO，
    库静默退回单写者、生成期间继续 500，而日志一片安静——比没修更糟。
    """
    import logging as _logging

    with caplog.at_level(_logging.INFO, logger="app.db"):
        db._log_sqlite_pragmas({"journal_mode": "delete", "busy_timeout": 30000})
    assert any(r.levelno == _logging.WARNING for r in caplog.records), "切不成 WAL 却没有 WARNING"

    caplog.clear()
    with caplog.at_level(_logging.INFO, logger="app.db"):
        db._log_sqlite_pragmas({"journal_mode": "wal", "busy_timeout": 30000})
    assert not any(r.levelno >= _logging.WARNING for r in caplog.records), "成功时不该告警"


def test_wal_failure_warning_survives_unknown_mode(caplog):
    """回报为空/怪值时也必须告警（"没拿到"不等于"成功了"）。"""
    import logging as _logging

    with caplog.at_level(_logging.INFO, logger="app.db"):
        db._log_sqlite_pragmas({})
    assert any(r.levelno == _logging.WARNING for r in caplog.records)
