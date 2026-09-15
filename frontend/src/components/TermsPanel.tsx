import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, Pencil, Plus, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import { termsApi, type NovelTerm } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";

/** 预设分类（也可自由输入新分类） */
const CATEGORY_PRESETS = ["人名", "地名", "招式", "法宝", "口头禅", "其他"];

interface TermsPanelProps {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  novelId: number;
  /** 点击词条：把词插入到编辑器光标处 */
  onInsert: (term: NovelTerm) => void;
}

/** 常用词（写作用语库）侧边面板：分类分组展示 / 点击插入 / 新增 / 编辑 / 删除 */
export default function TermsPanel({ open, onOpenChange, novelId, onInsert }: TermsPanelProps) {
  const [list, setList] = useState<NovelTerm[]>([]);
  const [loading, setLoading] = useState(false);
  // 新增 / 编辑表单（editing 非空 = 编辑模式）
  const [editing, setEditing] = useState<NovelTerm | null>(null);
  const [name, setName] = useState("");
  const [category, setCategory] = useState("人名");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setList(await termsApi.list(novelId));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "常用词加载失败");
    } finally {
      setLoading(false);
    }
  }, [novelId]);

  useEffect(() => {
    if (!open) return;
    resetForm();
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, load]);

  function resetForm() {
    setEditing(null);
    setName("");
    setCategory("人名");
    setNote("");
  }

  /** 按分类分组（保持后端 category+id 排序），空分组不出现 */
  const groups = useMemo(() => {
    const map = new Map<string, NovelTerm[]>();
    for (const t of list) {
      const key = t.category || "其他";
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(t);
    }
    return [...map.entries()];
  }, [list]);

  async function submit() {
    const n = name.trim();
    if (!n) {
      toast.error("请填写词条");
      return;
    }
    setSaving(true);
    try {
      const body = { category: category.trim() || "其他", name: n, note: note.trim() };
      if (editing) {
        await termsApi.update(novelId, editing.id, body);
        toast.success("词条已更新");
      } else {
        await termsApi.create(novelId, body);
        toast.success(`已收录「${n}」`);
      }
      resetForm();
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function remove(t: NovelTerm) {
    if (!window.confirm(`删除词条「${t.name}」？`)) return;
    try {
      await termsApi.remove(novelId, t.id);
      if (editing?.id === t.id) resetForm();
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除失败");
    }
  }

  function startEdit(t: NovelTerm) {
    setEditing(t);
    setName(t.name);
    setCategory(t.category);
    setNote(t.note);
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="flex w-full flex-col gap-0 p-0 sm:max-w-md">
        <SheetHeader className="border-b border-border px-4 py-3">
          <SheetTitle className="text-sm">常用词库</SheetTitle>
          <SheetDescription className="text-xs">
            点击词条插入到光标处；同人名 / 地名 / 招式 / 法宝等高频用词集中管理。
          </SheetDescription>
        </SheetHeader>

        {/* 新增 / 编辑表单 */}
        <div className="space-y-2 border-b border-border px-4 py-3">
          <div className="flex gap-2">
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="词条（如：碧霄）"
              maxLength={100}
              className="h-8 text-sm"
              onKeyDown={(e) => e.key === "Enter" && void submit()}
            />
            <Input
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              placeholder="分类"
              maxLength={50}
              list="beidou-term-categories"
              className="h-8 w-28 text-sm"
            />
            <datalist id="beidou-term-categories">
              {CATEGORY_PRESETS.map((c) => (
                <option key={c} value={c} />
              ))}
            </datalist>
          </div>
          <div className="flex gap-2">
            <Input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="备注 / 用法（可选）"
              maxLength={500}
              className="h-8 text-sm"
              onKeyDown={(e) => e.key === "Enter" && void submit()}
            />
            <Button size="sm" className="h-8 shrink-0" onClick={() => void submit()} disabled={saving}>
              {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : editing ? "保存" : <Plus className="h-3.5 w-3.5" />}
              {!saving && !editing && <span className="ml-1">收录</span>}
            </Button>
            {editing && (
              <Button size="sm" variant="ghost" className="h-8 shrink-0" onClick={resetForm}>
                <X className="h-3.5 w-3.5" />
              </Button>
            )}
          </div>
        </div>

        {/* 分组列表 */}
        <ScrollArea className="min-h-0 flex-1">
          {loading ? (
            <div className="flex items-center justify-center py-10 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
            </div>
          ) : groups.length === 0 ? (
            <p className="px-4 py-10 text-center text-xs text-muted-foreground">
              还没有收录词条，在上方添加第一个吧
            </p>
          ) : (
            <div className="px-4 py-3">
              {groups.map(([cat, terms]) => (
                <div key={cat} className="mb-4">
                  <div className="mb-1.5 text-[11px] font-medium text-muted-foreground">
                    {cat}
                    <span className="ml-1 opacity-60">{terms.length}</span>
                  </div>
                  <div className="space-y-0.5">
                    {terms.map((t) => (
                      <div
                        key={t.id}
                        className="group flex items-center gap-1 rounded px-1.5 py-1 hover:bg-accent"
                      >
                        <button
                          className="min-w-0 flex-1 text-left"
                          title={t.note ? `${t.note}（点击插入）` : "点击插入到光标处"}
                          onClick={() => onInsert(t)}
                        >
                          <span className="text-sm">{t.name}</span>
                          {t.note && (
                            <span className="ml-2 truncate text-[11px] text-muted-foreground">
                              {t.note}
                            </span>
                          )}
                        </button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-6 w-6 shrink-0 opacity-0 group-hover:opacity-100"
                          title="编辑词条"
                          onClick={() => startEdit(t)}
                        >
                          <Pencil className="h-3 w-3" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-6 w-6 shrink-0 opacity-0 group-hover:opacity-100"
                          title="删除词条"
                          onClick={() => void remove(t)}
                        >
                          <Trash2 className="h-3 w-3" />
                        </Button>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}
