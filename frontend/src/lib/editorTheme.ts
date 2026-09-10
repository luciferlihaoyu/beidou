/** 写作区主题（横线 + 背景）：纯工具函数，零依赖。
 *
 * 数据结构（localStorage 也按此存）：
 * - lineType: blank | lined | grid | dotted | cross（五类）
 * - lineColor: 浅色 hex
 * - lineSpacing: 像素 (16-48)
 * - bgColor: 编辑区背景色
 * - bgImage: base64 dataURL（null = 无）
 * - bgImageOpacity: 0-1
 * - bgImageBlur: 0-20px
 * - bgImageFit: cover | contain | tile
 *
 * 持久化：localStorage 键 beidou:editor-theme（全局共享，不分小说）
 */

export type LineType = "blank" | "lined" | "grid" | "dotted" | "cross";
export type BgImageFit = "cover" | "contain" | "tile";

export interface EditorTheme {
  lineType: LineType;
  lineColor: string;
  lineSpacing: number;
  bgColor: string;
  bgImage: string | null;
  bgImageOpacity: number;
  bgImageBlur: number;
  bgImageFit: BgImageFit;
  /** 主题预设：选了预设就批量改 lineColor/bgColor，但用户能微调 */
  preset: string;
}

export const DEFAULT_THEME: EditorTheme = {
  lineType: "blank",
  lineColor: "#e5e7eb",
  lineSpacing: 28,
  bgColor: "#ffffff",
  bgImage: null,
  bgImageOpacity: 0.5,
  bgImageBlur: 0,
  bgImageFit: "cover",
  preset: "default",
};

/** 预设主题（点击即套用整套配色） */
export const PRESETS: { id: string; label: string; lineColor: string; bgColor: string; lineType: LineType }[] = [
  { id: "default", label: "经典白", lineColor: "#e5e7eb", bgColor: "#ffffff", lineType: "blank" },
  { id: "paper", label: "米黄纸", lineColor: "#d4c5a0", bgColor: "#fdf6e3", lineType: "lined" },
  { id: "grid", label: "稿纸格", lineColor: "#d1d5db", bgColor: "#fafafa", lineType: "grid" },
  { id: "cross", label: "田字格", lineColor: "#fbbf24", bgColor: "#fffbeb", lineType: "cross" },
  { id: "dotted", label: "点阵", lineColor: "#cbd5e1", bgColor: "#f8fafc", lineType: "dotted" },
  { id: "night", label: "夜读", lineColor: "#3f3f46", bgColor: "#18181b", lineType: "lined" },
  { id: "ocean", label: "海洋", lineColor: "#93c5fd", bgColor: "#eff6ff", lineType: "lined" },
  { id: "rose", label: "玫瑰", lineColor: "#fda4af", bgColor: "#fff1f2", lineType: "lined" },
];

const STORAGE_KEY = "beidou:editor-theme";

export function loadTheme(): EditorTheme {
  if (typeof localStorage === "undefined") return DEFAULT_THEME;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_THEME;
    const obj = JSON.parse(raw);
    return { ...DEFAULT_THEME, ...obj };
  } catch {
    return DEFAULT_THEME;
  }
}

export function saveTheme(theme: EditorTheme) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(theme));
}

/** 生成横线 CSS background-image（多行字符串注入到 inline style） */
export function buildLineBackground(t: EditorTheme): string {
  const s = t.lineSpacing;
  const c = t.lineColor;
  switch (t.lineType) {
    case "blank":
      return "none";
    case "lined":
      return `linear-gradient(to bottom, transparent calc(${s}px - 1px), ${c} calc(${s}px - 1px), ${c} ${s}px, transparent ${s}px)`;
    case "grid":
      return (
        `linear-gradient(to right, ${c} 1px, transparent 1px) 0 0 / ${s}px ${s}px,` +
        ` linear-gradient(to bottom, ${c} 1px, transparent 1px) 0 0 / ${s}px ${s}px`
      );
    case "dotted":
      return `radial-gradient(circle, ${c} 1px, transparent 1.5px) 0 0 / ${s}px ${s}px`;
    case "cross": {
      // 田字格：每格 s x s；中间用 + 号把方块 4 等分
      // SVG dataURL 嵌入
      const inner = s / 2;
      const svg = `<svg xmlns='http://www.w3.org/2000/svg' width='${s}' height='${s}' viewBox='0 0 ${s} ${s}'><line x1='0' y1='0' x2='${s}' y2='0' stroke='${c}' stroke-width='1'/><line x1='0' y1='${s}' x2='${s}' y2='${s}' stroke='${c}' stroke-width='1'/><line x1='0' y1='0' x2='0' y2='${s}' stroke='${c}' stroke-width='1'/><line x1='${s}' y1='0' x2='${s}' y2='${s}' stroke='${c}' stroke-width='1'/><line x1='${inner}' y1='0' x2='${inner}' y2='${s}' stroke='${c}' stroke-width='0.5' stroke-dasharray='2 2'/><line x1='0' y1='${inner}' x2='${s}' y2='${inner}' stroke='${c}' stroke-width='0.5' stroke-dasharray='2 2'/></svg>`;
      return `url("data:image/svg+xml;utf8,${encodeURIComponent(svg)}") 0 0 / ${s}px ${s}px`;
    }
  }
}

/** 背景图（base64）拼装 background-image，配合 opacity/blur 用 ::before 层 */
export function buildImageBackground(t: EditorTheme): string | undefined {
  if (!t.bgImage) return undefined;
  switch (t.bgImageFit) {
    case "cover":
      return `url("${t.bgImage}") center / cover no-repeat`;
    case "contain":
      return `url("${t.bgImage}") center / contain no-repeat`;
    case "tile":
      return `url("${t.bgImage}") repeat`;
  }
}

export const LINE_TYPE_LABELS: Record<LineType, string> = {
  blank: "空白",
  lined: "横线纸",
  grid: "方格",
  dotted: "点阵",
  cross: "田字格",
};

export const FIT_LABELS: Record<BgImageFit, string> = {
  cover: "铺满",
  contain: "完整",
  tile: "平铺",
};

/** 由背景色亮度推导正文文字颜色（A3：暗色模式下避免「亮背景+浅字」不可读）。
 * 亮背景 → 深灰字；暗背景 → 浅灰字。YIQ 亮度公式粗判。
 */
export function textColorForBg(bgHex: string): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(bgHex.trim());
  if (!m) return "inherit";
  const n = parseInt(m[1], 16);
  const r = (n >> 16) & 0xff;
  const g = (n >> 8) & 0xff;
  const b = n & 0xff;
  const yiq = (r * 299 + g * 587 + b * 114) / 1000;
  return yiq >= 140 ? "#24272a" : "#e8eaed";
}
