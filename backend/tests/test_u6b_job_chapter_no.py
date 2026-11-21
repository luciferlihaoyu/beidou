"""u6b：章节任务投影必须带**真实章号**（chapter_no），否则下载报告的文件名只能用主键。

背景（实测）：后端报告页用 `chapter.sort_order` 当章号，而前端 job 投影里只有 `chapter_id`（主键），
于是「第 3 章」的文件会被命名成「第 47 章」——用户看到的是错的数字。
本文件钉两件事：① 投影带 chapter_no 且等于调用方给的真章号；② 章号与主键必须可区分（防止有人写成同一个值）。
"""

import app.routers.ai_factory as factory


class _Job:
    id = 1
    chapter_id = 47
    status = "done"
    outline = ""
    actual_words = 0
    attempt = 1
    review_issues = None
    review_score = None
    summary = ""
    finished_at = None
    draft_text = ""


class _Chapter:
    title = "开篇"


def test_chapter_no_is_the_real_number_not_the_primary_key():
    out = factory._job_out(_Job(), _Chapter(), 3)
    assert out["chapter_no"] == 3, f"投影没带真章号：{out.get('chapter_no')!r}，前端只能退回主键"
    assert out["chapter_id"] == 47, "主键口径被改坏了"
    assert out["chapter_no"] != out["chapter_id"], "章号被写成了主键——这正是本用例要防的错"


def test_chapter_no_survives_when_chapter_row_is_gone():
    """章节行被删（投影里标题显示"已删除"）时，章号仍须是调用方给的那个数。"""
    out = factory._job_out(_Job(), None, 9)
    assert out["chapter_no"] == 9
    assert out["chapter_title"] == "（已删除）"
