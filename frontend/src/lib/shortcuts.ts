/** 全局快捷键：纯函数 + 序列化 + 匹配。
 *
 * 设计：KeyboardEvent → serializeKey() 序列化为小写 key 字符串（"ctrl+s" / "ctrl+shift+k" / "f11"），
 * 调用方在注册时声明一个 handlers map（key → action），用 useGlobalShortcuts 挂到 window。
 *
 * 不在输入框/可编辑元素聚焦时触发默认行为，由 hook 层过滤；这里只做纯函数。
 */

/** 序列化后的 key：小写 + 用 "+" 连接；修饰键顺序固定为 ctrl < alt < shift < meta */
export type KeyCombo = string;

/** 把 KeyboardEvent 序列化为统一的 KeyCombo 字符串。
 *  - 自动归一化为小写
 *  - 修饰键只识别 ctrl / alt / shift / meta（其他键忽略）
 *  - 修饰键本身（仅 ctrl）不作为主键返回 ""
 *  - 主键识别：e.key（"a" "Enter" "F11" "ArrowUp" "?" "/" "Escape" 等）
 */
export function serializeKey(e: KeyboardEvent): KeyCombo {
  const parts: string[] = [];
  // 修饰键统一为 ctrl/alt/shift/meta
  if (e.ctrlKey) parts.push("ctrl");
  if (e.altKey) parts.push("alt");
  if (e.shiftKey) parts.push("shift");
  if (e.metaKey) parts.push("meta");
  let main = e.key;
  // 修饰键本身不算主键
  if (main === "Control" || main === "Alt" || main === "Shift" || main === "Meta") {
    main = "";
  }
  if (main) {
    // 大小写归一：单字符小写；功能键保持原样（Enter/F11/ArrowUp/Escape/?/）
    if (main.length === 1) main = main.toLowerCase();
    parts.push(main);
  }
  return parts.join("+");
}

/** 判断 event 是否匹配某个 key 串。修饰键全部必须匹配（除非 combo 没声明该修饰键）。 */
export function matchesKey(e: KeyboardEvent, combo: KeyCombo): boolean {
  if (!combo) return false;
  const got = serializeKey(e).split("+");
  const want = combo.toLowerCase().split("+");
  // 主键必须相同
  if (got[got.length - 1] !== want[want.length - 1]) return false;
  // 修饰键集合必须完全相同（去重后 set 相等）
  const modWant = new Set(want.slice(0, -1));
  const modGot = new Set(got.slice(0, -1));
  if (modWant.size !== modGot.size) return false;
  for (const m of Array.from(modWant)) {
    if (!modGot.has(m)) return false;
  }
  return true;
}

/** 判断 KeyboardEvent.target 是否位于"可编辑"区域（input / textarea / contenteditable）。
 *  hook 层在 editable 元素聚焦时跳过全局快捷键，避免破坏 IME 与编辑键。 */
export function isEditableTarget(target: EventTarget | null): boolean {
  if (!target || !(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (target.isContentEditable) return true;
  return false;
}

/** 北斗的快捷键清单（单一事实源，给 UI 速查面板 + 实际绑定都用同一份）。
 *  字段：key / label / desc / section（分组），UI 速查面板直接渲染。 */
export interface ShortcutEntry {
  key: KeyCombo;
  label: string;
  desc: string;
  section: "写作" | "保存" | "AI" | "导航" | "视图";
}

export const SHORTCUTS: ShortcutEntry[] = [
  { key: "ctrl+b", label: "加粗", desc: "选区加粗（编辑器内）", section: "写作" },
  { key: "ctrl+i", label: "斜体", desc: "选区斜体（编辑器内）", section: "写作" },
  { key: "ctrl+u", label: "下划线", desc: "选区下划线（编辑器内）", section: "写作" },
  { key: "ctrl+z", label: "撤销", desc: "编辑器内置撤销", section: "写作" },
  { key: "ctrl+shift+z", label: "重做", desc: "编辑器内置重做", section: "写作" },

  { key: "ctrl+s", label: "立即保存", desc: "强制保存当前章节（跳过自动保存去抖）", section: "保存" },
  { key: "ctrl+shift+s", label: "立即快照", desc: "存一个手动快照点（带 label 输入）", section: "保存" },

  { key: "ctrl+enter", label: "AI 续写", desc: "触发 AI 续写技能卡（需打开 AI 面板）", section: "AI" },
  { key: "ctrl+shift+r", label: "AI 改写", desc: "改写当前选中文本（需打开 AI 面板）", section: "AI" },

  { key: "ctrl+k", label: "命令面板", desc: "搜索 / 跳转 / 动作（仿 Notion/Slack）", section: "导航" },
  { key: "ctrl+f", label: "章内查找替换", desc: "打开章内搜索替换浮动条（编辑器内）", section: "导航" },
  { key: "ctrl+shift+f", label: "全文搜索", desc: "打开资料库全文搜索（人物/设定/章/资料卡）", section: "导航" },
  { key: "ctrl+g", label: "跳到章节", desc: "弹章节跳转", section: "导航" },

  { key: "f11", label: "沉浸模式", desc: "全屏写作（隐藏所有 chrome）", section: "视图" },
  { key: "ctrl+shift+t", label: "打字机", desc: "开关打字机模式（光标居中）", section: "视图" },
  { key: "?", label: "快捷键速查", desc: "打开当前快捷键速查面板", section: "视图" },
  { key: "escape", label: "取消", desc: "关闭弹窗 / 退出沉浸模式", section: "视图" },
];

/** 给 SHORTCUTS 排序后的速查（按 section + label） */
export function groupedShortcuts(): Record<string, ShortcutEntry[]> {
  const out: Record<string, ShortcutEntry[]> = {};
  for (const s of SHORTCUTS) {
    if (!out[s.section]) out[s.section] = [];
    out[s.section].push(s);
  }
  return out;
}

/** 友好的 key 展示：把 "ctrl" 替换为平台符号（Mac ⌘ / Win Ctrl），"shift" 替换为 ⇧ 等。 */
export function formatKey(combo: KeyCombo, isMac: boolean): string {
  const parts = combo.toLowerCase().split("+");
  return parts
    .map((p) => {
      if (p === "ctrl") return isMac ? "⌃" : "Ctrl";
      if (p === "alt") return isMac ? "⌥" : "Alt";
      if (p === "shift") return isMac ? "⇧" : "Shift";
      if (p === "meta") return isMac ? "⌘" : "Win";
      if (p === "enter") return isMac ? "↩" : "Enter";
      if (p === "escape") return "Esc";
      if (p === "arrowup") return "↑";
      if (p === "arrowdown") return "↓";
      if (p === "arrowleft") return "←";
      if (p === "arrowright") return "→";
      if (p === "?") return "?";
      if (p === "/") return "/";
      return p.toUpperCase();
    })
    .join(isMac ? "" : "+");
}
