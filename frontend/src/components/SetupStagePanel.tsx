/** AI 工厂 · 设定阶段编辑面板（角色卡 + 世界观条目）
 *
 * 生成设定后停留预览：逐项编辑/删除/新增，直接读写小说的设定系统
 * （/api/novels/{id}/settings/characters|worldview 现成 CRUD），
 * 确认满意后点「生成大纲」推进。所有修改即时落库——不存在「没设完不能保存」。
 */

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Loader2, Pencil, Plus, Sparkles, Trash2, X } from "lucide-react";
import { api, aiFactoryApi, type AiProject, type Character, type WorldviewEntry } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface Props {
  project: AiProject;
  busy: string | null;
  onRun: (key: string, fn: () => Promise<AiProject>, okMsg: string) => Promise<void>;
  onOutline: () => void;
}

export default function SetupStagePanel({ project, busy, onRun, onOutline }: Props) {
  const novelId = project.novel_id;
  const [chars, setChars] = useState<Character[]>([]);
  const [worlds, setWorlds] = useState<WorldviewEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingChar, setEditingChar] = useState<Partial<Character> | null>(null);
  const [editingWorld, setEditingWorld] = useState<Partial<WorldviewEntry> | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(() => {
    if (!novelId) return;
    setLoading(true);
    Promise.all([
      api.get<Character[]>(`/api/novels/${novelId}/settings/characters`),
      api.get<WorldviewEntry[]>(`/api/novels/${novelId}/settings/worldview`),
    ])
      .then(([c, w]) => {
        setChars(c);
        setWorlds(w);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [novelId]);
  useEffect(load, [load]);

  async function saveChar() {
    if (!editingChar?.name?.trim()) {
      toast.error("角色名不能为空");
      return;
    }
    setSaving(true);
    const body = {
      name: editingChar.name.trim(),
      role: editingChar.role ?? "配角",
      tags: editingChar.tags ?? "",
      description: editingChar.description ?? "",
      relations: editingChar.relations ?? "",
    };
    try {
      if (editingChar.id) {
        await api.put(`/api/novels/${novelId}/settings/characters/${editingChar.id}`, body);
      } else {
        await api.post(`/api/novels/${novelId}/settings/characters`, body);
      }
      toast.success("角色已保存");
      setEditingChar(null);
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function saveWorld() {
    if (!editingWorld?.title?.trim()) {
      toast.error("条目标题不能为空");
      return;
    }
    setSaving(true);
    const body = {
      category: editingWorld.category ?? "其他",
      title: editingWorld.title.trim(),
      content: editingWorld.content ?? "",
    };
    try {
      if (editingWorld.id) {
        await api.put(`/api/novels/${novelId}/settings/worldview/${editingWorld.id}`, body);
      } else {
        await api.post(`/api/novels/${novelId}/settings/worldview`, body);
      }
      toast.success("条目已保存");
      setEditingWorld(null);
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function removeChar(id: number) {
    if (!confirm("删除这个角色？")) return;
    await api.delete(`/api/novels/${novelId}/settings/characters/${id}`);
    load();
  }

  async function removeWorld(id: number) {
    if (!confirm("删除这个条目？")) return;
    await api.delete(`/api/novels/${novelId}/settings/worldview/${id}`);
    load();
  }

  const hasSetup = chars.length > 0 || worlds.length > 0;

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-border bg-card p-4">
        <div className="mb-3 flex items-center justify-between">
          <div>
            <h3 className="text-sm font-medium">设定（角色卡 + 世界观）</h3>
            <p className="text-[11px] text-muted-foreground/70">所有修改即时保存到书稿设定系统，随时可改</p>
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              className="h-8"
              disabled={busy !== null}
              onClick={() => {
                if (hasSetup && !confirm("重新生成会重建全部角色卡和世界观（你手动的修改将被覆盖），继续？")) return;
                void onRun("setup", () => aiFactoryApi.setup(project.id), "设定已生成，可逐项编辑");
              }}
            >
              {busy === "setup" ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Sparkles className="mr-1 h-4 w-4" />}
              {hasSetup ? "重新生成设定" : "AI 生成设定"}
            </Button>
            {hasSetup && (
              <Button size="sm" className="h-8" onClick={onOutline} disabled={busy !== null}>
                {busy === "outline" ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
                设定完成，生成大纲 →
              </Button>
            )}
          </div>
        </div>

        {loading ? (
          <p className="py-6 text-center text-xs text-muted-foreground">加载中…</p>
        ) : !hasSetup ? (
          <p className="py-6 text-center text-xs text-muted-foreground">
            还没有设定——点「AI 生成设定」起草（角色 4-8 个 + 世界观 6-12 条），或手动新增
          </p>
        ) : (
          <div className="space-y-4">
            {/* 角色卡 */}
            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <p className="text-xs font-medium text-muted-foreground">角色卡（{chars.length}）</p>
                <Button variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={() => setEditingChar({ role: "配角" })}>
                  <Plus className="mr-1 h-3 w-3" />
                  新增
                </Button>
              </div>
              <div className="space-y-1.5">
                {chars.map((c) => (
                  <div key={c.id} className="group flex items-start gap-2 rounded border border-border px-2.5 py-1.5 text-xs">
                    <div className="min-w-0 flex-1">
                      <span className="font-medium">{c.name}</span>
                      <span className="mx-1.5 text-primary">{c.role}</span>
                      <p className="mt-0.5 line-clamp-2 text-muted-foreground">{c.description}</p>
                    </div>
                    <button className="shrink-0 p-1 text-muted-foreground hover:text-foreground" onClick={() => setEditingChar(c)}>
                      <Pencil className="h-3 w-3" />
                    </button>
                    <button className="shrink-0 p-1 text-muted-foreground hover:text-destructive" onClick={() => void removeChar(c.id)}>
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </div>
                ))}
              </div>
            </div>

            {/* 世界观 */}
            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <p className="text-xs font-medium text-muted-foreground">世界观条目（{worlds.length}）</p>
                <Button variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={() => setEditingWorld({ category: "其他" })}>
                  <Plus className="mr-1 h-3 w-3" />
                  新增
                </Button>
              </div>
              <div className="space-y-1.5">
                {worlds.map((w) => (
                  <div key={w.id} className="flex items-start gap-2 rounded border border-border px-2.5 py-1.5 text-xs">
                    <div className="min-w-0 flex-1">
                      <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">{w.category}</span>
                      <span className="ml-1.5 font-medium">{w.title}</span>
                      <p className="mt-0.5 line-clamp-2 text-muted-foreground">{w.content}</p>
                    </div>
                    <button className="shrink-0 p-1 text-muted-foreground hover:text-foreground" onClick={() => setEditingWorld(w)}>
                      <Pencil className="h-3 w-3" />
                    </button>
                    <button className="shrink-0 p-1 text-muted-foreground hover:text-destructive" onClick={() => void removeWorld(w.id)}>
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* 角色编辑弹窗 */}
      {editingChar && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={() => setEditingChar(null)}>
          <div className="w-full max-w-md rounded-lg border border-border bg-background p-4" onClick={(e) => e.stopPropagation()}>
            <div className="mb-3 flex items-center justify-between">
              <h4 className="text-sm font-medium">{editingChar.id ? "编辑角色" : "新增角色"}</h4>
              <button onClick={() => setEditingChar(null)} className="text-muted-foreground hover:text-foreground">
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="space-y-2.5">
              <div className="grid grid-cols-2 gap-2">
                <Input placeholder="角色名" value={editingChar.name ?? ""} onChange={(e) => setEditingChar({ ...editingChar, name: e.target.value })} />
                <Input placeholder="身份（主角/配角/反派…）" value={editingChar.role ?? ""} onChange={(e) => setEditingChar({ ...editingChar, role: e.target.value })} />
              </div>
              <textarea
                className="min-h-28 w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm"
                placeholder="外貌+性格+动机+口癖+弧光"
                value={editingChar.description ?? ""}
                onChange={(e) => setEditingChar({ ...editingChar, description: e.target.value })}
              />
              <div className="flex justify-end gap-2">
                <Button variant="ghost" size="sm" onClick={() => setEditingChar(null)}>取消</Button>
                <Button size="sm" disabled={saving} onClick={() => void saveChar()}>
                  {saving && <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />}
                  保存
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 世界观编辑弹窗 */}
      {editingWorld && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={() => setEditingWorld(null)}>
          <div className="w-full max-w-md rounded-lg border border-border bg-background p-4" onClick={(e) => e.stopPropagation()}>
            <div className="mb-3 flex items-center justify-between">
              <h4 className="text-sm font-medium">{editingWorld.id ? "编辑条目" : "新增条目"}</h4>
              <button onClick={() => setEditingWorld(null)} className="text-muted-foreground hover:text-foreground">
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="space-y-2.5">
              <div className="grid grid-cols-2 gap-2">
                <Input placeholder="分类（力量体系/地理/势力…）" value={editingWorld.category ?? ""} onChange={(e) => setEditingWorld({ ...editingWorld, category: e.target.value })} />
                <Input placeholder="条目标题" value={editingWorld.title ?? ""} onChange={(e) => setEditingWorld({ ...editingWorld, title: e.target.value })} />
              </div>
              <textarea
                className="min-h-28 w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm"
                placeholder="条目内容"
                value={editingWorld.content ?? ""}
                onChange={(e) => setEditingWorld({ ...editingWorld, content: e.target.value })}
              />
              <div className="flex justify-end gap-2">
                <Button variant="ghost" size="sm" onClick={() => setEditingWorld(null)}>取消</Button>
                <Button size="sm" disabled={saving} onClick={() => void saveWorld()}>
                  {saving && <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />}
                  保存
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
