# -*- coding: utf-8 -*-
"""validate_skillcard.py 的单元测试。

用 tmp_path 造迷你假技能卡逐项测检查项 1-7（各坏样本 exit 1 且输出含对应
检查项编号），再对真实技能卡目录做冒烟测试（锁住"技能卡现状可持续通过校验"）。

运行:
    PYTHONPATH=/data/dsh/北斗/.pydeps python3 -m pytest backend/tests/test_validate_skillcard.py -q
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path("/data/dsh/北斗")
SCRIPT_PATH = (REPO_ROOT / "beidou/backend/app/skillcards/novel-deconstruction"
               / "scripts/validate_skillcard.py")
REAL_SKILL_DIR = SCRIPT_PATH.parent.parent

_spec = importlib.util.spec_from_file_location("validate_skillcard", SCRIPT_PATH)
vsc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vsc)

MINI_SKILL_MD = """---
name: mini-skill
description: 迷你测试技能卡，仅用于结构校验单测
---

# Mini Skill

| 用户问的是 | 加载参考文件 |
|---|---|
| 核心维度 | `references/core.md` |

## 工作流

### 第 0 步:明确目的

### 第 1 步:分析

```bash
python scripts/tool.py <输入>
```
"""

MINI_CORE_MD = """# core

正文若干。

## 核对本五维度时必问的问题（Key Questions）

- Q1?

## 警示信号（Warning signals）

- ⚠️ 某信号

## Agent 硬指令（Agent instruction）

必须先做 X 再做 Y。
"""

MINI_TEMPLATE_MD = """# 报告模板

## 一、总览

全书统计。

## 二、要点

### 2.1 细节

要点细节。
"""


def build_mini(root: Path) -> Path:
    """在 root 下造一张默认全绿的迷你技能卡，返回其目录。"""
    (root / "references").mkdir(parents=True)
    (root / "assets").mkdir()
    (root / "scripts").mkdir()
    (root / "SKILL.md").write_text(MINI_SKILL_MD, encoding="utf-8")
    (root / "references/core.md").write_text(MINI_CORE_MD, encoding="utf-8")
    (root / "assets/report-template.md").write_text(MINI_TEMPLATE_MD, encoding="utf-8")
    (root / "scripts/tool.py").write_text("print('ok')\n", encoding="utf-8")
    return root


def run_validator(skill_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--skill-dir", str(skill_dir)],
        capture_output=True, text=True, timeout=30,
    )


def test_real_skillcard_smoke_passes():
    """真实技能卡目录当前全部合规：exit 0、7 项全过。"""
    proc = run_validator(REAL_SKILL_DIR)
    assert proc.returncode == 0, f"真实技能卡校验失败:\n{proc.stdout}\n{proc.stderr}"
    assert "PASS: 7 checks, FAIL: 0" in proc.stdout
    assert "❌" not in proc.stdout


def test_mini_skillcard_all_pass(tmp_path):
    proc = run_validator(build_mini(tmp_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS: 7 checks, FAIL: 0" in proc.stdout


# ---------------- 检查项 1-7 各一个坏样本 ----------------

def _bad_root(tmp_path, mutate) -> Path:
    root = build_mini(tmp_path)
    mutate(root)
    return root


def test_bad_frontmatter_missing_name(tmp_path):
    def mutate(root: Path):
        (root / "SKILL.md").write_text(
            MINI_SKILL_MD.replace("name: mini-skill\n", ""), encoding="utf-8")
    proc = run_validator(_bad_root(tmp_path, mutate))
    assert proc.returncode == 1
    assert "检查1" in proc.stdout and "name:" in proc.stdout


def test_bad_route_reference_404(tmp_path):
    def mutate(root: Path):
        (root / "SKILL.md").write_text(
            MINI_SKILL_MD.replace("`references/core.md`", "`references/ghost.md`"),
            encoding="utf-8")
    proc = run_validator(_bad_root(tmp_path, mutate))
    assert proc.returncode == 1
    assert "检查2" in proc.stdout and "references/ghost.md" in proc.stdout


def test_bad_orphan_reference(tmp_path):
    def mutate(root: Path):
        (root / "references/orphan.md").write_text("# 没人引用我\n", encoding="utf-8")
    proc = run_validator(_bad_root(tmp_path, mutate))
    assert proc.returncode == 1
    assert "检查3" in proc.stdout and "orphan.md" in proc.stdout


def test_bad_four_part_toc_only_does_not_rescue(tmp_path):
    """目录行不算数：正文节标题被改名后，即使顶部目录仍在也应被检查4点名。"""
    def mutate(root: Path):
        fake = "摘要\n1. 警示信号（Warning signals）\n1. Agent 硬指令（Agent instruction）\n"
        (root / "references/core.md").write_text(
            fake + MINI_CORE_MD.replace(
                "## 警示信号（Warning signals）", "## 其它备注"), encoding="utf-8")
    proc = run_validator(_bad_root(tmp_path, mutate))
    assert proc.returncode == 1
    assert "检查4" in proc.stdout and "core.md" in proc.stdout and "警示信号" in proc.stdout


def test_bad_missing_agent_instruction(tmp_path):
    def mutate(root: Path):
        (root / "references/core.md").write_text(
            MINI_CORE_MD.split("## Agent 硬指令")[0], encoding="utf-8")
    proc = run_validator(_bad_root(tmp_path, mutate))
    assert proc.returncode == 1
    assert "检查4" in proc.stdout and "Agent 硬指令" in proc.stdout


def test_bad_duplicate_step_number(tmp_path):
    def mutate(root: Path):
        text = (root / "SKILL.md").read_text(encoding="utf-8")
        text += "\n### 第 1 步:重复步骤\n\n不该出现第二个第 1 步。\n"
        (root / "SKILL.md").write_text(text, encoding="utf-8")
    proc = run_validator(_bad_root(tmp_path, mutate))
    assert proc.returncode == 1
    assert "检查5" in proc.stdout and "重复" in proc.stdout


def test_bad_template_wrong_subsection(tmp_path):
    """历史错号复现：'七、' 大节之下挂 8.2，必须被检查项 6 拦下。"""
    def mutate(root: Path):
        (root / "assets/report-template.md").write_text(
            MINI_TEMPLATE_MD.replace("## 二、要点", "## 七、要点")
                            .replace("### 2.1 细节", "### 8.2 细节"),
            encoding="utf-8")
    proc = run_validator(_bad_root(tmp_path, mutate))
    assert proc.returncode == 1
    assert "检查6" in proc.stdout and "小节错号" in proc.stdout


def test_bad_missing_script(tmp_path):
    def mutate(root: Path):
        text = (root / "SKILL.md").read_text(encoding="utf-8")
        text += "\n另外记得跑 `python scripts/ghost_tool.py`。\n"
        (root / "SKILL.md").write_text(text, encoding="utf-8")
    proc = run_validator(_bad_root(tmp_path, mutate))
    assert proc.returncode == 1
    assert "检查7" in proc.stdout and "scripts/ghost_tool.py" in proc.stdout


# ---------------- 其他行为 ----------------

def test_missing_skill_dir_exit_2(tmp_path):
    proc = run_validator(tmp_path / "no-such-dir")
    assert proc.returncode == 2
    assert "不存在" in proc.stderr


def test_default_skill_dir_is_script_location():
    """不带 --skill-dir 时默认校验脚本所在目录（真实技能卡）。"""
    proc = subprocess.run([sys.executable, str(SCRIPT_PATH)],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "FAIL: 0" in proc.stdout


# ---------------- 中文数字解析 ----------------

@pytest.mark.parametrize("raw,expected", [
    ("一", 1), ("二", 2), ("三", 3), ("四", 4), ("五", 5),
    ("六", 6), ("七", 7), ("八", 8), ("九", 9), ("十", 10),
    ("十一", 11), ("十四", 14), ("二十", 20),
])
def test_parse_cn_numeral_valid(raw, expected):
    assert vsc.parse_cn_numeral(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "甲"])
def test_parse_cn_numeral_invalid(raw):
    assert vsc.parse_cn_numeral(raw) is None
