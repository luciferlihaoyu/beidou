import { useCallback, useEffect, useMemo, useState } from "react";
import { DatabaseBackup, Download, KeyRound, Loader2, MoreHorizontal, PenLine, Plug, Plus, RefreshCw, Sparkles, Star, Trash2 } from "lucide-react";
import { toast } from "sonner";
import AppShell from "@/components/AppShell";
import { api, xuanjiApi, type AIConfig, type IntegrationState, type XuanjiTree } from "@/lib/api";
import { relTime } from "@/lib/utils";
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";

/** 反向同步的硬排除目录（与后端 ``xj_sync.XJ_EXCLUDED_FOLDER`` 同名）：
 *  它是**正向**设定集同步的落点，拉回来会自我循环，故界面置灰不可勾。 */
const XJ_EXCLUDED_FOLDER = "北斗小说资料";

/** 间隔下拉的常用档（与后端 5..1440 钳位一致；当前值不在档内时另补一行，绝不静默改值） */
const XJ_INTERVAL_CHOICES = [5, 10, 30, 60];

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
  const [testingId, setTestingId] = useState<number | null>(null);
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
    auto_backup_enabled: false,
    // 批F：反向同步三键。表单要编辑就**回传原值**（M1 的 Optional 约定允许省略，
    // 但这里始终显式带上，免得「改完文件夹又保存」把别的字段带成 None 语义）。
    xj_sync_enabled: false,
    xj_sync_folders: "",
    xj_sync_interval_min: 10,
  });
  const [integSaving, setIntegSaving] = useState(false);
  const [alistTesting, setAlistTesting] = useState(false);
  const [backingUp, setBackingUp] = useState(false);
  const [backupsOpen, setBackupsOpen] = useState(false);
  const [backups, setBackups] = useState<{ filename: string; size: number; modified_at: string }[]>([]);
  const [backupsLoading, setBackupsLoading] = useState(false);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [xuanjiTesting, setXuanjiTesting] = useState(false);
  // 批F：反向同步块 —— 目录树（勾选数据源）+ 两个忙碌态
  const [xjTree, setXjTree] = useState<XuanjiTree | null>(null);
  const [xjTreeLoading, setXjTreeLoading] = useState(false);
  const [xjSyncing, setXjSyncing] = useState(false);

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
          auto_backup_enabled: s.auto_backup_enabled ?? false,
          xj_sync_enabled: s.xj_sync_enabled ?? false,
          xj_sync_folders: s.xj_sync_folders ?? "",
          xj_sync_interval_min: s.xj_sync_interval_min ?? 10,
        });
      })
      .catch(() => {});
  }, []);
  useEffect(load, [load]);

  /** 拉璇玑目录树（勾选框唯一数据源）。读不通一律静默：块内已有「先配地址」的提示文案，
   *  进页面就弹一个红 toast 反而像报错。 */
  async function loadXjTree() {
    setXjTreeLoading(true);
    try {
      setXjTree(await xuanjiApi.tree());
    } catch {
      setXjTree(null);
    } finally {
      setXjTreeLoading(false);
    }
  }

  // 首屏静默预取一次：**不置 loading**（没配璇玑地址时转圈纯属噪声），
  // 且必须走 .then 回调而非在 effect 体内直接 setState（否则触发级联渲染告警）。
  useEffect(() => {
    let alive = true;
    xuanjiApi
      .tree()
      .then((t) => {
        if (alive) setXjTree(t);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  // 已勾选的白名单（名字集合；后端解析期兼容全角逗号，这里统一按半角回传）
  const xjSelected = useMemo(
    () => new Set(integForm.xj_sync_folders.split(/[,，]/).map((s) => s.trim()).filter(Boolean)),
    [integForm.xj_sync_folders]
  );

  // 每个文件夹的文档数：后端 tree 只平铺返回 documents，计数在本地按 folderId 聚合
  const xjDocCount = useMemo(() => {
    const m = new Map<number, number>();
    for (const d of xjTree?.documents ?? []) {
      if (d.folderId != null) m.set(d.folderId, (m.get(d.folderId) ?? 0) + 1);
    }
    return m;
  }, [xjTree]);

  /** 勾选/取消一个文件夹 → 逗号拼回 xj_sync_folders。
   *  顺序按树内顺序排列；**树里没有的旧勾选原样保留**（璇玑侧刚改名或树没拉到时，
   *  一次点选不该把白名单里别的目录悄悄清掉）。 */
  function toggleXjFolder(name: string) {
    const next = new Set(xjSelected);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    const treeNames = (xjTree?.folders ?? []).map((f) => f.name);
    const ordered = treeNames.filter((n) => next.has(n));
    const orphans = [...next].filter((n) => !ordered.includes(n));
    setIntegForm({ ...integForm, xj_sync_folders: [...ordered, ...orphans].join(",") });
  }

  /** 立即全量同步：阻塞式接口（后端持同步锁跑完一轮），完成才回包——故只做 loading，不设超时。 */
  async function syncNow() {
    setXjSyncing(true);
    try {
      const r = await xuanjiApi.syncNow();
      toast.success(
        `新增 ${r.added} · 更新 ${r.updated} · 跳过 ${r.skipped} · 回写 ${r.pushed} · ` +
          `待人工 ${r.conflicts} · 失败 ${r.failed + r.push_failed}`,
        // 六个计数挤一行，5 秒来不及看
        { duration: 12000 }
      );
      load(); // 刷新「上次同步」留痕
      await loadXjTree();
    } catch (e) {
      // 未配地址 / 未勾文件夹 → 400，detail 就是给人看的中文提示
      toast.error((e as Error).message);
    } finally {
      setXjSyncing(false);
    }
  }

  /** 间隔下拉档位：当前值不在常用档内就补一行，避免打开页面就静默复位成别的值 */
  const xjIntervalChoices = useMemo(
    () =>
      XJ_INTERVAL_CHOICES.includes(integForm.xj_sync_interval_min)
        ? XJ_INTERVAL_CHOICES
        : [...XJ_INTERVAL_CHOICES, integForm.xj_sync_interval_min].sort((a, b) => a - b),
    [integForm.xj_sync_interval_min]
  );

  /** 已勾选但当前树里查不到的目录（璇玑侧改名/删目录）：照实提示，别让它无声消失 */
  const xjOrphanFolders = useMemo(() => {
    const treeNames = new Set((xjTree?.folders ?? []).map((f) => f.name));
    return xjTree ? [...xjSelected].filter((n) => !treeNames.has(n)) : [];
  }, [xjTree, xjSelected]);

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
      // 备份后刷新备份列表
      if (backupsOpen) await loadBackups();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBackingUp(false);
    }
  }

  async function loadBackups() {
    setBackupsLoading(true);
    try {
      const r = await api.get<{ backups: { filename: string; size: number; modified_at: string }[] }>(
        "/api/integrations/alist/backups"
      );
      setBackups(r.backups);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBackupsLoading(false);
    }
  }

  async function openBackups() {
    setBackupsOpen(true);
    await loadBackups();
  }

  async function downloadBackup(filename: string) {
    setDownloading(filename);
    try {
      // 直接 fetch 拿 blob——走 a[download] 触发浏览器下载（不暴露 token）
      const token = localStorage.getItem("beidou_token");
      const resp = await fetch(
        `/api/integrations/alist/backups/${encodeURIComponent(filename)}/download`,
        { headers: token ? { Authorization: `Bearer ${token}` } : {} }
      );
      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        let msg = `下载失败 (${resp.status})`;
        try {
          const data = JSON.parse(text);
          if (typeof data.detail === "string") msg = data.detail;
        } catch {
          /* ignore */
        }
        throw new Error(msg);
      }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast.success(`已下载：${filename}`);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setDownloading(null);
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

  /** 列表里直接测某个已保存配置：不必打开编辑弹窗，改完就能验 */
  async function testSavedConfig(c: AIConfig) {
    setTestingId(c.id);
    const t = toast.loading(`正在测试「${c.name}」…`);
    try {
      const r = await api.post<{ reply: string; model: string; latency_ms: number }>(
        `/api/ai/configs/${c.id}/test`,
        {}
      );
      toast.success(
        `「${c.name}」连接成功：${r.model} · ${r.latency_ms} ms · 回复「${r.reply.slice(0, 12)}」`,
        { id: t }
      );
    } catch (e) {
      // 后端已把失败分类成「病因 + 建议」，这里原样呈现
      toast.error((e as Error).message, { id: t, duration: 12000 });
    } finally {
      setTestingId(null);
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
        const r = await api.post<{ reply: string; model: string; latency_ms: number }>(
          `/api/ai/configs/${editing.id}/test`,
          {}
        );
        toast.success(
          `连接成功（已保存的 Key）：${r.model} · ${r.latency_ms} ms · 回复「${r.reply.slice(0, 12)}」`
        );
        return;
      }
      const result = await api.post<{ reply: string; model: string; latency_ms: number }>("/api/ai/test", {
        base_url: form.base_url,
        api_key: key,
        model: form.model,
      });
      toast.success(`连接成功：${result.model} · ${result.latency_ms} ms · 回复「${result.reply.slice(0, 12)}」`);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setTesting(false);
    }
  }

  async function fetchModels(forConfigId?: number) {
    // 优先用 config_id 让后端从 DB 读 key（避免重复输入）；新配置或缺 key 时退回 base_url+api_key
    const key = form.api_key.trim();
    const useConfigId = forConfigId ?? (editing?.id ?? null);
    if (!useConfigId && !key) {
      return toast.error("请先填写 API Key，或在编辑现有配置时由系统自动读取");
    }
    setModelsLoading(true);
    try {
      const payload: { base_url?: string; api_key?: string; config_id?: number } = {};
      if (useConfigId) {
        payload.config_id = useConfigId;
        // 编辑现有配置时允许用户用新 key 覆盖；新建配置时必传
        if (key) {
          payload.api_key = key;
        }
      } else {
        payload.base_url = form.base_url;
        payload.api_key = key;
      }
      const r = await api.post<{ models: string[] }>("/api/ai/models", payload);
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

  async function fetchModelsForConfig(c: AIConfig) {
    // 在不打开编辑弹窗的情况下预览该配置的可用模型（直接传 config_id，DB 里有 key）
    if (!c.has_key) return toast.error("该配置还没有 Key，请先编辑填写");
    setModelsLoading(true);
    try {
      const r = await api.post<{ models: string[] }>("/api/ai/models", { config_id: c.id });
      if (r.models.length === 0) {
        return toast.info(`「${c.name}」接口没有返回模型列表`);
      }
      toast.success(`「${c.name}」共 ${r.models.length} 个模型：${r.models.slice(0, 6).join("、")}${r.models.length > 6 ? " …" : ""}`);
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
                        <DropdownMenuItem onClick={() => void fetchModelsForConfig(c)}>
                          <Sparkles className="mr-2 h-4 w-4" />
                          获取可用模型
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          disabled={!c.has_key || testingId === c.id}
                          title={c.has_key ? "用已保存的 Key 发一次最小请求" : "该配置还没有保存 Key"}
                          onClick={() => void testSavedConfig(c)}
                        >
                          {testingId === c.id ? (
                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                          ) : (
                            <Plug className="mr-2 h-4 w-4" />
                          )}
                          测试连接
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
                AList 通过原生接口连接（无需开启 WebDAV），用于数据备份、文本上传与封面图存储；璇玑为个人知识库，配置后可在资料库中浏览并导入文档
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
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
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
                    placeholder="/（账户基本路径）或留空"
                    className="tnum"
                  />
                  <p className="text-xs text-muted-foreground">
                    留空 = 直接用该 AList 账户的基本路径；填子目录（如 /novels）则在其下再加一层
                    三个子目录 backup / uploads / covers 都会建到这里
                  </p>
                </div>
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="accent-primary"
                    checked={integForm.auto_backup_enabled}
                    onChange={(e) => setIntegForm({ ...integForm, auto_backup_enabled: e.target.checked })}
                  />
                  每日自动备份到 AList
                  {integ?.last_backup_at && (
                    <span className="text-xs text-muted-foreground">（最近备份：{integ.last_backup_at}）</span>
                  )}
                </label>
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
                  <Button variant="outline" size="sm" onClick={() => void openBackups()}>
                    <Download className="mr-1 h-3.5 w-3.5" />
                    查看备份
                  </Button>
                </div>
              </div>

              {/* 璇玑 */}
              <div className="space-y-3 rounded-lg border border-border bg-card p-5">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-medium">璇玑知识库</h3>
                  <Badge variant="outline" className="border-primary/40 text-primary">
                    可读取导入
                  </Badge>
                </div>
                <div className="space-y-2">
                  <Label>璇玑地址</Label>
                  <Input
                    value={integForm.xuanji_url}
                    onChange={(e) => setIntegForm({ ...integForm, xuanji_url: e.target.value })}
                    placeholder="https://xuanjj29.zeabur.app"
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
                    在璇玑后台「智能助手 / Agent」中创建 API Key（含读取权限）。配置后即可在资料库页「从璇玑导入」浏览并导入文档
                  </p>
                </div>
                <div className="flex gap-2 pt-1">
                  <Button variant="outline" size="sm" onClick={() => void testXuanji()} disabled={xuanjiTesting}>
                    {xuanjiTesting ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : <Plug className="mr-1 h-3.5 w-3.5" />}
                    测试连接
                  </Button>
                </div>

                {/* 批F：资料库反向同步（M2 出参接入本页） */}
                <Separator />
                <div className="space-y-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h4 className="text-sm font-medium">资料库反向同步</h4>
                      <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
                        勾选的璇玑文件夹将镜像进公共资料库；璇玑更新会覆盖镜像条目正文（本地改名不受影响）；
                        你在北斗改过的内容自动回写璇玑；两边都改过的条目标冲突待你裁决。
                      </p>
                    </div>
                    <label className="flex shrink-0 items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        className="accent-primary"
                        checked={integForm.xj_sync_enabled}
                        onChange={(e) => setIntegForm({ ...integForm, xj_sync_enabled: e.target.checked })}
                      />
                      启用
                    </label>
                  </div>

                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <Label>同步文件夹（白名单）</Label>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 px-2 text-xs"
                        onClick={() => void loadXjTree()}
                        disabled={xjTreeLoading}
                      >
                        {xjTreeLoading ? (
                          <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <RefreshCw className="mr-1 h-3.5 w-3.5" />
                        )}
                        拉取文件夹
                      </Button>
                    </div>
                    {xjTree ? (
                      <div className="max-h-56 space-y-0.5 overflow-y-auto rounded-md border border-border p-1.5">
                        {xjTree.folders.length === 0 && (
                          <p className="px-1 py-2 text-xs text-muted-foreground">璇玑里还没有文件夹</p>
                        )}
                        {xjTree.folders.map((f) => {
                          const isSink = f.name === XJ_EXCLUDED_FOLDER;
                          return (
                            <label
                              key={f.id}
                              className={`flex items-center gap-2 rounded px-1.5 py-1 text-sm ${
                                isSink ? "cursor-not-allowed opacity-50" : "cursor-pointer hover:bg-muted"
                              }`}
                            >
                              <input
                                type="checkbox"
                                className="accent-primary"
                                disabled={isSink}
                                checked={!isSink && xjSelected.has(f.name)}
                                onChange={() => toggleXjFolder(f.name)}
                              />
                              <span className="truncate">{f.name}</span>
                              <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                                {isSink ? "设定集落点，不参与" : `${xjDocCount.get(f.id) ?? 0} 篇`}
                              </span>
                            </label>
                          );
                        })}
                      </div>
                    ) : (
                      <p className="rounded-md border border-dashed border-border px-2 py-3 text-xs text-muted-foreground">
                        拉不到璇玑文件夹列表——请先填好上面的「璇玑地址 / API Key」并保存，再点「拉取文件夹」
                      </p>
                    )}
                    {xjOrphanFolders.length > 0 && (
                      <p className="text-xs text-muted-foreground">
                        白名单里还有璇玑当前树中查不到的目录：{xjOrphanFolders.join("、")}
                        （可能已改名或删除，原样保留不动）
                      </p>
                    )}
                  </div>

                  <div className="flex flex-wrap items-end gap-3">
                    <div className="space-y-2">
                      <Label>自动同步间隔</Label>
                      <Select
                        value={String(integForm.xj_sync_interval_min)}
                        onValueChange={(v) => setIntegForm({ ...integForm, xj_sync_interval_min: Number(v) })}
                      >
                        <SelectTrigger className="h-8 w-28 text-xs">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {xjIntervalChoices.map((m) => (
                            <SelectItem key={m} value={String(m)}>
                              {m} 分钟
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <Button variant="outline" size="sm" onClick={() => void syncNow()} disabled={xjSyncing}>
                      {xjSyncing && <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />}
                      {xjSyncing ? "同步中…" : "⟳ 立即全量同步"}
                    </Button>
                    <span className="pb-1.5 text-xs text-muted-foreground">
                      上次同步：{relTime(integ?.xj_sync_last_at) ?? "未同步"}
                    </span>
                  </div>
                  {xjSyncing && (
                    <p className="text-xs text-muted-foreground">
                      正在跑全量对账（入方向拉取 + 待回写批量兜底），要等这一轮跑完才出结果，别关页面。
                    </p>
                  )}
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
              {models.length > 0 ? (
                <div className="max-h-32 overflow-y-auto rounded border border-input bg-muted/30 p-1">
                  {models.map((m) => (
                    <button
                      type="button"
                      key={m}
                      onClick={() => setForm({ ...form, model: m })}
                      className={
                        "block w-full truncate rounded px-2 py-1 text-left text-xs tnum transition-colors hover:bg-muted " +
                        (form.model === m ? "bg-primary/10 font-medium text-primary" : "text-foreground/80")
                      }
                      title={m}
                    >
                      {m}
                    </button>
                  ))}
                </div>
              ) : (
                <p className="text-[11px] text-muted-foreground">
                  点「获取模型列表」从 {form.base_url || "接口"} 拉取可选项，或直接手填模型名
                </p>
              )}
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

      {/* AList 备份列表：从 AList 远程拉备份 zip 到本地下载（手动还原） */}
      <Dialog open={backupsOpen} onOpenChange={setBackupsOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>AList 备份</DialogTitle>
            <p className="text-xs text-muted-foreground">
              从 AList {integ?.alist_root || "/"} 下的 backup/ 目录列出。点击「下载」拿到本地后需手动停服、
              替换 beidou.db、重启服务（避免运行中覆盖数据库导致损坏）。
            </p>
          </DialogHeader>
          {backupsLoading ? (
            <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              读取中…
            </div>
          ) : backups.length === 0 ? (
            <div className="rounded-lg border border-dashed border-input py-10 text-center text-sm text-muted-foreground">
              还没有备份，点「立即备份」生成一个
            </div>
          ) : (
            <ul className="max-h-80 divide-y divide-border overflow-y-auto">
              {backups.map((b) => (
                <li key={b.filename} className="flex items-center gap-3 py-2.5">
                  <DatabaseBackup className="h-4 w-4 shrink-0 text-primary" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium tnum">{b.filename}</p>
                    <p className="mt-0.5 text-[11px] text-muted-foreground tnum">
                      {(b.size / 1024).toFixed(0)} KB · {b.modified_at.replace("T", " ").replace(/\..*$/, "")}
                    </p>
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={downloading === b.filename}
                    onClick={() => void downloadBackup(b.filename)}
                    className="shrink-0"
                  >
                    {downloading === b.filename ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Download className="mr-1 h-3.5 w-3.5" />
                    )}
                    下载
                  </Button>
                </li>
              ))}
            </ul>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => void loadBackups()} disabled={backupsLoading}>
              <Loader2 className={"mr-1 h-3.5 w-3.5 " + (backupsLoading ? "animate-spin" : "")} />
              刷新
            </Button>
            <Button variant="ghost" onClick={() => setBackupsOpen(false)}>
              关闭
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppShell>
  );
}
