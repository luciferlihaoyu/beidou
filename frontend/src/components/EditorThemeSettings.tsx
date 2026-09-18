/** 写作区主题设置：4 类横线 + 8 个预设配色 + 自定义背景色 + 上传背景图 + 透明度/模糊度。
 *
 * 触发：父级 useGlobalShortcuts 监听或工具栏按钮调 onOpen
 * 持久化：localStorage beidou:editor-theme（全局共享）
 *
 * 实时预览：onChange 每改一项立即回调（写回 store + 重渲染）
 */

import { useRef } from "react";
import { Palette, RotateCcw, Upload, X } from "lucide-react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import {
  type EditorTheme,
  type LineType,
  type BgImageFit,
  DEFAULT_THEME,
  PRESETS,
  LINE_TYPE_LABELS,
  FIT_LABELS,
  buildLineBackground,
  saveTheme,
  GRID_MODE_LABELS,
  GRID_MIN,
  GRID_MAX,
  inPaperMode,
  isGridLine,
  paperMetrics,
  type GridMode,
} from "@/lib/editorTheme";

interface Props {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  theme: EditorTheme;
  onChange: (t: EditorTheme) => void;
  /** 真实排版变量（--bd-font-size / --bd-line-height / --bd-line-box）。
   *  不传则预览走兜底 35px，与正文区不一致——「预览看着对、正文不对」就是这么来的。 */
  previewVars?: Record<string, string>;
}

const MAX_IMG_BYTES = 4 * 1024 * 1024; // 4MB base64

export default function EditorThemeSettings({
  open,
  onOpenChange,
  theme,
  onChange,
  previewVars,
}: Props) {
  const paper = inPaperMode(theme);
  const gridLine = isGridLine(theme.lineType);
  const metrics = paperMetrics(theme.gridSize);
  const fileRef = useRef<HTMLInputElement | null>(null);

  function patch(p: Partial<EditorTheme>) {
    const next = { ...theme, ...p };
    onChange(next);
    saveTheme(next);
  }

  function reset() {
    onChange(DEFAULT_THEME);
    saveTheme(DEFAULT_THEME);
    toast.success("已重置为经典白");
  }

  function onFile(file: File) {
    if (file.size > MAX_IMG_BYTES) {
      toast.error(`图片过大（${Math.round(file.size / 1024)}KB），最大 4MB`);
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = String(reader.result || "");
      if (!dataUrl.startsWith("data:image/")) {
        toast.error("仅支持图片文件");
        return;
      }
      patch({ bgImage: dataUrl, bgImageOpacity: 0.5, bgImageBlur: 0 });
      toast.success("已上传背景图");
    };
    reader.onerror = () => toast.error("读取失败");
    reader.readAsDataURL(file);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Palette className="h-4 w-4" />
            写作区主题
          </DialogTitle>
          <DialogDescription>
            选横线纸风格、配色，或上传自己的背景图（透明 + 模糊可调）
          </DialogDescription>
        </DialogHeader>

        {/* 预设 4 列网格 */}
        <section>
          <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            预设主题
          </h3>
          <div className="grid grid-cols-4 gap-2">
            {PRESETS.map((p) => {
              const isOn = theme.preset === p.id;
              const previewLine = buildLineBackground({
                ...DEFAULT_THEME,
                lineType: p.lineType,
                lineColor: p.lineColor,
                lineSpacing: 8,
                bgColor: p.bgColor,
              });
              const previewStyle: React.CSSProperties = {
                backgroundColor: p.bgColor,
                backgroundImage: previewLine?.image,
                backgroundSize: previewLine?.size,
                backgroundRepeat: previewLine?.repeat,
                backgroundPosition: previewLine?.position,
                // 小色块没有正文内边距：把相位补偿归零，否则方格纸/田字格预设块会被推成空白
                "--bd-pad-x": "0px",
                "--bd-pad-y": "0px",
              } as React.CSSProperties;
              return (
                <button
                  key={p.id}
                  onClick={() => {
                    patch({
                      preset: p.id,
                      lineType: p.lineType,
                      lineColor: p.lineColor,
                      bgColor: p.bgColor,
                    });
                  }}
                  className={
                    "group rounded-md border p-1 transition-colors " +
                    (isOn ? "border-primary ring-2 ring-primary/30" : "border-border hover:border-primary/40")
                  }
                >
                  <div
                    className="h-12 w-full rounded"
                    style={{ ...previewStyle, ...(previewVars as React.CSSProperties) }}
                  />
                  <div className="mt-1 text-center text-[11px]">{p.label}</div>
                </button>
              );
            })}
          </div>
        </section>

        {/* 横线类型 */}
        <section>
          <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            横线类型
          </h3>
          <div className="flex flex-wrap gap-1.5">
            {(Object.keys(LINE_TYPE_LABELS) as LineType[]).map((k) => (
              <button
                key={k}
                onClick={() => patch({ lineType: k, preset: "custom" })}
                className={
                  "rounded-md border px-3 py-1 text-xs transition-colors " +
                  (theme.lineType === k
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border text-muted-foreground hover:border-primary/40")
                }
              >
                {LINE_TYPE_LABELS[k]}
              </button>
            ))}
          </div>
          {gridLine && (
            <div className="mt-3 rounded-md border border-border bg-muted/30 p-2.5">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-[11px] text-muted-foreground">格子对齐</span>
                <div className="inline-flex overflow-hidden rounded-md border border-border text-xs">
                  {(
                    [
                      ["paper", GRID_MODE_LABELS.paper],
                      ["flow", GRID_MODE_LABELS.flow],
                    ] as [GridMode, string][]
                  ).map(([mode, label]) => (
                    <button
                      key={mode}
                      className={
                        "px-2 py-0.5 transition-colors " +
                        (theme.gridMode === mode
                          ? "bg-primary/10 font-medium text-primary"
                          : "text-muted-foreground hover:bg-muted")
                      }
                      onClick={() => patch({ gridMode: mode })}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              {paper ? (
                <>
                  <p className="mt-2 text-[11px] leading-5 text-muted-foreground">
                    正方形格、<span className="text-foreground">一字一格</span>：格边长同时决定字号与行高
                    （当前 字号 {metrics.fontSize}px · 行距 1.0），段间距自动归零——真稿纸没有段间距，
                    这样每一行都严格落在格上。改格边长即整体缩放，竖线永远卡在字与字之间。
                    <span className="text-muted-foreground">此时的字号/行距档位被接管，切回「跟随文字」即还原。</span>
                  </p>
                  <Label className="mt-2 block text-xs">格边长（{theme.gridSize}px）</Label>
                  <input
                    type="range"
                    min={GRID_MIN}
                    max={GRID_MAX}
                    value={theme.gridSize}
                    onChange={(e) => patch({ gridSize: Number(e.target.value) })}
                    className="mt-1 w-full"
                  />
                </>
              ) : (
                <p className="mt-2 text-[11px] leading-5 text-muted-foreground">
                  格宽 = 一个字的宽度、格高 = 行框高度：<span className="text-foreground">竖线对字、横线对行</span>，
                  保留你自己的字号与行距（格子是长方形，不是正方形稿纸）。
                </p>
              )}
            </div>
          )}
        </section>

        {/* 横线颜色 / 间距 / 背景色 */}
        <section className="space-y-3">
          <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            颜色与间距
          </h3>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label className="text-xs">横线颜色</Label>
              <div className="mt-1 flex items-center gap-2">
                <input
                  type="color"
                  value={theme.lineColor}
                  onChange={(e) => patch({ lineColor: e.target.value, preset: "custom" })}
                  className="h-8 w-12 cursor-pointer rounded border border-border"
                />
                <Input
                  value={theme.lineColor}
                  onChange={(e) => patch({ lineColor: e.target.value, preset: "custom" })}
                  className="h-8 text-xs"
                />
              </div>
            </div>
            <div>
              <div className="flex items-center justify-between">
                <Label className="text-xs">线距</Label>
                <div className="flex rounded-md border border-border text-[11px]">
                  {(
                    [
                      ["auto", "跟随文字"],
                      ["manual", "固定像素"],
                    ] as const
                  ).map(([mode, label]) => (
                    <button
                      key={mode}
                      className={
                        "px-2 py-0.5 transition-colors " +
                        ((theme.lineSpacingMode ?? "auto") === mode
                          ? "bg-primary/10 font-medium text-primary"
                          : "text-muted-foreground hover:bg-muted")
                      }
                      onClick={() => patch({ lineSpacingMode: mode })}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              {(theme.lineSpacingMode ?? "auto") === "auto" ? (
                <div className="mt-2">
                  <p className="text-[11px] leading-5 text-muted-foreground">
                    横线按「字号 × 行距」自动平铺，始终落在每行文字下方；竖线（方格/田字格）按
                    <span className="text-foreground">一个字宽</span>平铺，永远卡在字与字之间。线还会自动补偿
                    正文内边距，改字号/行距/页宽即跟着变——下面的微调只是「再挪半像素」的细活。
                  </p>
                  <Label className="mt-2 block text-xs">
                    线位微调（{theme.lineOffset ?? 0}px，只挪相位不改间距）
                  </Label>
                  <input
                    type="range"
                    min={-8}
                    max={8}
                    value={theme.lineOffset ?? 0}
                    onChange={(e) => patch({ lineOffset: Number(e.target.value) })}
                    className="mt-1 w-full"
                  />
                </div>
              ) : (
                <div className="mt-2">
                  <p className="text-[11px] leading-5 text-amber-600 dark:text-amber-400">
                    固定像素与文字行高无关，字号或行距一变就会与文字错位。
                  </p>
                  <Label className="mt-2 block text-xs">行距（{theme.lineSpacing}px）</Label>
                  <input
                    type="range"
                    min={16}
                    max={48}
                    value={theme.lineSpacing}
                    onChange={(e) => patch({ lineSpacing: Number(e.target.value) })}
                    className="mt-1 w-full"
                  />
                </div>
              )}
            </div>
            <div className="col-span-2">
              <Label className="text-xs">背景色</Label>
              <div className="mt-1 flex items-center gap-2">
                <input
                  type="color"
                  value={theme.bgColor}
                  onChange={(e) => patch({ bgColor: e.target.value, preset: "custom" })}
                  className="h-8 w-12 cursor-pointer rounded border border-border"
                />
                <Input
                  value={theme.bgColor}
                  onChange={(e) => patch({ bgColor: e.target.value, preset: "custom" })}
                  className="h-8 text-xs"
                />
              </div>
            </div>
          </div>
        </section>

        {/* 背景图 */}
        <section>
          <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            背景图
          </h3>
          <div className="space-y-3 rounded-md border border-dashed border-input p-3">
            {theme.bgImage ? (
              <div className="flex items-center gap-2">
                <div
                  className="h-16 w-24 shrink-0 rounded border border-border bg-cover bg-center"
                  style={{ backgroundImage: `url(${theme.bgImage})` }}
                />
                <div className="min-w-0 flex-1 text-xs text-muted-foreground">
                  背景图已设置
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  onClick={() => patch({ bgImage: null })}
                  title="移除背景图"
                >
                  <X className="h-3.5 w-3.5" />
                </Button>
              </div>
            ) : (
              <div className="text-xs text-muted-foreground">
                支持 JPG / PNG / WebP，最大 4MB。上传后会作为编辑器底层背景。
              </div>
            )}
            <div className="flex items-center gap-2">
              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) onFile(f);
                  if (fileRef.current) fileRef.current.value = "";
                }}
              />
              <Button
                size="sm"
                variant="outline"
                onClick={() => fileRef.current?.click()}
                className="gap-1"
              >
                <Upload className="h-3.5 w-3.5" />
                {theme.bgImage ? "替换图片" : "上传图片"}
              </Button>
              {theme.bgImage && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => patch({ bgImage: null })}
                  className="gap-1 text-muted-foreground"
                >
                  <X className="h-3.5 w-3.5" />
                  移除
                </Button>
              )}
            </div>
            {theme.bgImage && (
              <div className="space-y-2">
                <div>
                  <Label className="text-xs">
                    透明度 {Math.round(theme.bgImageOpacity * 100)}%
                  </Label>
                  <input
                    type="range"
                    min={5}
                    max={100}
                    value={Math.round(theme.bgImageOpacity * 100)}
                    onChange={(e) =>
                      patch({ bgImageOpacity: Number(e.target.value) / 100 })
                    }
                    className="mt-1 w-full"
                  />
                </div>
                <div>
                  <Label className="text-xs">
                    模糊度 {theme.bgImageBlur}px
                  </Label>
                  <input
                    type="range"
                    min={0}
                    max={20}
                    value={theme.bgImageBlur}
                    onChange={(e) => patch({ bgImageBlur: Number(e.target.value) })}
                    className="mt-1 w-full"
                  />
                </div>
                <div>
                  <Label className="text-xs">填充方式</Label>
                  <div className="mt-1 flex gap-1.5">
                    {(Object.keys(FIT_LABELS) as BgImageFit[]).map((f) => (
                      <button
                        key={f}
                        onClick={() => patch({ bgImageFit: f })}
                        className={
                          "rounded-md border px-2.5 py-1 text-xs transition-colors " +
                          (theme.bgImageFit === f
                            ? "border-primary bg-primary/10 text-primary"
                            : "border-border text-muted-foreground hover:border-primary/40")
                        }
                      >
                        {FIT_LABELS[f]}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>
        </section>

        <div className="flex justify-end border-t border-border pt-3">
          <Button variant="ghost" size="sm" onClick={reset} className="gap-1">
            <RotateCcw className="h-3.5 w-3.5" />
            重置为经典白
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
