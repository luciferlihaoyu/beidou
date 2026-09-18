/** 写作区主题（横线 + 背景）：纯工具函数，零依赖。
 *
 * 数据结构（localStorage 也按此存）：
 * - lineType: blank | lined | grid | dotted | cross（五类）
 * - lineColor: 浅色 hex
 * - lineType/lineColor: 线型与颜色
 * - lineSpacingMode: auto（跟随文字行距，默认）| manual（固定像素）
 * - lineSpacing: 手动模式下的像素值 (16-48)
 * - lineOffset: 自动模式下的相位微调（px，负值上移）——只挪相位，不改间距
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

/** 横线间距来源：auto = 跟随排版的「字号 × 行距」，manual = 固定像素 */
export type LineSpacingMode = "auto" | "manual";

export interface EditorTheme {
  lineType: LineType;
  lineColor: string;
  /** 默认 auto：线距由文字行高推导，保证线始终落在每行文字下方 */
  lineSpacingMode: LineSpacingMode;
  lineSpacing: number;
  /** 自动模式的相位微调（px）：background-position 只挪相位，绝不改间距 */
  lineOffset: number;
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
  lineSpacingMode: "auto", // 关键默认：线距跟随文字行高，避免「线跟字越错越多」
  lineSpacing: 28,
  lineOffset: 0,
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

/** 横线/方格背景：返回可分别赋给 background-* 的图层参数
 *
 * 关键：不能把「位置 / 尺寸」简写（如 `linear-gradient(...) 0 0 / 28px 28px`）
 * 塞进 backgroundImage —— 该简写只在 background 属性里合法，
 * 赋给 background-image 会整条声明被浏览器丢弃（曾经的方格/点阵不显示就因此）。
 * 所以这里拆成 image + size + repeat 三段。
 */
export interface LineBackground {
  image: string;
  size: string;
  repeat: string;
  /** 自动模式的相位微调用；手动模式为 undefined（不需要） */
  position?: string;
}

/** 一行文字的「行框高度」：字号 × 行距，由 Editor.tsx 注入为 CSS 变量。
 *  用 CSS 变量而不是 JS 计算，排版一改线就跟着改，无需重新渲染。 */
const LINE_BOX = "var(--bd-line-box, 35px)";

export function buildLineBackground(t: EditorTheme): LineBackground | null {
  const manual = t.lineSpacingMode === "manual";
  const s = t.lineSpacing; // 手动模式的像素间距
  // 自动模式：间距 = 行框高度（与文字严格同周期）；线画在每个重复单元的底部，
  // 即「每行文字的基线下方」，所以字变大小、行距变松紧，线都贴在字下方合适距离。
  const periodY = manual ? `${s}px` : LINE_BOX;
  const position = manual ? undefined : `0 ${t.lineOffset || 0}px`;
  const c = t.lineColor;

  switch (t.lineType) {
    case "blank":
      return null;
    case "lined":
      // 自动模式用百分比停靠：底部 1px 实线，不受具体像素影响
      return {
        image: manual
          ? `linear-gradient(to bottom, transparent 0, transparent ${s - 1}px, ${c} ${s - 1}px, ${c} ${s}px, transparent ${s}px)`
          : `linear-gradient(to bottom, transparent 0 calc(100% - 1px), ${c} calc(100% - 1px) 100%)`,
        size: `100% ${periodY}`,
        repeat: "repeat",
        position,
      };
    case "grid":
      // 横线 + 竖线两层；单元格边长 = 行框高度（方形格），两层各自平铺
      return {
        image: manual
          ? `linear-gradient(to right, ${c} 1px, transparent 1px), linear-gradient(to bottom, transparent 0, transparent ${s - 1}px, ${c} ${s - 1}px, ${c} ${s}px, transparent ${s}px)`
          : `linear-gradient(to right, ${c} 0 1px, transparent 1px 100%), linear-gradient(to bottom, transparent 0 calc(100% - 1px), ${c} calc(100% - 1px) 100%)`,
        size: manual ? `${s}px ${s}px, ${s}px ${s}px` : `${LINE_BOX} ${LINE_BOX}, 100% ${LINE_BOX}`,
        repeat: "repeat, repeat",
        position,
      };
    case "dotted":
      // 点落在每行文字下方（自动模式取底部）
      return {
        image: manual
          ? `radial-gradient(circle, ${c} 1px, transparent 1.5px)`
          : `radial-gradient(circle at 50% calc(100% - 1px), ${c} 1px, transparent 1.5px)`,
        size: manual ? `${s}px ${s}px` : `100% ${LINE_BOX}`,
        repeat: "repeat",
        position,
      };
    case "cross": {
      // 田字格：用 viewBox + preserveAspectRatio=none 让格子随行高伸缩，
      // 线条用 vector-effect=non-scaling-stroke 保持 1px（否则拉伸会变粗变虚）。
      const svg = manual
        ? `<svg xmlns='http://www.w3.org/2000/svg' width='${s}' height='${s}' viewBox='0 0 ${s} ${s}'>` +
          `<line x1='0' y1='0' x2='${s}' y2='0' stroke='${c}' stroke-width='1'/>` +
          `<line x1='0' y1='${s}' x2='${s}' y2='${s}' stroke='${c}' stroke-width='1'/>` +
          `<line x1='0' y1='0' x2='0' y2='${s}' stroke='${c}' stroke-width='1'/>` +
          `<line x1='${s}' y1='0' x2='${s}' y2='${s}' stroke='${c}' stroke-width='1'/>` +
          `<line x1='${s / 2}' y1='0' x2='${s / 2}' y2='${s}' stroke='${c}' stroke-width='0.5' stroke-dasharray='2 2'/>` +
          `<line x1='0' y1='${s / 2}' x2='${s}' y2='${s / 2}' stroke='${c}' stroke-width='0.5' stroke-dasharray='2 2'/></svg>`
        : `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100' preserveAspectRatio='none'>` +
          `<line x1='0' y1='99.5' x2='100' y2='99.5' stroke='${c}' stroke-width='1' vector-effect='non-scaling-stroke'/>` +
          `<line x1='0' y1='0' x2='100' y2='0' stroke='${c}' stroke-width='1' vector-effect='non-scaling-stroke'/>` +
          `<line x1='0.5' y1='0' x2='0.5' y2='100' stroke='${c}' stroke-width='1' vector-effect='non-scaling-stroke'/>` +
          `<line x1='99.5' y1='0' x2='99.5' y2='100' stroke='${c}' stroke-width='1' vector-effect='non-scaling-stroke'/>` +
          `<line x1='50' y1='0' x2='50' y2='100' stroke='${c}' stroke-width='0.5' stroke-dasharray='2 2' vector-effect='non-scaling-stroke'/>` +
          `<line x1='0' y1='50' x2='100' y2='50' stroke='${c}' stroke-width='0.5' stroke-dasharray='2 2' vector-effect='non-scaling-stroke'/></svg>`;
      return {
        image: `url("data:image/svg+xml;utf8,${encodeURIComponent(svg)}")`,
        size: manual ? `${s}px ${s}px` : `${LINE_BOX} ${LINE_BOX}`,
        repeat: "repeat",
        position,
      };
    }
    default:
      return null;
  }
}

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
