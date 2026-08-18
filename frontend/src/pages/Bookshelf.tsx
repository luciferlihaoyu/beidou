import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { BookOpen, MoreHorizontal, PenLine, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import AppShell from "@/components/AppShell";
import { api, type Novel } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const COVER_COLORS = [
  "#004EFF",
  "#0EA5E9",
  "#6366F1",
  "#0F766E",
  "#B45309",
  "#BE123C",
  "#4D7C0F",
  "#24272A",
];

const GENRES = ["玄幻", "都市", "仙侠", "科幻", "历史", "悬疑", "言情", "奇幻", "武侠", "其他"];
const STATUSES = ["连载中", "已完结", "暂停"];

interface FormState {
  title: string;
  author: string;
  genre: string;
  status: string;
  description: string;
  cover_color: string;
}

const emptyForm: FormState = {
  title: "",
  author: "",
  genre: "",
  status: "连载中",
  description: "",
  cover_color: COVER_COLORS[0],
};

function NovelDialog({
  open,
  onOpenChange,
  initial,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  initial: Novel | null;
  onSaved: () => void;
}) {
  const [form, setForm] = useState<FormState>(emptyForm);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) {
      setForm(
        initial
          ? {
              title: initial.title,
              author: initial.author,
              genre: initial.genre,
              status: initial.status,
              description: initial.description,
              cover_color: initial.cover_color,
            }
          : emptyForm
      );
    }
  }, [open, initial]);

  async function save() {
    if (!form.title.trim()) {
      toast.error("请填写书名");
      return;
    }
    setSaving(true);
    try {
      if (initial) await api.put(`/api/novels/${initial.id}`, form);
      else await api.post("/api/novels", form);
      onOpenChange(false);
      onSaved();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{initial ? "编辑作品" : "新建作品"}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label>书名</Label>
            <Input
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              placeholder="输入书名"
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>笔名</Label>
              <Input
                value={form.author}
                onChange={(e) => setForm({ ...form, author: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>类型</Label>
              <Select value={form.genre} onValueChange={(v) => setForm({ ...form, genre: v })}>
                <SelectTrigger>
                  <SelectValue placeholder="选择类型" />
                </SelectTrigger>
                <SelectContent>
                  {GENRES.map((g) => (
                    <SelectItem key={g} value={g}>
                      {g}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-2">
            <Label>简介</Label>
            <Textarea
              rows={3}
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="一句话概括这个故事…"
            />
          </div>
          <div className="space-y-2">
            <Label>封面色</Label>
            <div className="flex gap-2">
              {COVER_COLORS.map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => setForm({ ...form, cover_color: c })}
                  className="h-6 w-6 rounded-full transition-transform hover:scale-110"
                  style={{
                    backgroundColor: c,
                    boxShadow: form.cover_color === c ? `0 0 0 2px #fff, 0 0 0 4px ${c}` : "none",
                  }}
                />
              ))}
            </div>
          </div>
          {initial && (
            <div className="space-y-2">
              <Label>状态</Label>
              <Select value={form.status} onValueChange={(v) => setForm({ ...form, status: v })}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {STATUSES.map((s) => (
                    <SelectItem key={s} value={s}>
                      {s}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
        </div>
        <DialogFooter>
          <Button onClick={save} disabled={saving}>
            {saving ? "保存中…" : "保存"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function Bookshelf() {
  const [novels, setNovels] = useState<Novel[]>([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<Novel | null>(null);
  const [deleting, setDeleting] = useState<Novel | null>(null);
  const navigate = useNavigate();

  async function load() {
    try {
      setNovels(await api.get<Novel[]>("/api/novels"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function remove() {
    if (!deleting) return;
    try {
      await api.delete(`/api/novels/${deleting.id}`);
      setDeleting(null);
      void load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除失败");
    }
  }

  return (
    <AppShell
      actions={
        <Button
          size="sm"
          className="h-8"
          onClick={() => {
            setEditing(null);
            setDialogOpen(true);
          }}
        >
          <Plus className="mr-1 h-4 w-4" />
          新建作品
        </Button>
      }
    >
      <div className="mx-auto max-w-5xl px-6 py-10">
        <div className="mb-8 flex items-end justify-between">
          <div>
            <h1 className="font-content text-2xl font-semibold">书架</h1>
            <p className="mt-1 text-sm text-muted-foreground tnum">
              共 {novels.length} 部作品
            </p>
          </div>
        </div>

        {loading ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {[1, 2, 3].map((i) => (
              <div key={i} className="h-44 animate-pulse rounded-lg border border-border bg-card" />
            ))}
          </div>
        ) : novels.length === 0 ? (
          <div className="rise-in flex flex-col items-center rounded-lg border border-dashed border-input py-24">
            <BookOpen className="h-10 w-10 text-muted-foreground/40" strokeWidth={1.2} />
            <p className="mt-4 text-sm text-muted-foreground">书架还是空的</p>
            <Button
              variant="outline"
              size="sm"
              className="mt-4"
              onClick={() => {
                setEditing(null);
                setDialogOpen(true);
              }}
            >
              <Plus className="mr-1 h-4 w-4" />
              创建第一部作品
            </Button>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {novels.map((novel, i) => (
              <div
                key={novel.id}
                className="rise-in group relative flex cursor-pointer overflow-hidden rounded-lg border border-border bg-card transition-all duration-200 hover:border-primary/50 hover:shadow-[0_1px_20px_rgba(0,0,0,0.06)]"
                style={{ animationDelay: `${i * 50}ms` }}
                onClick={() => navigate(`/novel/${novel.id}`)}
              >
                <div className="w-1.5 shrink-0" style={{ backgroundColor: novel.cover_color }} />
                <div className="flex min-w-0 flex-1 flex-col p-5">
                  <div className="flex items-start justify-between gap-2">
                    <h3 className="font-content min-w-0 flex-1 truncate text-lg font-semibold">
                      {novel.title}
                    </h3>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <button
                          className="rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-muted group-hover:opacity-100"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <MoreHorizontal className="h-4 w-4" />
                        </button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
                        <DropdownMenuItem
                          onClick={() => {
                            setEditing(novel);
                            setDialogOpen(true);
                          }}
                        >
                          <PenLine className="mr-2 h-4 w-4" />
                          编辑信息
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          className="text-destructive"
                          onClick={() => setDeleting(novel)}
                        >
                          <Trash2 className="mr-2 h-4 w-4" />
                          删除
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {[novel.author, novel.genre, novel.status].filter(Boolean).join(" · ")}
                  </p>
                  <p className="mt-3 line-clamp-2 min-h-10 flex-1 text-sm leading-5 text-muted-foreground">
                    {novel.description || "还没有简介。"}
                  </p>
                  <div className="mt-4 flex items-center gap-4 border-t border-border pt-3 text-xs text-muted-foreground tnum">
                    <span>{novel.chapter_count} 章</span>
                    <span>{novel.total_words.toLocaleString()} 字</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <NovelDialog open={dialogOpen} onOpenChange={setDialogOpen} initial={editing} onSaved={load} />

      <AlertDialog open={!!deleting} onOpenChange={() => setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除《{deleting?.title}》？</AlertDialogTitle>
            <AlertDialogDescription>
              作品下的全部章节与设定都会被删除，此操作不可恢复。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={remove}
            >
              确认删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </AppShell>
  );
}
