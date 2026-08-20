import { useCallback, useEffect, useState } from "react";
import { DatabaseBackup, KeyRound, Loader2, MoreHorizontal, PenLine, Plug, Plus, Star, Trash2 } from "lucide-react";
import { toast } from "sonner";
import AppShell from "@/components/AppShell";
import { api, type AIConfig, type IntegrationState } from "@/lib/api";
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
  const [models, setModels] = useState<string[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [pw, setPw] = useState({ old_password: "", new_password: "", confirm: "" });
  const [pwSaving, setPwSaving] = useState(false);
  const [integ, setInteg] = useState<IntegrationState | null>(null);
  const [integForm, setIntegForm] = useState({
    alist_url: "",
    alist_username: "",
    alist_password: "",
    alist_root: "/beidou",
    xuanji_url: "",
    xuanji_api_key: "",
  });
  const [integSaving, setIntegSaving] = useState(false);
  const [alistTesting, setAlistTesting] = useState(false);
  const [backingUp, setBackingUp] = useState(false);
  const [xuanjiTesting, setXuanjiTesting] = useState(false);

  const load = useCallback(() => {
    api.get<AIConfig[]>("/api/ai/configs").then(setConfigs).catch((e) => toast.error(e.message));
    api
      .get<IntegrationState>("/api/integrations")
      .then((s) => {
        setInteg(s);
        setIntegForm({
          alist_url: s.alist_url,
          alist_username: s.alist_username,
          alist_password: "",
          alist_root: s.alist_root,
          xuanji_url: s.xuanji_url,
          xuanji_api_key: "",
        });
      })
      .catch(() => {});
  }, []);
  useEffect(load, [load]);

  async function saveIntegration() {
    setIntegSaving(true);
    try {
      await api.put("/api/integrations", integForm);
      toast.success("集成配置已保存");
      load();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setIntegSaving(false);
    }
  }

  async function testAlist() {
    setAlistTesting(true);
    try {
      await api.put("/api/integrations", integForm);
      const r = await api.post<{ message: string }>("/api/integrations/alist/test");
      toast.success(r.message);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setAlistTesting(false);
    }
  }

  async function backupNow() {
    setBackingUp(true);
    try {
      const r = await api.post<{ path: string; size: number }>("/api/integrations/alist/backup");
      toast.success(`备份完成：${r.path}（${(r.size / 1024).toFixed(0)} KB）`);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBackingUp(false);
    }
  }

  async function testXuanji() {
    setXuanjiTesting(true);
    try {
      await api.put("/api/integrations", integForm);
      const r = await api.post<{ message: string }>("/api/integrations/xuanji/test");
      toast.success(r.message);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setXuanjiTesting(false);
    }
  }

  function openDialog(item: AIConfig | null) {
    setEditing(item);
    setModels([]);
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
    setTesting(true);
    try {
      if (!key) {
        // 编辑已有配置且未重填 Key：直接用服务端保存的 Key 测试
        if (!editing) return toast.error("请先填写 API Key");
        if (!editing.has_key) return toast.error("该配置尚未保存 Key，请先填写");
        const r = await api.post<{ reply: string }>(`/api/ai/configs/${editing.id}/test`, {});
        toast.success(`连接成功（使用已保存的 Key）：${r.reply.slice(0, 30)}`);
        return;
      }
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

  async function fetchModels() {
    const key = form.api_key.trim();
    if (!key) {
      if (editing?.has_key) return toast.info("拉取模型列表需要重新输入一次 API Key（服务端不回传 Key）");
      return toast.error("请先填写 API Key");
    }
    setModelsLoading(true);
    try {
      const r = await api.post<{ models: string[] }>("/api/ai/models", {
        base_url: form.base_url,
        api_key: key,
      });
      if (r.models.length === 0) {
        toast.info("接口没有返回模型列表，请手动填写模型名");
      } else {
        setModels(r.models);
        toast.success(`获取到 ${r.models.length} 个模型，点模型输入框选择`);
      }
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setModelsLoading(false);
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

          {/* 存储与集成 */}
          <section>
            <div className="mb-4">
              <h2 className="flex items-center gap-2 text-base font-semibold">
                <DatabaseBackup className="h-4 w-4 text-primary" />
                存储与集成
              </h2>
              <p className="mt-0.5 text-xs text-muted-foreground">
                AList 用于数据备份、文本上传与封面图存储；璇玑为个人知识库（同步功能即将开通）
              </p>
            </div>

            <div className="grid gap-6 lg:grid-cols-2">
              {/* AList */}
              <div className="space-y-3 rounded-lg border border-border bg-card p-5">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-medium">AList 存储</h3>
                  {integ?.has_alist_password && integ.alist_url && (
                    <Badge variant="secondary">已配置</Badge>
                  )}
                </div>
                <div className="space-y-2">
                  <Label>AList 地址</Label>
                  <Input
                    value={integForm.alist_url}
                    onChange={(e) => setIntegForm({ ...integForm, alist_url: e.target.value })}
                    placeholder="https://alist.example.com"
                    className="tnum"
                  />
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div className="space-y-2">
                    <Label>账号</Label>
                    <Input
                      value={integForm.alist_username}
                      onChange={(e) => setIntegForm({ ...integForm, alist_username: e.target.value })}
                      placeholder="beidou"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>密码{integ?.has_alist_password && "（留空不修改）"}</Label>
                    <Input
                      type="password"
                      value={integForm.alist_password}
                      onChange={(e) => setIntegForm({ ...integForm, alist_password: e.target.value })}
                      placeholder="••••••"
                      autoComplete="off"
                    />
                  </div>
                </div>
                <div className="space-y-2">
                  <Label>根目录</Label>
                  <Input
                    value={integForm.alist_root}
                    onChange={(e) => setIntegForm({ ...integForm, alist_root: e.target.value })}
                    placeholder="/beidou"
                    className="tnum"
                  />
                  <p className="text-xs text-muted-foreground">
                    会自动创建 backup / uploads / covers 三个子目录
                  </p>
                </div>
                <div className="flex gap-2 pt-1">
                  <Button variant="outline" size="sm" onClick={() => void testAlist()} disabled={alistTesting}>
                    {alistTesting ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : <Plug className="mr-1 h-3.5 w-3.5" />}
                    测试连接
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => void backupNow()} disabled={backingUp}>
                    {backingUp ? (
                      <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <DatabaseBackup className="mr-1 h-3.5 w-3.5" />
                    )}
                    立即备份
                  </Button>
                </div>
              </div>

              {/* 璇玑 */}
              <div className="space-y-3 rounded-lg border border-border bg-card p-5">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-medium">璇玑知识库</h3>
                  <Badge variant="outline" className="text-muted-foreground">
                    同步即将开通
                  </Badge>
                </div>
                <div className="space-y-2">
                  <Label>璇玑地址</Label>
                  <Input
                    value={integForm.xuanji_url}
                    onChange={(e) => setIntegForm({ ...integForm, xuanji_url: e.target.value })}
                    placeholder="https://xuanji.example.com"
                    className="tnum"
                  />
                </div>
                <div className="space-y-2">
                  <Label>API Key{integ?.has_xuanji_key && "（留空不修改）"}</Label>
                  <Input
                    type="password"
                    value={integForm.xuanji_api_key}
                    onChange={(e) => setIntegForm({ ...integForm, xuanji_api_key: e.target.value })}
                    placeholder="在璇玑中创建 API Key"
                    autoComplete="off"
                  />
                  <p className="text-xs text-muted-foreground">
                    开通后可将璇玑的知识条目同步到公共资料库
                  </p>
                </div>
                <div className="flex gap-2 pt-1">
                  <Button variant="outline" size="sm" onClick={() => void testXuanji()} disabled={xuanjiTesting}>
                    {xuanjiTesting ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : <Plug className="mr-1 h-3.5 w-3.5" />}
                    测试连接
                  </Button>
                </div>
              </div>
            </div>

            <div className="mt-4 flex justify-end">
              <Button size="sm" onClick={() => void saveIntegration()} disabled={integSaving}>
                {integSaving ? "保存中…" : "保存集成配置"}
              </Button>
            </div>
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
              <div className="flex gap-2">
                <Input
                  list="model-options"
                  value={form.model}
                  onChange={(e) => setForm({ ...form, model: e.target.value })}
                  className="tnum"
                  placeholder="点右侧按钮拉取可选项，或直接填写"
                />
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="shrink-0"
                  onClick={() => void fetchModels()}
                  disabled={modelsLoading}
                >
                  {modelsLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : "获取模型列表"}
                </Button>
              </div>
              <datalist id="model-options">
                {models.map((m) => (
                  <option key={m} value={m} />
                ))}
              </datalist>
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
