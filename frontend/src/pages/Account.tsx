import { useCallback, useEffect, useState } from "react";
import { KeyRound, Loader2, MoreHorizontal, PenLine, Plug, Plus, Star, Trash2 } from "lucide-react";
import { toast } from "sonner";
import AppShell from "@/components/AppShell";
import { api, type AIConfig } from "@/lib/api";
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
import { Separator } from "@/components/ui/separator";

interface ConfigForm {
  name: string;
  base_url: string;
  api_key: string;
  model: string;
  is_default: boolean;
}

const emptyConfig: ConfigForm = {
  name: "",
  base_url: "https://api.deepseek.com",
  api_key: "",
  model: "deepseek-chat",
  is_default: false,
};

const PRESETS = [
  { name: "DeepSeek", base_url: "https://api.deepseek.com", model: "deepseek-chat" },
  { name: "Kimi", base_url: "https://api.moonshot.cn", model: "kimi-k2-0905-preview" },
  { name: "OpenAI", base_url: "https://api.openai.com", model: "gpt-4o-mini" },
];

export default function Account() {
  const [configs, setConfigs] = useState<AIConfig[]>([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<AIConfig | null>(null);
  const [form, setForm] = useState<ConfigForm>(emptyConfig);
  const [testing, setTesting] = useState(false);
  const [pw, setPw] = useState({ old_password: "", new_password: "", confirm: "" });
  const [pwSaving, setPwSaving] = useState(false);

  const load = useCallback(() => {
    api.get<AIConfig[]>("/api/ai/configs").then(setConfigs).catch((e) => toast.error(e.message));
  }, []);
  useEffect(load, [load]);

  function openDialog(item: AIConfig | null) {
    setEditing(item);
    setForm(
      item
        ? { name: item.name, base_url: item.base_url, api_key: "", model: item.model, is_default: item.is_default }
        : emptyConfig
    );
    setOpen(true);
  }

  async function save() {
    if (!form.name.trim()) return toast.error("请填写配置名称");
    if (!editing && !form.api_key.trim()) return toast.error("请填写 API Key");
    try {
      if (editing) await api.put(`/api/ai/configs/${editing.id}`, form);
      else await api.post("/api/ai/configs", form);
      setOpen(false);
      load();
      toast.success("已保存");
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function test() {
    const key = form.api_key.trim();
    if (!key && !editing?.has_key) return toast.error("请先填写 API Key");
    setTesting(true);
    try {
      if (!key) return toast.info("测试需要重新输入 API Key");
      const result = await api.post<{ reply: string }>("/api/ai/test", {
        base_url: form.base_url,
        api_key: key,
        model: form.model,
      });
      toast.success(`连接成功：${result.reply.slice(0, 30)}`);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setTesting(false);
    }
  }

  async function changePassword() {
    if (pw.new_password.length < 6) return toast.error("新密码至少 6 位");
    if (pw.new_password !== pw.confirm) return toast.error("两次输入的新密码不一致");
    setPwSaving(true);
    try {
      await api.post("/api/auth/change-password", {
        old_password: pw.old_password,
        new_password: pw.new_password,
      });
      setPw({ old_password: "", new_password: "", confirm: "" });
      toast.success("密码已修改");
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setPwSaving(false);
    }
  }

  return (
    <AppShell back="/" title="账号设置">
      <ScrollArea className="h-full">
        <div className="mx-auto max-w-3xl px-6 py-8">
          {/* AI 接口配置 */}
          <section>
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h2 className="text-base font-semibold">AI 接口配置</h2>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  支持任意 OpenAI 兼容接口（DeepSeek / Kimi / OpenAI 等）
                </p>
              </div>
              <Button size="sm" className="h-8" onClick={() => openDialog(null)}>
                <Plus className="mr-1 h-4 w-4" />
                添加配置
              </Button>
            </div>

            {configs.length === 0 ? (
              <div className="rounded-lg border border-dashed border-input py-12 text-center text-sm text-muted-foreground">
                还没有 AI 配置，添加后才能在编辑器中使用 AI 助手
              </div>
            ) : (
              <div className="space-y-2">
                {configs.map((c) => (
                  <div key={c.id} className="flex items-center gap-3 rounded-lg border border-border bg-card px-4 py-3">
                    <Plug className="h-4 w-4 shrink-0 text-primary" />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="truncate text-sm font-medium">{c.name}</span>
                        {c.is_default && (
                          <Badge variant="secondary" className="gap-1">
                            <Star className="h-3 w-3" />
                            默认
                          </Badge>
                        )}
                      </div>
                      <p className="mt-0.5 truncate text-xs text-muted-foreground tnum">
                        {c.base_url} · {c.model} · {c.has_key ? "已配置 Key" : "未配置 Key"}
                      </p>
                    </div>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <button className="rounded p-1 text-muted-foreground hover:bg-muted">
                          <MoreHorizontal className="h-4 w-4" />
                        </button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onClick={() => openDialog(c)}>
                          <PenLine className="mr-2 h-4 w-4" />
                          编辑
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          className="text-destructive"
                          onClick={async () => {
                            if (window.confirm(`删除配置「${c.name}」？`)) {
                              await api.delete(`/api/ai/configs/${c.id}`);
                              load();
                            }
                          }}
                        >
                          <Trash2 className="mr-2 h-4 w-4" />
                          删除
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                ))}
              </div>
            )}
          </section>

          <Separator className="my-8" />

          {/* 修改密码 */}
          <section>
            <h2 className="mb-4 flex items-center gap-2 text-base font-semibold">
              <KeyRound className="h-4 w-4 text-primary" />
              修改密码
            </h2>
            <div className="max-w-sm space-y-3 rounded-lg border border-border bg-card p-5">
              <div className="space-y-2">
                <Label>原密码</Label>
                <Input
                  type="password"
                  value={pw.old_password}
                  onChange={(e) => setPw({ ...pw, old_password: e.target.value })}
                  autoComplete="current-password"
                />
              </div>
              <div className="space-y-2">
                <Label>新密码</Label>
                <Input
                  type="password"
                  value={pw.new_password}
                  onChange={(e) => setPw({ ...pw, new_password: e.target.value })}
                  autoComplete="new-password"
                />
              </div>
              <div className="space-y-2">
                <Label>确认新密码</Label>
                <Input
                  type="password"
                  value={pw.confirm}
                  onChange={(e) => setPw({ ...pw, confirm: e.target.value })}
                  autoComplete="new-password"
                />
              </div>
              <Button onClick={() => void changePassword()} disabled={pwSaving} className="mt-1">
                {pwSaving ? "提交中…" : "修改密码"}
              </Button>
            </div>
          </section>
        </div>
      </ScrollArea>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{editing ? "编辑 AI 配置" : "添加 AI 配置"}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            {!editing && (
              <div className="flex flex-wrap gap-2">
                {PRESETS.map((p) => (
                  <button
                    key={p.name}
                    type="button"
                    className="rounded-full border border-border px-3 py-1 text-xs text-muted-foreground transition-colors hover:border-primary hover:text-primary"
                    onClick={() => setForm({ ...form, name: p.name, base_url: p.base_url, model: p.model })}
                  >
                    {p.name}
                  </button>
                ))}
              </div>
            )}
            <div className="space-y-2">
              <Label>名称</Label>
              <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="如：DeepSeek" />
            </div>
            <div className="space-y-2">
              <Label>Base URL</Label>
              <Input
                value={form.base_url}
                onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                placeholder="https://api.deepseek.com"
                className="tnum"
              />
            </div>
            <div className="space-y-2">
              <Label>API Key{editing && "（留空则不修改）"}</Label>
              <Input
                type="password"
                value={form.api_key}
                onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                placeholder="sk-..."
                autoComplete="off"
              />
            </div>
            <div className="space-y-2">
              <Label>模型</Label>
              <Input value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} className="tnum" />
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={form.is_default}
                onChange={(e) => setForm({ ...form, is_default: e.target.checked })}
                className="h-4 w-4 accent-[#004eff]"
              />
              设为默认配置
            </label>
          </div>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button variant="outline" onClick={() => void test()} disabled={testing}>
              {testing ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Plug className="mr-1 h-4 w-4" />}
              测试连接
            </Button>
            <Button onClick={() => void save()}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppShell>
  );
}
