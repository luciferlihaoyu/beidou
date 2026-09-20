# -*- coding: utf-8 -*-
"""fact_extractor 单元测试(pytest,单文件 <30s)。

纯标准库脚本没有包结构,通过 sys.path 引入 scripts/fact_extractor.py;
命令行入口的报错行为用 subprocess 验证(退出码非 0 + 可读消息,无 traceback)。
测试数据在 tmp_path 造小型假小说目录:1 个 chapter_index.json + 2 个章节 txt,
txt 内埋数值规则一句、时间词一句、重复人名两三次。
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[1] / "app" / "skillcards" / "novel-deconstruction"
SCRIPT = SKILL_ROOT / "scripts" / "fact_extractor.py"
if str(SCRIPT.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT.parent))

import fact_extractor  # noqa: E402

CH1 = (
    "林凡从昏迷中醒来，识海中悬浮着一枚【青冥珠】。\n"
    "系统提示：每击杀1妖兽=10积分。\n"
    "三日后，宗门大比将启。林凡暗自握紧了拳头。\n"
)
CH2 = (
    "林凡连斩十只妖兽，收获一百积分。\n"
    "岁月流转，又是一年过去。\n"
    "这个秘密只有老陈知道，却没有注意到袖中的玉佩。\n"
)


def make_book(tmp_path: Path, with_empty_chapter: bool = False) -> Path:
    d = tmp_path / "book_chapters"
    d.mkdir(parents=True, exist_ok=True)
    (d / "0001_第一章_觉醒.txt").write_text(CH1, encoding="utf-8")
    (d / "0002_第二章_初战.txt").write_text(CH2, encoding="utf-8")
    chapters = [
        {"no": 1, "title": "第一章 觉醒", "chars": 40, "start": 0, "end": 100},
        {"no": 2, "title": "第二章 初战", "chars": 40, "start": 100, "end": 200},
    ]
    if with_empty_chapter:
        (d / "0003_第三章_空.txt").write_text("", encoding="utf-8")
        chapters.append({"no": 3, "title": "第三章 空", "chars": 0, "start": 200, "end": 200})
    index = {
        "source": "fake.txt",
        "total_chars": len(CH1) + len(CH2),
        "chapter_count": len(chapters),
        "chapters": chapters,
    }
    (d / "chapter_index.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8")
    return d


# ---------------------------------------------------------------- 提取逻辑
def test_generates_facts_index(tmp_path):
    d = make_book(tmp_path)
    result = fact_extractor.run(str(d))
    out = d / "facts_index.json"
    assert out.is_file()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert set(data.keys()) == {"facts", "topics"}
    assert data["facts"] == result["facts"]
    # facts 按章号排序
    chapters_seq = [f["chapter"] for f in data["facts"]]
    assert chapters_seq == sorted(chapters_seq)


def test_numeric_hits(tmp_path):
    d = make_book(tmp_path)
    result = fact_extractor.run(str(d))
    facts = result["facts"]
    # 第1章命中数值规则 "每击杀1妖兽=10积分"
    ch1_num = [f for f in facts if f["category"] == "numeric" and f["chapter"] == 1]
    assert any("10积分" in f["text"] for f in ch1_num)
    # 第2章命中中文数字结算 "一百积分"
    ch2_num = [f for f in facts if f["category"] == "numeric" and f["chapter"] == 2]
    assert any("积分" in f["text"] for f in ch2_num)


def test_temporal_hits(tmp_path):
    d = make_book(tmp_path)
    result = fact_extractor.run(str(d))
    facts = result["facts"]
    assert any(
        f["category"] == "temporal" and f["chapter"] == 1 and "三日" in f["text"]
        for f in facts
    )
    assert any(
        f["category"] == "temporal" and f["chapter"] == 2 and "一年" in f["text"]
        for f in facts
    )


def test_topic_first_chapter(tmp_path):
    d = make_book(tmp_path)
    result = fact_extractor.run(str(d))
    lin = [t for t in result["topics"] if t["term"] == "林凡"]
    assert lin, f"人名 林凡 应入选 topics,实际: {result['topics']}"
    assert lin[0]["chapters"] == [1, 2]
    assert lin[0]["first_chapter"] == 1


def test_empty_chapter_skipped_gracefully(tmp_path):
    d = make_book(tmp_path, with_empty_chapter=True)
    result = fact_extractor.run(str(d))
    assert result["chapters"] == 2          # 空章被跳过
    assert result["empty_skipped"] == 1
    assert all(f["chapter"] in (1, 2) for f in result["facts"])


# ---------------------------------------------------------------- CLI 行为
def _cli(tmp_path, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, timeout=60,
    )


def test_cli_missing_index_readable_error(tmp_path):
    empty = tmp_path / "no_index"
    empty.mkdir()
    p = _cli(tmp_path, str(empty))
    assert p.returncode != 0
    combined = p.stdout + p.stderr
    assert "chapter_index.json" in combined          # 消息可读、指向问题根源
    assert "Traceback" not in combined               # 不许裸 traceback


def test_cli_missing_chapter_file(tmp_path):
    d = make_book(tmp_path)
    (d / "0002_第二章_初战.txt").unlink()             # 索引声明了但文件缺失
    p = _cli(tmp_path, str(d))
    assert p.returncode != 0
    combined = p.stdout + p.stderr
    assert "章节文件缺失" in combined
    assert "Traceback" not in combined


def test_cli_no_args_exits_nonzero():
    p = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=60)
    assert p.returncode != 0


def test_cli_nonexistent_dir(tmp_path):
    p = _cli(tmp_path, str(tmp_path / "does_not_exist"))
    assert p.returncode != 0
    assert "工作目录" in (p.stdout + p.stderr)


@pytest.mark.parametrize("dummy", [0])  # 占位保持文件为纯 pytest 单文件结构
def test_summary_counts(tmp_path, dummy):
    d = make_book(tmp_path)
    result = fact_extractor.run(str(d))
    dist = result["dist"]
    assert dist["numeric"] >= 2 and dist["temporal"] >= 2
    assert len(result["topics"]) >= 1


# --------------------------------- Web 白名单纯函数 extract_facts（skilltools 用）
PURE_TEXT = (
    "林凡睁开眼，识海中悬浮着一枚【青冥珠】。\n"
    "系统提示：每击杀1妖兽=10积分。\n"
    "林凡翻了个身。三日后，宗门大比将启。\n"
    "林凡暗自握紧了拳头，这个秘密无人知晓。\n"
)
FACT_KEYS = {"facts_numeric", "facts_temporal", "facts_entity", "facts_state", "topics"}


def test_extract_facts_hits_all_categories():
    out = fact_extractor.extract_facts(PURE_TEXT)
    assert set(out) == FACT_KEYS
    assert any("10积分" in f["text"] for f in out["facts_numeric"])
    assert any("三日" in f["text"] for f in out["facts_temporal"])
    assert any("青冥珠" in f["text"] for f in out["facts_entity"])
    assert any("无人知晓" in f["text"] for f in out["facts_state"])
    # topics 复用全书阈值(单章口径):林凡出现 3 次入选且带 count
    lin = [t for t in out["topics"] if t["term"] == "林凡"]
    assert lin and lin[0]["count"] == 3


def test_extract_facts_empty_returns_full_empty_structure():
    out = fact_extractor.extract_facts("")
    assert set(out) == FACT_KEYS
    assert all(v == [] for v in out.values())


def test_extract_facts_is_pure_and_deterministic():
    a = fact_extractor.extract_facts(PURE_TEXT)
    b = fact_extractor.extract_facts(PURE_TEXT)
    assert a == b  # 无副作用、无内部可变态


def test_extract_facts_swallows_internal_errors(monkeypatch):
    def boom(_t):
        raise RuntimeError("模拟内核崩溃")

    monkeypatch.setattr(fact_extractor, "_match_facts", boom)
    out = fact_extractor.extract_facts("任意文本")
    assert set(out) == FACT_KEYS and all(v == [] for v in out.values())
    # 非法输入类型同样兜底为空结构,不抛异常
    assert fact_extractor.extract_facts(None) == fact_extractor.extract_facts("")


def test_extract_facts_matches_cli_extracts(tmp_path):
    """纯函数与 CLI 走同一套规则:逐类事实(去章号后)必须逐一相等,杜绝口径漂移。"""
    d = make_book(tmp_path)
    for no, name in ((1, "0001_第一章_觉醒.txt"), (2, "0002_第二章_初战.txt")):
        text = (d / name).read_text(encoding="utf-8")
        pure = fact_extractor.extract_facts(text)
        cli = [
            {"category": f["category"], "text": f["text"], "detail": f["detail"]}
            for f in fact_extractor.extract_facts_for_chapter(no, text)
        ]
        flat = (
            pure["facts_numeric"] + pure["facts_temporal"]
            + pure["facts_entity"] + pure["facts_state"]
        )
        assert cli == flat
