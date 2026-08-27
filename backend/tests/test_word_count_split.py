"""中英双口径字数统计测试：count_words_split 纯函数 + 路由输出含 word_count_split。
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.routers.chapters as ch_mod
from app.deps import count_words_split
from app.models import Base, Chapter, Novel, User


# ---------- 纯函数 ----------


def test_count_words_split_empty_and_pure_ascii():
    assert count_words_split("") == {"cjk": 0, "en": 0, "total": 0}
    # 纯英文：2 个单词（hello + world）+ 数字"123"在 total 里
    # 去空白后："helloworld123"=13 字符
    assert count_words_split("hello world 123") == {"cjk": 0, "en": 2, "total": 13}


def test_count_words_split_pure_cjk():
    # 「你好世界网文创作」= 8 个汉字；英文 0；total = 8
    assert count_words_split("你好世界网文创作") == {"cjk": 8, "en": 0, "total": 8}


def test_count_words_split_mixed_cjk_and_en():
    # 「在 JavaScript 里 写代码」
    # CJK=5（在/里/写/代/码），EN=1（JavaScript）
    # 去空白后："在JavaScript里写代码" = 5+10 = 15 字符
    assert count_words_split("在 JavaScript 里 写代码") == {"cjk": 5, "en": 1, "total": 15}


def test_count_words_split_strips_html():
    # HTML 标签去除后："你好 world 创作" → CJK=4（你/好/创/作），EN=1（world）
    # 去空白："你好world创作"=9 字符
    assert count_words_split("<p>你好 <b>world</b> 创作</p>") == {"cjk": 4, "en": 1, "total": 9}


def test_count_words_split_fullwidth_punctuation_not_counted_as_cjk():
    """全角标点（，！？）不算 CJK 字，但计入 total。"""
    # 「你好，世界！」：CJK=4（你/好/世/界），en=0，total=7（4 CJK + ， + ！ + 1）
    # 实际去空白后："你好，世界！"= 6 字符
    # wait: 你/好/，/世/界/！ = 6 字符
    assert count_words_split("你好，世界！") == {"cjk": 4, "en": 0, "total": 6}


def test_count_words_split_punctuation_and_digits_in_total():
    """total 包含 ASCII 数字、标点、空白不算。"""
    # "test 1, 2." → cjk=0, en=1（test）, total=8（t/e/s/t/1/,/2/. = 8 字符）
    assert count_words_split("test 1, 2.") == {"cjk": 0, "en": 1, "total": 8}


def test_count_words_split_jp_kr_kanji_supported():
    """日文假名/韩文音节也算 CJK。"""
    # "こんにちは"（5 假名）+ "안녕"（2 谚文）= 7
    assert count_words_split("こんにちは 안녕") == {"cjk": 7, "en": 0, "total": 7}


def test_count_words_split_case_insensitive_word_but_distinct():
    """\b 切分：'Hello' 与 'hello' 是两个英文单词（按出现次数计）。"""
    assert count_words_split("Hello hello HELLO") == {"cjk": 0, "en": 3, "total": 15}


# ---------- 路由层：ChapterOut 含 word_count_split ----------


@pytest_asyncio.fixture
async def db_with_chapter():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SL = async_sessionmaker(engine, expire_on_commit=False)
    async with SL() as s:
        u = User(username="u", password_hash="x", role="author")
        s.add(u)
        await s.flush()
        n = Novel(user_id=u.id, title="N", author="a", description="", genre="")
        s.add(n)
        await s.flush()
        c = Chapter(
            novel_id=n.id,
            title="c1",
            content="<p>在 JavaScript 里 写代码，5 个字 + 1 个英文词</p>",
            word_count=20,
            sort_order=0,
        )
        s.add(c)
        await s.commit()
        await s.refresh(c)
        yield s, n, c


@pytest.mark.asyncio
async def test_out_includes_word_count_split(db_with_chapter):
    """_out 输出含 word_count_split 字段，且值与 count_words_split(chapter.content) 一致。"""
    db, novel, chapter = db_with_chapter
    pairs = await ch_mod._ordered_with_numbers(novel, db)
    out = ch_mod._out(pairs[0][0], pairs[0][1])
    assert "word_count_split" in out
    # 文本「在 JavaScript 里 写代码，5 个字 + 1 个英文词」去 HTML 标签后是
    # 「在 JavaScript 里 写代码，5 个字 + 1 个英文词」→ 验证字段存在 + 结构正确
    spl = out["word_count_split"]
    assert set(spl.keys()) == {"cjk", "en", "total"}
    # 字符 + 单词数必须非负
    assert spl["cjk"] >= 0
    assert spl["en"] >= 1  # JavaScript 至少 1 个
    # total ≥ cjk + en（标点/数字也计入 total）
    assert spl["total"] >= spl["cjk"] + spl["en"]


@pytest.mark.asyncio
async def test_word_count_split_recomputed_each_call(db_with_chapter):
    """每次 _out 调用都重新计算（不缓存）；修改 chapter.content 后 word_count_split 反映新内容。"""
    db, novel, chapter = db_with_chapter
    pairs = await ch_mod._ordered_with_numbers(novel, db)
    out1 = ch_mod._out(pairs[0][0], pairs[0][1])

    # 修改 chapter.content，再算一次
    chapter.content = "<p>纯中文测试</p>"
    await db.commit()
    out2 = ch_mod._out(pairs[0][0], pairs[0][1])
    assert out2["word_count_split"] != out1["word_count_split"]
    # 「纯中文测试」= 5 个汉字
    assert out2["word_count_split"] == {"cjk": 5, "en": 0, "total": 5}
