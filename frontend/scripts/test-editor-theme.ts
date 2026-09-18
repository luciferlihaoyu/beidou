/** editorTheme 纯函数测试：横线/方格随排版自适应（node 直接跑 TS，零依赖）
 *
 * 背景：横线间距曾是一个手工像素值（默认 28px），而文字行高是「字号 × 行距」
 * （约 35px）——两者无关，线必然与文字错位，且越错的越多。现在自动模式用
 * CSS 变量 var(--bd-line-box) 做周期，并让线落在每个行框的底部。
 *
 * 运行：npm run test:theme
 */

import { readFileSync } from "node:fs";
import {
  DEFAULT_THEME,
  buildLineBackground,
  inPaperMode,
  isGridLine,
  paperMetrics,
  type EditorTheme,
  type LineType,
} from "../src/lib/editorTheme.ts";

const LINE_BOX = "var(--bd-line-box";
const LINE_BOX_FULL = "var(--bd-line-box, 35px)";

let pass = 0;
let fail = 0;

function ok(name: string, cond: boolean, extra = "") {
  if (cond) {
    pass++;
    console.log(`  ✓ ${name}`);
  } else {
    fail++;
    console.log(`  ✗ ${name}${extra ? " → " + extra : ""}`);
  }
}

const ALL: LineType[] = ["blank", "lined", "grid", "dotted", "cross"];
const t = (patch: Partial<EditorTheme> = {}): EditorTheme => ({ ...DEFAULT_THEME, ...patch });

console.log("自动模式·跟随文字：周期必须跟随文字行框（方格/田字格用 flow 变体）");
for (const type of ALL) {
  const gridish = type === "grid" || type === "cross";
  // 方格/田字格默认是「稿纸格」（格边长驱动，见下方专段）；这里专门检验「跟随文字」变体
  const bg = buildLineBackground(
    t(gridish ? { lineType: type, gridMode: "flow" } : { lineType: type })
  );
  if (type === "blank") {
    ok("blank 不画线", bg === null);
    continue;
  }
  ok(`${type} 横线周期跟随行框`, !!bg && bg.size.includes(LINE_BOX), bg?.size);
  ok(
    `${type} 尺寸里不含固定像素周期`,
    !!bg && !/\b\d+px (\+|\/|,)/.test(bg.size.replace(/var\([^)]*\)/g, "")),
    bg?.size
  );
  if (gridish) {
    // 竖线层必须是「一个字宽」——旧 bug 是竖线跟着行高 → 一格两字、竖线穿字
    ok(`${type} 竖线层为一个字宽`, !!bg && bg.size.startsWith("var(--bd-font-size"), bg?.size);
    ok(`${type} 竖线周期不再等于行框高`, !!bg && !bg.size.startsWith(LINE_BOX), bg?.size);
  }
  ok(`${type} 有 image/size/repeat`, !!bg && !!bg.image && !!bg.size && !!bg.repeat);
}

console.log("\n自动模式：线要落在行框底部（否则会压在字上或被顶到字顶）");
{
  const lined = buildLineBackground(t({ lineType: "lined" }))!;
  ok("lined 用 calc(100% - 1px) 贴底", lined.image.includes("calc(100% - 1px)"), lined.image);
  ok("lined 尺寸为 100% × 行框", lined.size === `100% ${LINE_BOX_FULL}`, lined.size);

  const grid = buildLineBackground(t({ lineType: "grid", gridMode: "flow" }))!;
  ok(
    "grid 横线同样贴底",
    grid.image.includes("calc(100% - 1px)"),
    grid.image
  );
  ok("grid 竖线为 1px 且写在前面（横向平铺层）", grid.image.indexOf("to right") < grid.image.indexOf("to bottom"));
  // 注意：var(--bd-line-box, 35px) 自带逗号，不能用 split(",") 数层数
  ok("grid 第二层为整行宽（100% × 行框）", grid.size.includes(`100% ${LINE_BOX_FULL}`), grid.size);
  // 注意：var(--bd-line-box, 35px) 自带逗号，split(", ") 拆不出层数——用后缀判断
  ok("grid 横线层用行框高度", grid.size.endsWith(`100% ${LINE_BOX_FULL}`), grid.size);
  ok("grid 竖线层用字宽", grid.size.startsWith("var(--bd-font-size"), grid.size);

  const dotted = buildLineBackground(t({ lineType: "dotted" }))!;
  ok("dotted 圆点在底部", dotted.image.includes("circle at 50% calc(100% - 1px)"), dotted.image);

  const cross = buildLineBackground(t({ lineType: "cross" }))!;
  const svg = decodeURIComponent(cross.image.replace(/^url\("data:image\/svg\+xml;utf8,/, "").replace(/"\)$/, ""));
  ok("cross 用 viewBox 缩放", svg.includes("viewBox='0 0 100 100'"), svg.slice(0, 80));
  ok("cross preserveAspectRatio=none（随行高伸缩）", svg.includes("preserveAspectRatio='none'"));
  ok(
    "cross 线条 non-scaling-stroke（拉伸不变粗）",
    (svg.match(/non-scaling-stroke/g) || []).length === 6,
    String((svg.match(/non-scaling-stroke/g) || []).length)
  );
}

console.log("\n线位微调：只挪相位，绝不改周期（改周期就又会与文字错位）");
{
  const base = buildLineBackground(t({ lineType: "lined" }))!;
  const shifted = buildLineBackground(t({ lineType: "lined", lineOffset: -4 }))!;
  // 相位必须补偿「正文内边距」（背景画在外层容器、文字被 padding 推开）：
  // 旧行为是 `0 0px`，等于让线整体偏移一个内边距——横线糊在字顶、竖线整体平移。
  ok(
    "相位补偿内边距（x = 左内边距）",
    !!base.position && base.position.startsWith("var(--bd-pad-x"),
    base.position
  );
  ok(
    "相位补偿内边距（y = 上内边距）",
    !!base.position && base.position.includes("var(--bd-pad-y"),
    base.position
  );
  ok(
    "offset=-4 时在补偿基础上微调",
    !!shifted.position && shifted.position.includes("calc(var(--bd-pad-y, 2rem) + -4px)"),
    shifted.position
  );
  ok("offset 不改变基线补偿", !!shifted.position && shifted.position.startsWith("var(--bd-pad-x"));
  ok("微调不改 size（周期不变）", base.size === shifted.size, `${base.size} vs ${shifted.size}`);
  ok("微调不改 image", base.image === shifted.image);
  // 稿纸格不需要相位微调（格边长即排版），但仍要补偿内边距
  const gridPaper = buildLineBackground(t({ lineType: "grid", lineOffset: 3 }))!;
  ok(
    "稿纸格相位只补内边距、不吃微调",
    gridPaper.position === "var(--bd-pad-x, 2.5rem) var(--bd-pad-y, 2rem)",
    String(gridPaper.position)
  );
  const gridFlow = buildLineBackground(t({ lineType: "grid", gridMode: "flow", lineOffset: 3 }))!;
  ok(
    "跟随文字模式相位含微调",
    !!gridFlow.position && gridFlow.position.includes("+ 3px"),
    String(gridFlow.position)
  );
}

console.log("\n手动模式：保留原有固定像素行为（老用户设置不失效）");
{
  const lined = buildLineBackground(t({ lineType: "lined", lineSpacingMode: "manual", lineSpacing: 28 }))!;
  ok("lined 28px 周期", lined.size === "100% 28px", lined.size);
  ok("lined 线画在 27-28px 处", lined.image.includes("transparent 27px, #e5e7eb 27px, #e5e7eb 28px"), lined.image);
  ok("手动模式不带 position（无需相位）", lined.position === undefined);

  const grid = buildLineBackground(t({ lineType: "grid", lineSpacingMode: "manual", lineSpacing: 30 }))!;
  ok("grid 双 30px 周期", grid.size === "30px 30px, 30px 30px", grid.size);

  const cross = buildLineBackground(t({ lineType: "cross", lineSpacingMode: "manual", lineSpacing: 24 }))!;
  // SVG 是 URL 编码的，必须先解码再断言
  const crossSvg = decodeURIComponent(cross.image);
  ok(
    "cross 手动模式用固定像素 svg",
    crossSvg.includes("width='24'") && crossSvg.includes("height='24'"),
    crossSvg.slice(0, 90)
  );
}

console.log("\n向后兼容：老配置没有 lineSpacingMode 字段时必须按自动模式处理");
{
  const legacy = { ...DEFAULT_THEME, lineType: "lined" as LineType };
  delete (legacy as Partial<EditorTheme>).lineSpacingMode;
  const bg = buildLineBackground(legacy as EditorTheme)!;
  ok("缺字段 → 自动模式（用行框）", bg.size.includes(LINE_BOX), bg.size);
}

console.log("\n格子对齐：稿纸格（一字一格）vs 跟随文字（竖线对字宽）");
{
  // 稿纸格：正方形格，tile = 格边长 px（两层都用字面格边长，不依赖 --bd-line-box 同步）
  for (const size of [16, 24, 40]) {
    const bg = buildLineBackground(t({ lineType: "grid", gridMode: "paper", gridSize: size }))!;
    ok(`稿纸格 ${size}px → 正方形 tile`, bg.size.startsWith(`${size}px ${size}px`), bg.size);
    ok(`稿纸格 ${size}px → 横线层同为格边长`, bg.size.endsWith(`100% ${size}px`), bg.size);
  }
  // 跟随文字：竖线每「一个字宽」（var(--bd-font-size)），横线每行框
  const flow = buildLineBackground(t({ lineType: "grid", gridMode: "flow" }))!;
  ok("跟随文字竖线层 = 字宽 × 行框", flow.size.startsWith("var(--bd-font-size, 17px) var(--bd-line-box"), flow.size);
  ok("跟随文字横线层 = 整行宽 × 行框", flow.size.includes(`100% ${LINE_BOX_FULL}`), flow.size);
  const crossFlow = buildLineBackground(t({ lineType: "cross", gridMode: "flow" }))!;
  ok("田字格跟随文字 = 字宽 × 行框", crossFlow.size.startsWith("var(--bd-font-size, 17px) var(--bd-line-box"), crossFlow.size);

  // 字号/格边长必须有边界钳制，否则误存脏值会让格子变成 NaN 或 0
  ok("格边长下限钳制", buildLineBackground(t({ lineType: "grid", gridSize: 1 }))!.size.startsWith("16px"), buildLineBackground(t({ lineType: "grid", gridSize: 1 }))!.size);
  ok("格边长上限钳制", buildLineBackground(t({ lineType: "grid", gridSize: 999 }))!.size.startsWith("40px"));
  ok("格边长缺失回退默认 24", buildLineBackground(t({ lineType: "grid", gridSize: 0 as number }))!.size.startsWith("24px"));

  // 稿纸模式的排版推导：字号 = 格边长、行高 = 1（行框 = 格边长）——这是「一字一格」的前提
  for (const size of [16, 24, 40]) {
    const m = paperMetrics(size);
    ok(`稿纸推导 ${size}px：字号=${size} 行高=1 行框=${size}`, m.fontSize === size && m.lineHeight === 1 && m.lineBox === size, JSON.stringify(m));
  }
  // 关键不变量：格边长 == 行框 ⇒ 每个方格正好一行字高（否则横线会歪）
  ok("稿纸格：行框 == 格边长（横线才对得上）", paperMetrics(28).lineBox === 28);
  ok("isGridLine：只有方格/田字格算格子", isGridLine("grid") && isGridLine("cross") && !isGridLine("lined") && !isGridLine("dotted") && !isGridLine("blank"));
  ok("inPaperMode：只有格子线型 + paper 才算", inPaperMode(t({ lineType: "grid", gridMode: "paper" })) && !inPaperMode(t({ lineType: "lined", gridMode: "paper" })) && !inPaperMode(t({ lineType: "grid", gridMode: "flow" })));
  // 横线/点线不受格子模式影响（竖线无关）
  ok("横线不受格子模式影响", buildLineBackground(t({ lineType: "lined", gridMode: "paper" }))!.size === buildLineBackground(t({ lineType: "lined", gridMode: "flow" }))!.size);
}

console.log("\n内边距同源：背景相位引用的变量必须与 .prose-beidou 的 padding 同源");
{
  // 这两个变量名是「线跟字走」的命门：.prose-beidou 的 padding 与背景相位都读它们。
  // 任一侧改名而另一侧没跟上，就会退化回「线整体偏移一个内边距」。
  const css = readFileSync(new URL("../src/index.css", import.meta.url), "utf8");
  ok("index.css 定义了 --bd-pad-x", css.includes("--bd-pad-x:"));
  ok("index.css 定义了 --bd-pad-y", css.includes("--bd-pad-y:"));
  ok("prose-beidou 的 padding 读同一组变量", /\.prose-beidou\s*\{[^}]*padding:\s*var\(--bd-pad-y/.test(css));
  const forLine = buildLineBackground(t({ lineType: "lined" }))!;
  ok("背景相位引用 --bd-pad-x", forLine.position!.includes("var(--bd-pad-x"));
  ok("背景相位引用 --bd-pad-y", forLine.position!.includes("var(--bd-pad-y"));
  // 稿纸格：行高由格边长接管，段间距必须归零（首段上外边距会破坏行行对齐）
  const editor = readFileSync(new URL("../src/pages/Editor.tsx", import.meta.url), "utf8");
  ok("稿纸格下段间距归零", /"--bd-para-spacing":\s*paperMode\s*\?\s*"0"/.test(editor));
  ok("稿纸格下字号被格边长接管", /const fontVar = paperMode \?/.test(editor));
}

console.log("\n线色：用户改颜色必须原样进 CSS");
{
  const bg = buildLineBackground(t({ lineType: "lined", lineColor: "#ff00aa" }))!;
  ok("颜色进入 image", bg.image.includes("#ff00aa"), bg.image);
  const svgCase = buildLineBackground(t({ lineType: "cross", lineColor: "#123456" }))!;
  ok("颜色进入 svg", decodeURIComponent(svgCase.image).includes("#123456"));
}

console.log(`\n${pass} passed, ${fail} failed`);
if (fail > 0) process.exit(1);
