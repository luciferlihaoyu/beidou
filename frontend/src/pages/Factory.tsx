/** AI 工厂 · 项目列表页（/factory）
 *
 * 与人工写作（书架）平行的第二体系：从立项到正文的自动化流水线。
 * M1：列表 + 新建（一句话创意 + 可选字数目标）。
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { BookOpen, Bot, FileUp, Loader2, Plus, Trash2, Wand2 } from "lucide-react";
import { toast } from "sonner";
import AppShell from "@/components/AppShell";
import ImportDialog from "@/components/ImportDialog";
import { aiFactoryApi, deconstructApi, type AiProject, type AiProjectCreate, type ReferenceNote } from "@/lib/api";
import { ReferenceFields, ReferenceUploader, trimForUpload } from "@/components/reference";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

/** 拆书创建路径的本地暂存键（项目创建前没有服务端归属，先落本地） */
const FACTORY_BOOK_KEY = "beidou:factory-book-draft";

const STATUS_LABEL: Record<string, string> = {
  draft: "立项中",
  setup: "待设定",
  outline: "待大纲",
  writing: "生成中",
  reviewing: "审校中",
  done: "已完成",
  failed: "失败",
};
const STATUS_COLOR: Record<string, string> = {
  draft: "bg-muted text-muted-foreground",
  setup: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
  outline: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
  writing: "bg-primary/10 text-primary",
  reviewing: "bg-primary/10 text-primary",
  done: "bg-green-500/10 text-green-600 dark:text-green-400",
  failed: "bg-destructive/10 text-destructive",
};

export default function Factory() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<AiProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);
  const [form, setForm] = useState<AiProjectCreate>({ seed_prompt: "" });
  const [showTargets, setShowTargets] = useState(false);
  const [busy, setBusy] = useState(false);
  // 创建路径：idea = 一句话创意；book = 先拆书学范式。
  // 拆书草稿在项目创建前不存在服务端归属，先落本地（localStorage），
  // 创建成功即转存到项目并清除——刷新/误关页面都不丢。
  const [mode, setMode] = useState<"idea" | "book">("idea");
  const [refText, setRefText] = useState(() => {
    try {
      return localStorage.getItem(FACTORY_BOOK_KEY + ":text") ?? "";
    } catch {
      return "";
    }
  });
  const [refNote, setRefNote] = useState<ReferenceNote | null>(() => {
    try {
      const raw = localStorage.getItem(FACTORY_BOOK_KEY + ":note");
      return raw ? (JSON.parse(raw) as ReferenceNote) : null;
    } catch {
      return null;
    }
  });
  const [deconstructing, setDeconstructing] = useState(false);

  useEffect(() => {
    try {
      if (refText) localStorage.setItem(FACTORY_BOOK_KEY + ":text", refText);
      else localStorage.removeItem(FACTORY_BOOK_KEY + ":text");
    } catch {
      /* 隐私模式等场景静默失败，不阻断创建 */
    }
  }, [refText]);

  useEffect(() => {
    try {
      if (refNote) localStorage.setItem(FACTORY_BOOK_KEY + ":note", JSON.stringify(refNote));
      else localStorage.removeItem(FACTORY_BOOK_KEY + ":note");
    } catch {
      /* 同上 */
    }
  }, [refNote]);

  async function runDeconstruct() {
    if (refText.trim().length < 200) {
      toast.error("参考书文本太短——请上传整本 txt，或至少粘贴 200 字");
      return;
    }
    setDeconstructing(true);
    try {
      const r = await deconstructApi.run(trimForUpload(refText), "");
      setRefNote(r.reference);
      toast.success("拆书完成——检查并按需修改范式，创建后即刻生效");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "拆书失败");
    } finally {
      setDeconstructing(false);
    }
  }

  function load() {
    aiFactoryApi
      .list()
      .then(setProjects)
      .catch((e) => toast.error(e.message))
      .finally(() => setLoading(false));
  }
  useEffect(load, []);

  async function create() {
    let seed = form.seed_prompt.trim();
    if (seed.length < 4) {
      if (mode === "book" && refNote) {
        // 拆书路径：没写创意时按范式派生一个起点
        const g = form.genre?.trim() || "小说";
        seed = `参考${refNote.title ? `《${refNote.title}》` : "已拆解"}的写法范式，写一本全新的${g}`;
      } else {
        toast.error("创意至少 4 个字");
        return;
      }
    }
    setBusy(true);
    try {
      const p = await aiFactoryApi.create({ ...form, seed_prompt: seed });
      // 拆书路径：把范式挂到新项目上（立项/设定生成时自动注入），并带过去原文
      if (mode === "book" && refNote) {
        try {
          await deconstructApi.save(p.id, refNote);
          await deconstructApi
            .saveDraft(p.id, { text: trimForUpload(refText), hint: "", note: null })
            .catch(() => {});
        } catch {
          toast.warning("范式保存失败，可进入项目后在「拆书学习」里重新保存");
        }
      }
      // 已转存到项目，清掉本地暂存
      try {
        localStorage.removeItem(FACTORY_BOOK_KEY + ":text");
        localStorage.removeItem(FACTORY_BOOK_KEY + ":note");
      } catch {
        /* 清不掉也无妨 */
      }
      toast.success(mode === "book" && refNote ? "项目已创建并挂载范式，开始立项吧" : "项目已创建，开始立项吧");
      navigate(`/factory/${p.id}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "创建失败");
    } finally {
      setBusy(false);
    }
  }

  async function remove(p: AiProject) {
    const keepNovel = window.confirm(
      `删除项目「${p.novel_title ?? p.seed_prompt.slice(0, 20)}」？\n\n确定 = 项目删除但保留小说（转人工书）\n取消 = 再想想`
    );
    if (!keepNovel) return;
    try {
      await aiFactoryApi.remove(p.id, false);
      toast.success("已删除（小说已保留）");
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "删除失败");
    }
  }

  const numInput = (v: string) => {
    const n = Number(v);
    return v === "" || !Number.isFinite(n) || n <= 0 ? null : Math.floor(n);
  };

  return (
    <AppShell
      title={
        <span className="flex items-center gap-2">
          <Bot className="h-4 w-4 text-primary" />
          AI 工厂
        </span>
      }
      actions={
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" className="h-8" onClick={() => setImporting(true)}>
            <FileUp className="mr-1 h-4 w-4" />
            导入续写
          </Button>
          <Button size="sm" className="h-8" onClick={() => setCreating(true)}>
            <Plus className="mr-1 h-4 w-4" />
            新建项目
          </Button>
        </div>
      }
    >
      <div className="mx-auto w-full max-w-4xl p-6">
        <p className="mb-5 text-xs leading-5 text-muted-foreground">
          AI 工厂 = 自动化写作流水线：一句话创意 → 立项 → 设定 → 大纲 → 逐章生成。
          与书架的人工写作完全分开；生成的小说带 [AI] 前缀，任何章节都可接管人工编辑。
        </p>

        {loading ? (
          <p className="py-16 text-center text-sm text-muted-foreground">加载中…</p>
        ) : projects.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border py-16 text-center">
            <Bot className="mx-auto mb-3 h-10 w-10 text-primary/30" strokeWidth={1.2} />
            <p className="mb-1 text-sm text-muted-foreground">还没有 AI 项目</p>
            <p className="text-xs text-muted-foreground/70">点右上角「新建项目」，用一句话创意启动流水线</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {projects.map((p) => (
              <div
                key={p.id}
                className="group cursor-pointer rounded-lg border border-border bg-card p-4 transition-colors hover:border-primary/40"
                onClick={() => navigate(`/factory/${p.id}`)}
              >
                <div className="mb-2 flex items-start justify-between gap-2">
                  <h3 className="min-w-0 flex-1 truncate text-sm font-medium">
                    {p.novel_title ?? p.book_spec?.titles?.[0] ?? p.seed_prompt.slice(0, 24)}
                  </h3>
                  <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] ${STATUS_COLOR[p.status] ?? "bg-muted"}`}>
                    {STATUS_LABEL[p.status] ?? p.status}
                  </span>
                </div>
                <p className="mb-3 line-clamp-2 text-xs leading-5 text-muted-foreground">
                  {p.book_spec?.premise ?? p.seed_prompt}
                </p>
                <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                  <span className="tnum">
                    {p.genre && <span className="mr-2">{p.genre}</span>}
                    {p.chapter_count > 0 && `${p.chapter_count} 章`}
                    {p.target_total_words ? ` · 目标 ${(p.target_total_words / 10000).toFixed(0)}万字` : ""}
                  </span>
                  <button
                    className="rounded p-1 opacity-0 transition-opacity hover:bg-destructive/10 hover:text-destructive group-hover:opacity-100"
                    title="删除项目"
                    onClick={(e) => {
                      e.stopPropagation();
                      void remove(p);
                    }}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 新建项目 */}
      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>新建 AI 项目</DialogTitle>
            <DialogDescription>
              {mode === "idea"
                ? "一句话创意即可启动；字数目标全部可选（软约束，不硬性截断）"
                : "先拆一本参考书，学它的写法范式，再写自己的新书"}
            </DialogDescription>
          </DialogHeader>

          {/* 创建路径切换 */}
          <div className="flex gap-2">
            <button
              className={`flex-1 rounded-md border px-3 py-2 text-left text-xs transition-colors ${
                mode === "idea" ? "border-primary bg-primary/5 text-foreground" : "border-border text-muted-foreground hover:border-primary/40"
              }`}
              onClick={() => setMode("idea")}
            >
              <span className="flex items-center gap-1.5 font-medium">
                <Bot className="h-3.5 w-3.5" />
                从创意开始
              </span>
              <span className="mt-0.5 block text-[11px] text-muted-foreground">一句话点子 → AI 立项</span>
            </button>
            <button
              className={`flex-1 rounded-md border px-3 py-2 text-left text-xs transition-colors ${
                mode === "book" ? "border-primary bg-primary/5 text-foreground" : "border-border text-muted-foreground hover:border-primary/40"
              }`}
              onClick={() => setMode("book")}
            >
              <span className="flex items-center gap-1.5 font-medium">
                <BookOpen className="h-3.5 w-3.5" />
                拆书学习
              </span>
              <span className="mt-0.5 block text-[11px] text-muted-foreground">上传参考书 → 学套路写新书</span>
            </button>
          </div>

          {mode === "book" && (
            <div className="space-y-2 rounded-md border border-border p-3">
              {!refNote ? (
                <>
                  <p className="text-[11px] leading-5 text-muted-foreground">
                    上传一本你欣赏的书（txt，可多选拆多本），AI 提炼世界观结构、力量体系、人物配置、
                    节奏爽点、钩子手法与避坑清单——创建项目后生成立项/设定时自动注入，
                    人物地名全部重新原创。
                  </p>
                  <ReferenceUploader
                    compact
                    onAdd={(text) => setRefText((prev) => (prev ? `${prev}\n\n${text}` : text))}
                    onRemove={(chunk) => {
                      if (!refText.includes(chunk)) return false;
                      setRefText(refText.replace(chunk, "").replace(/\n{3,}/g, "\n\n").trim());
                      return true;
                    }}
                    onClearAll={() => setRefText("")}
                  />
                  <textarea
                    className="min-h-24 w-full rounded-md border border-border bg-transparent px-2.5 py-1.5 font-content text-xs leading-6"
                    placeholder="或直接粘贴参考书正文…"
                    value={refText}
                    onChange={(e) => setRefText(e.target.value)}
                  />
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] text-muted-foreground">
                      已备 {refText.length.toLocaleString()} 字
                      <span className="ml-1">· 本地暂存，刷新不丢</span>
                    </span>
                    <Button size="sm" variant="outline" disabled={deconstructing} onClick={() => void runDeconstruct()}>
                      {deconstructing ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Wand2 className="mr-1 h-4 w-4" />}
                      {deconstructing ? "拆解中…" : "开始拆书"}
                    </Button>
                  </div>
                </>
              ) : (
                <>
                  <div className="flex items-center justify-between">
                    <p className="text-xs font-medium text-green-600 dark:text-green-400">✓ 拆书完成（可修改）</p>
                    <button className="text-[11px] text-muted-foreground hover:text-foreground" onClick={() => setRefNote(null)}>
                      重新拆一本
                    </button>
                  </div>
                  <ReferenceFields value={refNote} onChange={setRefNote} />
                </>
              )}
            </div>
          )}

          <div className="space-y-3">
            <div>
              <Label className="text-xs">一句话创意 {mode === "book" ? "（可留空，按范式自动起头）" : "*"}</Label>
              <Textarea
                className="mt-1 min-h-20"
                placeholder="例：外卖员觉醒美食系统，靠一碗蛋炒饭征服修仙界"
                value={form.seed_prompt}
                onChange={(e) => setForm({ ...form, seed_prompt: e.target.value })}
              />
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <Label className="text-xs">类型偏好</Label>
                <Input
                  className="mt-1"
                  placeholder="玄幻 / 都市 / 科幻…"
                  value={form.genre ?? ""}
                  onChange={(e) => setForm({ ...form, genre: e.target.value })}
                />
              </div>
              <div>
                <Label className="text-xs">风格要求</Label>
                <Input
                  className="mt-1"
                  placeholder="热血 / 轻松 / 黑暗…"
                  value={form.style_notes ?? ""}
                  onChange={(e) => setForm({ ...form, style_notes: e.target.value })}
                />
              </div>
            </div>
            <button
              className="text-xs text-primary hover:underline"
              onClick={() => setShowTargets((v) => !v)}
            >
              {showTargets ? "收起字数目标" : "设置字数目标（可选）"}
            </button>
            {showTargets && (
              <div className="grid grid-cols-1 gap-3 rounded-md border border-border p-3 sm:grid-cols-2">
                <div>
                  <Label className="text-xs">总字数</Label>
                  <Input
                    className="mt-1"
                    type="number"
                    placeholder="如 1000000"
                    value={form.target_total_words ?? ""}
                    onChange={(e) => setForm({ ...form, target_total_words: numInput(e.target.value) })}
                  />
                </div>
                <div>
                  <Label className="text-xs">总章数</Label>
                  <Input
                    className="mt-1"
                    type="number"
                    placeholder="如 300"
                    value={form.target_chapters ?? ""}
                    onChange={(e) => setForm({ ...form, target_chapters: numInput(e.target.value) })}
                  />
                </div>
                <div>
                  <Label className="text-xs">卷数</Label>
                  <Input
                    className="mt-1"
                    type="number"
                    placeholder="如 3"
                    value={form.target_volumes ?? ""}
                    onChange={(e) => setForm({ ...form, target_volumes: numInput(e.target.value) })}
                  />
                </div>
                <div>
                  <Label className="text-xs">单章字数</Label>
                  <Input
                    className="mt-1"
                    type="number"
                    placeholder="如 3000"
                    value={form.target_chapter_words ?? ""}
                    onChange={(e) => setForm({ ...form, target_chapter_words: numInput(e.target.value) })}
                  />
                </div>
                <p className="col-span-2 text-[11px] text-muted-foreground">
                  目标只作 AI 生成参考（约 ±20% 浮动），剧情完整优先，绝不硬截断。
                </p>
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setCreating(false)}>
              取消
            </Button>
            <Button onClick={() => void create()} disabled={busy}>
              {busy ? "创建中…" : "创建并立项"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ImportDialog open={importing} onOpenChange={setImporting} />
    </AppShell>
  );
}
