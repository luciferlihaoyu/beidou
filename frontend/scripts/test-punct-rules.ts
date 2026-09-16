import {
  collapseDuplicatePunct,
  normalizeEllipsisAndDash,
  normalizeParagraph,
  normalizeQuotes,
  resolveTyped,
} from "../src/lib/punctRules.ts";

let pass = 0, fail = 0;
function eq(actual: unknown, expected: unknown, label: string) {
  const a = JSON.stringify(actual), b = JSON.stringify(expected);
  if (a === b) { pass++; } else { fail++; console.log(`✗ ${label}\n   得到 ${a}\n   期望 ${b}`); }
}

// ---- 批量规范化 ----
eq(normalizeParagraph("　　他说:好"), "他说：好", "行首全角空格 + 半角冒号");
eq(normalizeParagraph("你好,,世界"), "你好，世界", "重复逗号合并");
eq(normalizeParagraph("真的吗???"), "真的吗？？", "问号 3+ 收敛为 2（保留强调）");
eq(normalizeParagraph("不可能!!!!"), "不可能！！", "叹号 3+ 收敛为 2");
eq(normalizeParagraph("你敢?!"), "你敢？！", "叹号问号混用保留");
eq(normalizeParagraph("等等..."), "等等……", "三个点 → 省略号");
eq(normalizeParagraph("等等。。。"), "等等……", "三个中文句号 → 省略号");
eq(normalizeParagraph("他来了--然后就走了"), "他来了——然后就走了", "双连字符 → 破折号");
eq(normalizeParagraph("他来了—然后就走了"), "他来了——然后就走了", "单破折号补成双");
eq(normalizeParagraph('他说"你好"'), "他说“你好”", "直引号配对");
eq(normalizeParagraph("版本3.14发布了"), "版本3.14发布了", "数字中的点不动！");
eq(normalizeParagraph("abc.def"), "abc.def", "英文句点不动");
eq(normalizeParagraph("汉字后空格 ,"), "汉字后空格，", "标点前空格清理");
eq(normalizeParagraph("句尾空格   "), "句尾空格", "行尾空格清理");
eq(normalizeParagraph("（半角括号）(全角)"), "（半角括号）（全角）", "半角括号转全角");

// ---- 输入时转换 ----
eq(resolveTyped(",", "你好", ""), { kind: "insert", text: "，" }, "中文后逗号");
eq(resolveTyped(",", "abc", ""), null, "英文后逗号不动");
eq(resolveTyped(".", "3", ""), null, "数字后句点不动（3.14）");
eq(resolveTyped('"', "他说", ""), { kind: "insert", text: "“" }, "引号开");
eq(resolveTyped('"', "他说“你好", ""), { kind: "insert", text: "”" }, "引号闭（奇偶）");
eq(resolveTyped('"', "a", "b"), null, "英文语境引号不动");
eq(resolveTyped(".", "等等。。", ""), { kind: "replace", back: 2, text: "……" }, "第三点 → 省略号");
eq(resolveTyped(".", "等等。。", "."), null, "后面还有点时先不动");
eq(resolveTyped("-", "他来了-", ""), { kind: "replace", back: 1, text: "——" }, "第二个连字符 → 破折号");
eq(resolveTyped("-", "他来了——", ""), null, "已是破折号不再加");
eq(resolveTyped("(", "他说", ""), { kind: "insert", text: "（" }, "中文后半角括号");
eq(resolveTyped("ab", "他说", ""), null, "多字符不处理（粘贴）");

// ---- 单点函数 ----
eq(normalizeQuotes('他"说"完"了"'), "他“说”完“了”", "引号交替配对");
eq(normalizeEllipsisAndDash("a..b....c"), "a..b……c", "仅 3+ 点转省略号");
eq(collapseDuplicatePunct("好。。。" ), "好。", "句号叠写合并为 1");

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
