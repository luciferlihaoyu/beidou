/** editorTheme 纯函数测试：横线/方格随排版自适应（node 直接跑 TS，零依赖）
 *
 * 背景：横线间距曾是一个手工像素值（默认 28px），而文字行高是「字号 × 行距」
 * （约 35px）——两者无关，线必然与文字错位，且越错的越多。现在自动模式用
 * CSS 变量 var(--bd-line-box) 做周期，并让线落在每个行框的底部。
 *
 * 运行：npm run test:theme
 */

import { DEFAULT_THEME, buildLineBackground, type EditorTheme, type LineType } from "../src/lib/editorTheme.ts";

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

console.log("自动模式（默认）：周期必须跟随文字行框");
for (const type of ALL) {
  const bg = buildLineBackground(t({ lineType: type }));
  if (type === "blank") {
    ok("blank 不画线", bg === null);
    continue;
  }
  ok(`${type} 使用 var(--bd-line-box) 作周期`, !!bg && bg.size.includes(LINE_BOX), bg?.size);
  ok(`${type} 尺寸里不含固定像素周期`, !!bg && !/\b\d+px (\+|\/|,)/.test(bg.size.replace(/var\([^)]*\)/g, "")), bg?.size);
  ok(`${type} 有 image/size/repeat`, !!bg && !!bg.image && !!bg.size && !!bg.repeat);
}

console.log("\n自动模式：线要落在行框底部（否则会压在字上或被顶到字顶）");
{
  const lined = buildLineBackground(t({ lineType: "lined" }))!;
  ok("lined 用 calc(100% - 1px) 贴底", lined.image.includes("calc(100% - 1px)"), lined.image);
  ok("lined 尺寸为 100% × 行框", lined.size === `100% ${LINE_BOX_FULL}`, lined.size);

  const grid = buildLineBackground(t({ lineType: "grid" }))!;
  ok(
    "grid 横线同样贴底",
    grid.image.includes("calc(100% - 1px)"),
    grid.image
  );
  ok("grid 竖线为 1px 且写在前面（横向平铺层）", grid.image.indexOf("to right") < grid.image.indexOf("to bottom"));
  // 注意：var(--bd-line-box, 35px) 自带逗号，不能用 split(",") 数层数
  ok("grid 第二层为整行宽（100% × 行框）", grid.size.includes(`100% ${LINE_BOX_FULL}`), grid.size);
  ok("grid 两层都用行框高度", (grid.size.match(/var\(--bd-line-box/g) || []).length === 3, grid.size);

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
  ok("offset=0 时 position 为 0 0", base.position === "0 0px", base.position);
  ok("offset=-4 时 position 为 0 -4px", shifted.position === "0 -4px", shifted.position);
  ok("微调不改 size（周期不变）", base.size === shifted.size, `${base.size} vs ${shifted.size}`);
  ok("微调不改 image", base.image === shifted.image);
  const gridShift = buildLineBackground(t({ lineType: "grid", lineOffset: 3 }))!;
  ok("grid 也带 position", gridShift.position === "0 3px", gridShift.position);
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

console.log("\n线色：用户改颜色必须原样进 CSS");
{
  const bg = buildLineBackground(t({ lineType: "lined", lineColor: "#ff00aa" }))!;
  ok("颜色进入 image", bg.image.includes("#ff00aa"), bg.image);
  const svgCase = buildLineBackground(t({ lineType: "cross", lineColor: "#123456" }))!;
  ok("颜色进入 svg", decodeURIComponent(svgCase.image).includes("#123456"));
}

console.log(`\n${pass} passed, ${fail} failed`);
if (fail > 0) process.exit(1);
