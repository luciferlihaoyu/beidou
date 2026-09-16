/** AI 工厂 · 拆书学习卡片
 *
 * 粘贴参考书文本 → AI 拆出可复用的写法范式（世界观结构/力量体系/人物配置/
 * 节奏爽点/钩子手法/语言调性/避坑清单）→ 存到项目并**可逐项编辑** →
 * 生成立项草案与设定时自动注入（借结构，人物地名全部换新）。
 */

import { useState } from "react";
import { toast } from "sonner";
import { BookOpen, Eraser, Loader2, Save, Sparkles, Wand2 } from "lucide-react";
import {
  REFERENCE_FIELDS,
  deconstructApi,
  type AiProject,
  type ReferenceNote,
} from "@/lib/api";
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

  function setField(key: keyof ReferenceNote, value: string, list = false) {
    setRef((prev) => {
      const next: ReferenceNote = { ...(prev ?? {}) };
      if (list) {
        next[key] = value.split("\n").map((s) => s.trim()).filter(Boolean) as never;
      } else {
        next[key] = value as never;
      }
      return next;
    });
  }

  async function runDeconstruct() {
    if (text.trim().length < 200) {
      toast.error("参考书文本太短——建议直接粘贴整本或前几万字（至少 200 字）");
      return;
    }
    setBusy(true);
    try {
      const r = await deconstructApi.run(text, hint);
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
      const { aiFactoryApi } = await import("@/lib/api");
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
      const { aiFactoryApi } = await import("@/lib/api");
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
                粘贴一本你欣赏的书的正文（txt 内容，建议整本或前几万字）。AI 会提炼出
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
              <textarea
                className="min-h-32 w-full rounded-md border border-border bg-transparent px-3 py-2 font-content text-xs leading-6"
                placeholder="在此粘贴参考书正文…"
                value={text}
                onChange={(e) => setText(e.target.value)}
              />
              <div className="flex items-center justify-between">
                <span className="text-[11px] text-muted-foreground">
                  已粘 {text.length.toLocaleString()} 字（超 30 万字自动截断）
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
              <div className="space-y-2">
                {REFERENCE_FIELDS.map((f) =>
                  f.list ? (
                    <div key={f.key}>
                      <label className="text-xs text-muted-foreground">
                        {f.label} <span className="text-muted-foreground/60">（{f.hint}）</span>
                      </label>
                      <textarea
                        className="mt-1 min-h-16 w-full rounded-md border border-border bg-transparent px-2.5 py-1.5 text-xs"
                        value={((ref[f.key] as string[]) ?? []).join("\n")}
                        onChange={(e) => setField(f.key, e.target.value, true)}
                      />
                    </div>
                  ) : (
                    <div key={f.key}>
                      <label className="text-xs text-muted-foreground">
                        {f.label} <span className="text-muted-foreground/60">（{f.hint}）</span>
                      </label>
                      <textarea
                        className="mt-1 min-h-14 w-full rounded-md border border-border bg-transparent px-2.5 py-1.5 text-xs"
                        value={((ref[f.key] as string) ?? "")}
                        onChange={(e) => setField(f.key, e.target.value)}
                      />
                    </div>
                  )
                )}

                {ref.character_config && ref.character_config.length > 0 && (
                  <div>
                    <label className="text-xs text-muted-foreground">人物配置范式（只读参考，设定生成时借鉴）</label>
                    <div className="mt-1 space-y-1">
                      {ref.character_config.map((c, i) => (
                        <p key={i} className="rounded border border-border px-2 py-1 text-xs">
                          <span className="text-primary">{c.role}</span>
                          <span className="mx-1 text-muted-foreground">·</span>
                          <span className="font-medium">{c.archetype}</span>
                          <span className="ml-1 text-muted-foreground">{c.traits}</span>
                        </p>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              <div className="flex items-center justify-between border-t border-border pt-2">
                <button className="text-[11px] text-muted-foreground hover:text-destructive" onClick={() => void clear()}>
                  <Eraser className="mr-1 inline h-3 w-3" />
                  清空范式
                </button>
                <div className="flex gap-2">
                  <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={() => setRef(null)}>
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
