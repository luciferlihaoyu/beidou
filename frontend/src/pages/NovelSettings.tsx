import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router";
import { GitBranch, Landmark, MoreHorizontal, PenLine, Plus, Trash2, Users } from "lucide-react";
import { toast } from "sonner";
import AppShell from "@/components/AppShell";
import {
  api,
  type Character,
  type Foreshadowing,
  type Novel,
  type OutlineNode,
  type WorldviewEntry,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";

const CHARACTER_ROLES = ["主角", "配角", "反派", "导师", "其他"];
const WORLDVIEW_CATEGORIES = ["势力", "地理", "历史", "规则", "物品", "其他"];
const FS_STATUSES = ["未回收", "进行中", "已回收"];

/* ---------------- 角色 ---------------- */

function CharactersTab({ novelId }: { novelId: number }) {
  const [items, setItems] = useState<Character[]>([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Character | null>(null);
  const [form, setForm] = useState({ name: "", role: "配角", tags: "", description: "", relations: "" });

  const load = useCallback(() => {
    api.get<Character[]>(`/api/novels/${novelId}/settings/characters`).then(setItems).catch((e) => toast.error(e.message));
  }, [novelId]);
  useEffect(load, [load]);

  function openDialog(item: Character | null) {
    setEditing(item);
    setForm(
      item
        ? { name: item.name, role: item.role, tags: item.tags, description: item.description, relations: item.relations }
        : { name: "", role: "配角", tags: "", description: "", relations: "" }
    );
    setOpen(true);
  }

  async function save() {
    if (!form.name.trim()) return toast.error("请填写角色名");
    try {
      if (editing) await api.put(`/api/novels/${novelId}/settings/characters/${editing.id}`, form);
      else await api.post(`/api/novels/${novelId}/settings/characters`, form);
      setOpen(false);
      load();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function remove(item: Character) {
    if (!window.confirm(`删除角色「${item.name}」？`)) return;
    await api.delete(`/api/novels/${novelId}/settings/characters/${item.id}`);
    load();
  }

  return (
    <div>
      <TabHeader title="角色" count={items.length} onAdd={() => openDialog(null)} />
      {items.length === 0 ? (
        <EmptyHint text="创建第一个角色卡片" />
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {items.map((c) => (
            <div key={c.id} className="rounded-lg border border-border bg-card p-4">
              <div className="flex items-start justify-between">
                <div>
                  <span className="font-content text-base font-semibold">{c.name}</span>
                  <Badge variant="secondary" className="ml-2">{c.role}</Badge>
                </div>
                <ItemMenu onEdit={() => openDialog(c)} onDelete={() => void remove(c)} />
              </div>
              {c.tags && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {c.tags.split(/[,，]/).filter(Boolean).map((t) => (
                    <span key={t} className="rounded border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground">
                      {t.trim()}
                    </span>
                  ))}
                </div>
              )}
              {c.description && <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-muted-foreground">{c.description}</p>}
              {c.relations && <p className="mt-2 border-t border-border pt-2 text-xs text-muted-foreground">关系：{c.relations}</p>}
            </div>
          ))}
        </div>
      )}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader><DialogTitle>{editing ? "编辑角色" : "新建角色"}</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>姓名</Label>
                <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
              </div>
              <div className="space-y-2">
                <Label>定位</Label>
                <Select value={form.role} onValueChange={(v) => setForm({ ...form, role: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {CHARACTER_ROLES.map((r) => <SelectItem key={r} value={r}>{r}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-2">
              <Label>性格标签（逗号分隔）</Label>
              <Input value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })} placeholder="冷静，护短，嘴硬" />
            </div>
            <div className="space-y-2">
              <Label>人物小传</Label>
              <Textarea rows={4} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label>人物关系</Label>
              <Textarea rows={2} value={form.relations} onChange={(e) => setForm({ ...form, relations: e.target.value })} placeholder="与××是师徒；与××有血仇…" />
            </div>
          </div>
          <DialogFooter><Button onClick={() => void save()}>保存</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/* ---------------- 世界观 ---------------- */

function WorldviewTab({ novelId }: { novelId: number }) {
  const [items, setItems] = useState<WorldviewEntry[]>([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<WorldviewEntry | null>(null);
  const [form, setForm] = useState({ category: "其他", title: "", content: "" });

  const load = useCallback(() => {
    api.get<WorldviewEntry[]>(`/api/novels/${novelId}/settings/worldview`).then(setItems).catch((e) => toast.error(e.message));
  }, [novelId]);
  useEffect(load, [load]);

  const grouped = WORLDVIEW_CATEGORIES.map((cat) => ({
    cat,
    list: items.filter((i) => i.category === cat),
  })).filter((g) => g.list.length > 0);

  async function save() {
    if (!form.title.trim()) return toast.error("请填写标题");
    try {
      if (editing) await api.put(`/api/novels/${novelId}/settings/worldview/${editing.id}`, form);
      else await api.post(`/api/novels/${novelId}/settings/worldview`, form);
      setOpen(false);
      load();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  return (
    <div>
      <TabHeader title="世界观" count={items.length} onAdd={() => { setEditing(null); setForm({ category: "其他", title: "", content: "" }); setOpen(true); }} />
      {items.length === 0 ? (
        <EmptyHint text="记录势力、地理、规则等设定" />
      ) : (
        <div className="space-y-6">
          {grouped.map((g) => (
            <div key={g.cat}>
              <h3 className="mb-2 text-xs font-medium tracking-widest text-muted-foreground">{g.cat}</h3>
              <div className="space-y-2">
                {g.list.map((w) => (
                  <div key={w.id} className="rounded-lg border border-border bg-card p-4">
                    <div className="flex items-start justify-between">
                      <span className="font-medium">{w.title}</span>
                      <ItemMenu
                        onEdit={() => { setEditing(w); setForm({ category: w.category, title: w.title, content: w.content }); setOpen(true); }}
                        onDelete={async () => {
                          if (window.confirm(`删除「${w.title}」？`)) {
                            await api.delete(`/api/novels/${novelId}/settings/worldview/${w.id}`);
                            load();
                          }
                        }}
                      />
                    </div>
                    {w.content && <p className="mt-1.5 whitespace-pre-wrap text-sm leading-6 text-muted-foreground">{w.content}</p>}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader><DialogTitle>{editing ? "编辑条目" : "新建条目"}</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-3 gap-4">
              <div className="space-y-2">
                <Label>分类</Label>
                <Select value={form.category} onValueChange={(v) => setForm({ ...form, category: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {WORLDVIEW_CATEGORIES.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="col-span-2 space-y-2">
                <Label>标题</Label>
                <Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
              </div>
            </div>
            <div className="space-y-2">
              <Label>内容</Label>
              <Textarea rows={5} value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} />
            </div>
          </div>
          <DialogFooter><Button onClick={() => void save()}>保存</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/* ---------------- 伏笔 ---------------- */

function ForeshadowingTab({ novelId }: { novelId: number }) {
  const [items, setItems] = useState<Foreshadowing[]>([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Foreshadowing | null>(null);
  const [form, setForm] = useState({ title: "", content: "", status: "未回收" });

  const load = useCallback(() => {
    api.get<Foreshadowing[]>(`/api/novels/${novelId}/settings/foreshadowings`).then(setItems).catch((e) => toast.error(e.message));
  }, [novelId]);
  useEffect(load, [load]);

  async function save() {
    if (!form.title.trim()) return toast.error("请填写标题");
    try {
      if (editing) await api.put(`/api/novels/${novelId}/settings/foreshadowings/${editing.id}`, form);
      else await api.post(`/api/novels/${novelId}/settings/foreshadowings`, form);
      setOpen(false);
      load();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  const statusColor: Record<string, string> = {
    未回收: "border-primary/40 text-primary",
    进行中: "border-amber-500/50 text-amber-600",
    已回收: "border-border text-muted-foreground",
  };

  return (
    <div>
      <TabHeader title="伏笔" count={items.length} onAdd={() => { setEditing(null); setForm({ title: "", content: "", status: "未回收" }); setOpen(true); }} />
      {items.length === 0 ? (
        <EmptyHint text="埋下的伏笔记在这里，别忘回收" />
      ) : (
        <div className="space-y-2">
          {items.map((f) => (
            <div key={f.id} className="rounded-lg border border-border bg-card p-4">
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{f.title}</span>
                  <span className={`rounded border px-1.5 py-0.5 text-[11px] ${statusColor[f.status] ?? ""}`}>{f.status}</span>
                </div>
                <ItemMenu
                  onEdit={() => { setEditing(f); setForm({ title: f.title, content: f.content, status: f.status }); setOpen(true); }}
                  onDelete={async () => {
                    if (window.confirm(`删除伏笔「${f.title}」？`)) {
                      await api.delete(`/api/novels/${novelId}/settings/foreshadowings/${f.id}`);
                      load();
                    }
                  }}
                />
              </div>
              {f.content && <p className="mt-1.5 whitespace-pre-wrap text-sm leading-6 text-muted-foreground">{f.content}</p>}
            </div>
          ))}
        </div>
      )}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader><DialogTitle>{editing ? "编辑伏笔" : "新建伏笔"}</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-3 gap-4">
              <div className="space-y-2">
                <Label>状态</Label>
                <Select value={form.status} onValueChange={(v) => setForm({ ...form, status: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {FS_STATUSES.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="col-span-2 space-y-2">
                <Label>标题</Label>
                <Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
              </div>
            </div>
            <div className="space-y-2">
              <Label>伏笔内容与回收计划</Label>
              <Textarea rows={4} value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} />
            </div>
          </div>
          <DialogFooter><Button onClick={() => void save()}>保存</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/* ---------------- 大纲 ---------------- */

function OutlineTab({ novelId }: { novelId: number }) {
  const [nodes, setNodes] = useState<OutlineNode[]>([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<OutlineNode | null>(null);
  const [parentId, setParentId] = useState<number | null>(null);
  const [form, setForm] = useState({ title: "", content: "" });

  const load = useCallback(() => {
    api.get<OutlineNode[]>(`/api/novels/${novelId}/settings/outline`).then(setNodes).catch((e) => toast.error(e.message));
  }, [novelId]);
  useEffect(load, [load]);

  function openDialog(item: OutlineNode | null, parent: number | null) {
    setEditing(item);
    setParentId(item ? item.parent_id : parent);
    setForm(item ? { title: item.title, content: item.content } : { title: "", content: "" });
    setOpen(true);
  }

  async function save() {
    if (!form.title.trim()) return toast.error("请填写标题");
    try {
      if (editing) {
        await api.put(`/api/novels/${novelId}/settings/outline/${editing.id}`, {
          parent_id: editing.parent_id,
          sort_order: editing.sort_order,
          ...form,
        });
      } else {
        await api.post(`/api/novels/${novelId}/settings/outline`, { parent_id: parentId, sort_order: nodes.length, ...form });
      }
      setOpen(false);
      load();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function remove(node: OutlineNode) {
    if (!window.confirm(`删除「${node.title}」？其下子节点也会删除。`)) return;
    await api.delete(`/api/novels/${novelId}/settings/outline/${node.id}`);
    load();
  }

  function renderLevel(parent: number | null, depth: number) {
    const list = nodes.filter((n) => n.parent_id === parent);
    if (list.length === 0) return null;
    return (
      <div className={depth > 0 ? "ml-5 border-l border-border pl-3" : ""}>
        {list.map((n) => (
          <div key={n.id} className="mb-2">
            <div className="rounded-lg border border-border bg-card p-3.5">
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-2">
                  {depth === 0 && <GitBranch className="h-3.5 w-3.5 text-primary" />}
                  <span className={depth === 0 ? "font-medium" : "text-sm font-medium"}>{n.title}</span>
                </div>
                <div className="flex items-center">
                  {depth < 2 && (
                    <button
                      className="mr-1 rounded p-1 text-muted-foreground hover:bg-muted hover:text-primary"
                      title="添加子节点"
                      onClick={() => openDialog(null, n.id)}
                    >
                      <Plus className="h-3.5 w-3.5" />
                    </button>
                  )}
                  <ItemMenu onEdit={() => openDialog(n, n.parent_id)} onDelete={() => void remove(n)} />
                </div>
              </div>
              {n.content && <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-muted-foreground">{n.content}</p>}
            </div>
            {renderLevel(n.id, depth + 1)}
          </div>
        ))}
      </div>
    );
  }

  return (
    <div>
      <TabHeader title="大纲" count={nodes.length} onAdd={() => openDialog(null, null)} addLabel="新建节点" />
      {nodes.length === 0 ? (
        <EmptyHint text="按「卷 → 章 → 场景」组织你的大纲" />
      ) : (
        renderLevel(null, 0)
      )}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader><DialogTitle>{editing ? "编辑节点" : parentId ? "新建子节点" : "新建节点"}</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>标题</Label>
              <Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="如：第一卷 · 少年出山" />
            </div>
            <div className="space-y-2">
              <Label>内容</Label>
              <Textarea rows={5} value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} placeholder="这一阶段发生什么…" />
            </div>
          </div>
          <DialogFooter><Button onClick={() => void save()}>保存</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/* ---------------- 公共小组件 ---------------- */

function TabHeader({ title, count, onAdd, addLabel }: { title: string; count: number; onAdd: () => void; addLabel?: string }) {
  return (
    <div className="mb-4 flex items-center justify-between">
      <p className="text-sm text-muted-foreground tnum">{count} 条{title}</p>
      <Button size="sm" variant="outline" className="h-8" onClick={onAdd}>
        <Plus className="mr-1 h-4 w-4" />
        {addLabel ?? `新建${title}`}
      </Button>
    </div>
  );
}

function ItemMenu({ onEdit, onDelete }: { onEdit: () => void; onDelete: () => void }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button className="rounded p-1 text-muted-foreground hover:bg-muted">
          <MoreHorizontal className="h-4 w-4" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={onEdit}>
          <PenLine className="mr-2 h-4 w-4" />
          编辑
        </DropdownMenuItem>
        <DropdownMenuItem className="text-destructive" onClick={onDelete}>
          <Trash2 className="mr-2 h-4 w-4" />
          删除
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function EmptyHint({ text }: { text: string }) {
  return (
    <div className="rounded-lg border border-dashed border-input py-16 text-center text-sm text-muted-foreground">
      {text}
    </div>
  );
}

/* ---------------- 页面 ---------------- */

export default function NovelSettings() {
  const { id } = useParams<{ id: string }>();
  const novelId = Number(id);
  const [novel, setNovel] = useState<Novel | null>(null);

  useEffect(() => {
    api.get<Novel>(`/api/novels/${novelId}`).then(setNovel).catch((e) => toast.error(e.message));
  }, [novelId]);

  return (
    <AppShell
      back={`/novel/${novelId}`}
      title={
        <span>
          <span className="font-content font-medium">{novel?.title ?? "…"}</span>
          <span className="ml-2 text-muted-foreground">设定</span>
        </span>
      }
    >
      <ScrollArea className="h-full">
        <div className="mx-auto max-w-4xl px-6 py-8">
          <Tabs defaultValue="outline">
            <TabsList className="mb-6">
              <TabsTrigger value="outline">
                <GitBranch className="mr-1.5 h-3.5 w-3.5" />
                大纲
              </TabsTrigger>
              <TabsTrigger value="characters">
                <Users className="mr-1.5 h-3.5 w-3.5" />
                角色
              </TabsTrigger>
              <TabsTrigger value="worldview">
                <Landmark className="mr-1.5 h-3.5 w-3.5" />
                世界观
              </TabsTrigger>
              <TabsTrigger value="foreshadowing">
                <GitBranch className="mr-1.5 h-3.5 w-3.5 rotate-180" />
                伏笔
              </TabsTrigger>
            </TabsList>
            <TabsContent value="outline"><OutlineTab novelId={novelId} /></TabsContent>
            <TabsContent value="characters"><CharactersTab novelId={novelId} /></TabsContent>
            <TabsContent value="worldview"><WorldviewTab novelId={novelId} /></TabsContent>
            <TabsContent value="foreshadowing"><ForeshadowingTab novelId={novelId} /></TabsContent>
          </Tabs>
        </div>
      </ScrollArea>
    </AppShell>
  );
}
