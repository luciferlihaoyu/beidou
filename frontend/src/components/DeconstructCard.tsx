/** AI 工厂 · 拆书学习卡片
 *
 * 粘贴参考书文本 → AI 拆出可复用的写法范式（世界观结构/力量体系/人物配置/
 * 节奏爽点/钩子手法/语言调性/避坑清单）→ 存到项目并**可逐项编辑** →
 * 生成立项草案与设定时自动注入（借结构，人物地名全部换新）。
 */

import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { BookOpen, Eraser, Loader2, Save, Sparkles, Wand2 } from "lucide-react";
import { aiFactoryApi, deconstructApi, type AiProject, type ReferenceNote } from "@/lib/api";
import { ReferenceFields, ReferenceUploader, trimForUpload } from "@/components/reference";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export default function DeconstructCard({
  project,
  onProjectChange,
}: {
  project: AiProject;
  onProjectChange: (p: AiProject) => void;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [hint, setHint] = useState("");
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [ref, setRef] = useState<ReferenceNote | null>(project.reference ?? null);

  const has = !!(ref && (ref.worldview_framework || ref.power_system || ref.pacing));

  // 项目被外部刷新（如他处保存/清空范式）时同步卡片显示；用引用比较避免覆盖用户正在编辑的内容
  const lastProjectRef = useRef<unknown>(project.reference);
  useEffect(() => {
    if (project.reference !== lastProjectRef.current) {
      lastProjectRef.current = project.reference;
      setRef(project.reference ?? null);
    }
  }, [project.reference]);

  /** 从待拆文本中精确撤回某文件贡献的正文（同步判定，供上传器决定是否移除 chip） */
  function removeChunk(chunk: string): boolean {
    if (!text.includes(chunk)) return false;
    setText(text.replace(chunk, "").replace(/\n{3,}/g, "\n\n").trim());
    return true;
  }

  async function runDeconstruct() {
    if (text.trim().length < 200) {
      toast.error("参考书文本太短——建议上传整本 txt 或粘前几万字（至少 200 字）");
      return;
    }
    setBusy(true);
    try {
      const r = await deconstructApi.run(trimForUpload(text), hint);
      setRef(r.reference);
      toast.success("拆书完成——检查并按需修改范式，保存后生效");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "拆书失败");
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!ref) return;
    setSaving(true);
    try {
      await deconstructApi.save(project.id, ref);
      // 保存后刷新项目（让 reference 进入项目状态）
      onProjectChange(await aiFactoryApi.get(project.id));
      toast.success("范式已保存——生成立项草案和设定时会自动注入");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function clear() {
    if (!confirm("清空范式笔记？此后立项/设定不再注入参考范式。")) return;
    try {
      await deconstructApi.clear(project.id);
      setRef(null);
      onProjectChange(await aiFactoryApi.get(project.id));
      toast.success("已清空范式笔记");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "清空失败");
    }
  }

  return (
    <div className="mb-5 rounded-lg border border-border bg-card p-3">
      <button className="flex w-full items-center justify-between" onClick={() => setOpen((v) => !v)}>
        <span className="flex items-center gap-2 text-sm font-medium">
          <BookOpen className="h-4 w-4 text-primary" />
          拆书学习{has ? " ✓" : ""}
        </span>
        <span className="text-[11px] text-muted-foreground">
          {has ? "已挂载范式（生成立项/设定时注入）" : "粘贴参考书，学它的写法套路写新书"}
          <span className="ml-1">{open ? "收起 ▲" : "展开 ▼"}</span>
        </span>
      </button>

      {open && (
        <div className="mt-3 space-y-3">
          {!ref && (
            <>
              <p className="text-[11px] leading-5 text-muted-foreground">
                上传或粘贴一本你欣赏的书的正文（txt 文件直接拖进来，建议整本或前几万字）。AI 会提炼出
                <span className="text-foreground">可迁移的结构</span>：世界观框架、力量体系、人物配置套路、
                节奏爽点、钩子手法、语言调性，以及<span className="text-foreground">避坑清单</span>
                （这本书已用烂的桥段）。生成立项草案和设定时会自动注入——借结构与节奏，
                世界观名词/人名/地名全部重新原创，你再逐项改。
              </p>
              <Input
                className="h-8 text-sm"
                placeholder="参考书书名或题材标签（可选，如：斗破苍穹 类玄幻）"
                value={hint}
                onChange={(e) => setHint(e.target.value)}
              />

              <ReferenceUploader
                onAdd={(chunk) => setText((prev) => (prev ? `${prev}\n\n${chunk}` : chunk))}
                onRemove={removeChunk}
                onClearAll={() => setText("")}
              />

              <textarea
                className="min-h-32 w-full rounded-md border border-border bg-transparent px-3 py-2 font-content text-xs leading-6"
                placeholder="或者直接在此粘贴参考书正文…"
                value={text}
                onChange={(e) => setText(e.target.value)}
              />
              <div className="flex items-center justify-between">
                <span className="text-[11px] text-muted-foreground">
                  已备 {text.length.toLocaleString()} 字
                  {text.length > 360_000 && `（拆解时自动采样 ${(360_000).toLocaleString()} 字：首尾兼顾）`}
                </span>
                <Button size="sm" disabled={busy} onClick={() => void runDeconstruct()}>
                  {busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Wand2 className="mr-1 h-4 w-4" />}
                  {busy ? "拆解中…" : "开始拆书"}
                </Button>
              </div>
            </>
          )}

          {ref && (
            <>
              <ReferenceFields value={ref} onChange={setRef} />

              <div className="flex items-center justify-between border-t border-border pt-2">
                <button className="text-[11px] text-muted-foreground hover:text-destructive" onClick={() => void clear()}>
                  <Eraser className="mr-1 inline h-3 w-3" />
                  清空范式
                </button>
                <div className="flex gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 text-xs"
                    title="清空范式与已读入的参考文本，重新开始"
                    onClick={() => {
                      setRef(null);
                      setText("");
                      setHint("");
                    }}
                  >
                    <Sparkles className="mr-1 h-3 w-3" />
                    重新拆一本
                  </Button>
                  <Button size="sm" className="h-7 text-xs" disabled={saving} onClick={() => void save()}>
                    {saving ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Save className="mr-1 h-3 w-3" />}
                    保存范式
                  </Button>
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
