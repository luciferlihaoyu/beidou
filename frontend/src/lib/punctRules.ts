/** 中文标点规则：单一事实来源（输入时即时转换 + 一键排版批量规范化共用）
 *
 * 此前规则分散在两处且口径不一致：
 * - components/TiptapEditor.tsx：输入时只处理 6 个半角标点，且要求前一字为中文
 * - pages/Editor.tsx：一键排版版另有引号配对、省略号归并
 * 两边对「引号 / 省略号 / 破折号 / 括号」的处理互不相同，写出来的稿子
 * 取决于作者是「打着打字」还是「事后排版」。本模块收敛为一份规则，
 * 并可用 node 直接跑（零依赖纯函数）。
 *
 * 设计取舍：
 * - 只在中文上下文转换半角标点（"3.14" / "a.b" 这类不能动）
 * - ！？ 的叠用是网文的强调手法（「你敢？！」「太好了！！」），**不合并**
 *   只把 3 个以上收敛为 2 个
 * - ，。；： 的叠用是纯手误，合并为 1 个
 * - 省略号统一为中文六点「……」，破折号统一为「——」
 */

/** CJK 字符（汉字 + 假名 + 谚文） */
export const CJK_RE = /[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]/;

/** 拉丁字母/数字（判断是否英文语境，避免动英文里的标点） */
const LATIN_RE = /[A-Za-z0-9]/;

/** 全角标点（已处于中文语境的显式信号） */
const FULL_PUNCT_RE = /[，。！？；：、（）【】《》“”‘’…—～·]/;

/** 是否中文语境：前一字是汉字/假名/谚文，或已是全角标点
 *  （只看 CJK 会漏掉「你好,世界,」这种连打——第二个逗号的上一字是」而非汉字） */
function inChineseContext(ch: string): boolean {
  return !!ch && (CJK_RE.test(ch) || FULL_PUNCT_RE.test(ch));
}

/** 半角 → 全角（一一对应且中文写作常用；不含 <>[]{} 这类歧义符号） */
export const HALF_TO_FULL: Record<string, string> = {
  ",": "，",
  ".": "。",
  "?": "？",
  "!": "！",
  ";": "；",
  ":": "：",
  "(": "（",
  ")": "）",
};

/** 成对/成对的引号（用于奇偶配对） */
const DQ_CHARS = ["“", "”"];
const SQ_CHARS = ["‘", "’"];

export function isCJK(ch: string): boolean {
  return !!ch && CJK_RE.test(ch);
}

function countOf(s: string, chars: string[]): number {
  let n = 0;
  for (const ch of s) if (chars.includes(ch)) n++;
  return n;
}

function lastChar(s: string): string {
  return s ? s[s.length - 1] : "";
}

/** 段内引号配对：直引号按奇偶决定左右（“ ” / ‘ ’） */
export function normalizeQuotes(text: string): string {
  let dq = 0;
  let sq = 0;
  let out = "";
  for (const ch of text) {
    if (ch === '"') {
      out += dq % 2 === 0 ? "“" : "”";
      dq++;
      continue;
    }
    if (ch === "'") {
      out += sq % 2 === 0 ? "‘" : "’";
      sq++;
      continue;
    }
    out += ch;
  }
  return out;
}

/** 省略号/破折号归一：. . . → ……；-- / — / ---- → —— */
export function normalizeEllipsisAndDash(text: string): string {
  return text
    .replace(/\.{3,}/g, "……") // 三个以上英文句点
    .replace(/。{3,}/g, "……") // 中文句号叠写（输入时逐个转换后的产物）
    .replace(/…{2,}/g, "……") // 已经是省略号的收敛为六点
    .replace(/-{2,}/g, "——") // 双连字符
    .replace(/—{2,}/g, "——") // 连写破折号收敛为两字
    .replace(/(?<!—)[—–](?!—)/g, "——"); // 单个破折号/连接号补成中文双破折号（已是成对的不动）
}

/** 重复标点收敛：，。；：、 叠用合并为 1；！？ 保留强调（3+ 收敛为 2） */
export function collapseDuplicatePunct(text: string): string {
  return text
    .replace(/([，。；：、])\1+/g, "$1")
    .replace(/！{3,}/g, "！！")
    .replace(/？{3,}/g, "？？")
    .replace(/[！？]{4,}/g, "！？");
}

/** 标点前的多余空格清理（半角空格 / 全角空格 / Tab） */
function stripSpaceBeforePunct(text: string): string {
  return text.replace(/[ \t\u3000]+([，。！？；：、）”’…—》】])/g, "$1");
}

/** 半角标点转全角：仅当上下文是中文（前一个非空字符为 CJK），避免动英文与数字 */
function fullWidthInCJKCtx(text: string): string {
  let out = "";
  for (const ch of text) {
    const full = HALF_TO_FULL[ch];
    if (full) {
      // 前一个非空字符；空/行首不转换（避免段落以「。」开头）
      const prev = out.replace(/[ \t\u3000]+$/, "").slice(-1);
      if (inChineseContext(prev)) {
        out = out.replace(/[ \t\u3000]+$/, ""); // 顺手收掉标点前空格
        out += full;
        continue;
      }
    }
    out += ch;
  }
  return out;
}

/** 单段批量规范化（一键排版用）：清行首空格 → 引号 → 省略号/破折号 → 重复标点 → 半角转全角 → 标点前空格 → 行尾空格 */
export function normalizeParagraph(input: string): string {
  let s = input.replace(/^[ \t\u3000]+/, "");
  s = normalizeQuotes(s);
  s = fullWidthInCJKCtx(s); // 先统一全角，重复标点才可能相邻
  s = normalizeEllipsisAndDash(s);
  s = collapseDuplicatePunct(s);
  s = stripSpaceBeforePunct(s);
  return s.replace(/[ \t\u3000]+$/, "");
}

/** 输入时转换结果：insert = 替换本次键入；replace = 回退 back 个字符再插入（省略号/破折号需要吞掉前面的字符） */
export type TypedResult =
  | { kind: "insert"; text: string }
  | { kind: "replace"; back: number; text: string };

/**
 * 输入时即时转换：返回替换结果（null = 保持原样）。
 *
 * @param input   本次键入的字符串（仅处理单字符）
 * @param before  光标前的同段文本（建议给 8-16 字，用于判断上下文与奇偶）
 * @param after   光标后的同段文本（建议给 8-16 字，用于判断是否还有后续字符）
 */
export function resolveTyped(input: string, before: string, after = ""): TypedResult | null {
  if (input.length !== 1) return null;
  const prev = lastChar(before);
  const next = after.slice(0, 1);

  // 引号：按段内奇偶决定左右；英文语境（前后都是拉丁字母/数字）不动
  if (input === '"' || input === "'") {
    if (LATIN_RE.test(prev) && LATIN_RE.test(next)) return null;
    const openChars = input === '"' ? DQ_CHARS : SQ_CHARS;
    const closed = countOf(before, openChars) % 2 === 1;
    return { kind: "insert", text: closed ? openChars[1] : openChars[0] };
  }

  // 省略号：第三次连打句点 → 把已有两个「。」（或两个半角点）换成「……」
  if (input === "." && next.startsWith(".")) return null; // 正在一串点中间插点：不猜，交给后续输入
  if (input === ".") {
    if (before.endsWith("。。")) return { kind: "replace", back: 2, text: "……" };
    // 半角两点的省略号只在中文语境归并（"etc..." 这类英文不碰）
    if (before.endsWith("..") && inChineseContext(before.slice(-3, -2))) {
      return { kind: "replace", back: 2, text: "……" };
    }
  }

  // 破折号：第二个连字符 → 把已输入的「-」补成「——」；已是破折号则不动
  if (input === "-") {
    if (before.endsWith("-") && !before.endsWith("——")) return { kind: "replace", back: 1, text: "——" };
    if (before.endsWith("—")) return null;
  }

  // 半角标点 → 全角：需中文上下文（前一字为 CJK），避免动 3.14 / a.b
  const full = HALF_TO_FULL[input];
  if (full && inChineseContext(prev)) return { kind: "insert", text: full };

  return null;
}
