"""u4a：lifespan 关服必须 await 收尾后台循环（关服后不许再留 pending 任务）。

缺陷（修前）：``cancel()`` 只是"发出取消请求"，被取消的任务要等下一次事件循环调度
才真正结束；``lifespan`` 在 cancel 之后直接返回，于是关服瞬间两条 ``while True``
循环仍是 pending。本仓已实测的后果：任何"同进程反复建拆 TestClient"的测试会卡死在
anyio blocking portal 的 ``join()``（faulthandler 栈实锤，u0a 因此只能选"不进 lifespan"绕开）。

判据（本文件核心，也是本单的验收核心）：退出 lifespan 之后**同步地、中间不 await**
立刻数一次 ``asyncio.all_tasks()``，必须为空。
- 修前：cancel 只是请求，任务尚未被调度收尾 → leftover 非空 → 红；
- 修后：``await asyncio.gather(..., return_exceptions=True)`` 已把它们等到 done → 绿。

⚠️ 必须避开真实 ``init_db()``：这里 monkeypatch 掉模块级名字 ``app.main.init_db``
（``lifespan`` 里 ``await init_db()`` 是模块全局名字查找，所以补丁生效）；
并且**实测断言 fake 被调用过一次**，不靠"应该生效"。
"""

import asyncio

import app.main as main_mod


def test_lifespan_shutdown_awaits_background_tasks(monkeypatch):
    """进 lifespan 起两条循环 → 退出 lifespan 后立刻数残留任务，必须为 0。"""
    calls = {"init_db": 0}

    async def _fake_init_db():
        calls["init_db"] += 1

    # lifespan 体内 `await init_db()` 查的是模块全局名字，故补丁打在 app.main 上
    monkeypatch.setattr(main_mod, "init_db", _fake_init_db, raising=True)

    app = main_mod.app
    assert app.router.lifespan_context is not None, "本用例依赖 app.router.lifespan_context"

    async def _exercise():
        async with app.router.lifespan_context(app):
            # monkeypatch 生效的实测证据（放在 red 之前，红证也覆盖得到）
            assert calls["init_db"] == 1, f"monkeypatch 未生效：lifespan 没走 fake init_db（calls={calls}）"
            running = [
                t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()
            ]
            assert len(running) >= 2, f"两条后台循环没起来，本用例失去意义：{running}"

        # —— 退出 lifespan 之后，同步地（中间不 await）立刻检查 ——
        leftover = [
            t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()
        ]
        assert leftover == [], f"关服后仍有 {len(leftover)} 个任务未收尾：{leftover}"

    asyncio.run(_exercise())
