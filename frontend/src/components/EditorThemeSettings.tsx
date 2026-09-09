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
} from "@/lib/editorTheme";

interface Props {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  theme: EditorTheme;
  onChange: (t: EditorTheme) => void;
}

const MAX_IMG_BYTES = 4 * 1024 * 1024; // 4MB base64

export default function EditorThemeSettings({ open, onOpenChange, theme, onChange }: Props) {
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
              const previewStyle: React.CSSProperties = {
                backgroundColor: p.bgColor,
                backgroundImage: buildLineBackground({
                  ...DEFAULT_THEME,
                  lineType: p.lineType,
                  lineColor: p.lineColor,
                  lineSpacing: 8,
                  bgColor: p.bgColor,
                }),
                backgroundSize:
                  p.lineType === "lined" || p.lineType === "blank" ? "100% 8px" : undefined,
              };
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
                  <div className="h-12 w-full rounded" style={previewStyle} />
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
              <Label className="text-xs">行距（{theme.lineSpacing}px）</Label>
              <input
                type="range"
                min={16}
                max={48}
                value={theme.lineSpacing}
                onChange={(e) => patch({ lineSpacing: Number(e.target.value) })}
                className="mt-2 w-full"
              />
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
