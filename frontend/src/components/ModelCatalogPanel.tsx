import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronUp, Loader2, Search } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import {
  buildTestPayload,
  dedupeModels,
  filterModels,
  testResultLabel,
  testResultTone,
  type ModelTestResult,
} from "@/lib/modelCatalog";

/** 成本闸：一次批量最多同时 3 个真实调用；改大等于放大计费风险。
 *  （移动端/弱网也别指望再放大——每一条都是对上游的真实计费调用。） */
export const BATCH_LIMIT_DEFAULT = 3;

/** 面板的凭据来源。两种形态与后端 `POST /api/ai/test` 的两种用法一一对应。 */
export interface CatalogSource {
  /** 已保存配置 id；null = 未保存（dry-run，必须自带 base_url + api_key） */
  configId: number | null;
  /** 表单或配置的 base_url */
  baseUrl: string;
  /** 该配置在 DB 里的 base_url（编辑态用，判断用户是否改过地址） */
  savedBaseUrl?: string | null;
  /** 用户新填的 key（可能为空） */
  apiKey: string;
  /** 配置里是否已有 key */
  hasSavedKey: boolean;
}

export interface ModelCatalogPanelProps {
  models: string[];
  source: CatalogSource;
  /** 当前选中的模型（高亮用） */
  selected?: string;
  /** 选用回调；可返回 Promise（如"设为默认"要落库后才算数） */
  onPick: (model: string) => void | Promise<void>;
  /** 选用按钮文案，默认「选用」 */
  pickLabel?: string;
  /** 批量并发上限，默认 BATCH_LIMIT_DEFAULT */
  batchLimit?: number;
  /** 初始是否展开，默认 true */
  autoExpand?: boolean;
  /** 批量测试运行状态变化（m8：宿主据此禁用「获取模型列表」「刷新清单」） */
  onBusyChange?: (busy: boolean) => void;
}

/** 模型清单面板：搜索 + 共 N 个 + 逐个测试 + 批量测试（可取消）+ 选用。
 *
 * 强烈建议读 M2 的三条不变量，它们决定了这个组件为什么有 `useRef`：
 * 1. **同步闸**：批量运行标记放在 ref 里，不用 state——`setState` 是异步的，
 *    同一 tick 连点两下「全部测试」会起两轮（78×2 条真实计费调用）。
 * 2. **进度不许复活**：只有 `bulkRun.current === 本轮标记` 时才 setState，
 *    关掉再打开不会看到「已测 6/78」这种幽灵进度。
 * 3. **关掉就停**：卸载/收起时置 cancelled，避免组件没了还按旧闭包继续发真实调用。
 */
export default function ModelCatalogPanel({
  models,
  source,
  selected,
  onPick,
  pickLabel = "选用",
  batchLimit = BATCH_LIMIT_DEFAULT,
  autoExpand = true,
  onBusyChange,
}: ModelCatalogPanelProps) {
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState(autoExpand);
  /** 结果按「作用域」存放：清单内容或凭据来源一变，旧结果自然失配、自动作废。
   *  为什么不用 useEffect 清空——在 effect 里同步 setState 会触发级联渲染（eslint 直接报错），
   *  而且"派生"比"清空"更难写错：不会漏掉某条路径。 */
  const [store, setStore] = useState<{ scope: string; map: Record<string, ModelTestResult> }>({
    scope: "",
    map: {},
  });
  /** 进度展示用；真正"是否在跑"由 bulkRun（ref）判定。 */
  const [bulk, setBulk] = useState<{ done: number; total: number } | null>(null);

  /** 本轮批量的同步闸。非空 = 正在跑。 */
  const bulkRun = useRef<{ cancelled: boolean } | null>(null);
  const busyCb = useRef(onBusyChange);

  // ref 只在 effect 里同步（render 期写 ref 被 react-hooks/refs 禁止）
  useEffect(() => {
    busyCb.current = onBusyChange;
  }, [onBusyChange]);

  const list = useMemo(() => dedupeModels(models), [models]);
  const shown = useMemo(() => filterModels(list, query), [list, query]);

  /** 结果作用域：清单内容 + 凭据来源。三者任一变化，旧结果就不再展示。 */
  const scopeKey = useMemo(
    () => [source.configId ?? "dryrun", source.baseUrl, list.join("\u0001")].join("\u0000"),
    [source.configId, source.baseUrl, list]
  );
  const testResults = store.scope === scopeKey ? store.map : {};

  const setResult = useCallback(
    (model: string, r: ModelTestResult) => {
      setStore((prev) => {
        const base = prev.scope === scopeKey ? prev.map : {};
        return { scope: scopeKey, map: { ...base, [model]: r } };
      });
    },
    [scopeKey]
  );

  /** 停批量：置 cancelled（异步循环下一批前就会 break），清掉同步闸与进度。
   *  `notify` 只在用户主动点「取消」时为 true——关窗/收起时弹一句"已取消"只是噪音。 */
  const stopBulk = useCallback((notify: boolean) => {
    const run = bulkRun.current;
    if (!run) return;
    run.cancelled = true;
    bulkRun.current = null;
    setBulk(null);
    busyCb.current?.(false);
    if (notify) toast.info("已取消，剩余未测");
  }, []);

  // 卸载即停：组件没了还继续发真实调用是最坏的一种浪费。
  // 同时必须把宿主的 busy 标记清掉——否则关掉对话框后 Account 永远以为"批量在跑"，
  // 「获取模型列表」会被 m8 的闸永久禁用（这是我写这一行时踩过的坑）。
  useEffect(() => {
    return () => {
      const run = bulkRun.current;
      if (run) {
        run.cancelled = true;
        bulkRun.current = null;
        busyCb.current?.(false);
      }
    };
  }, []);

  /** 单条真实调用；只返回结果，不碰 state（state 由调用方按闸写）。 */
  const callOne = useCallback(
    async (model: string): Promise<ModelTestResult> => {
      const built = buildTestPayload({
        model,
        savedId: source.configId,
        savedBaseUrl: source.savedBaseUrl,
        formBaseUrl: source.baseUrl,
        formApiKey: source.apiKey,
        hasSavedKey: source.hasSavedKey,
      });
      if (!built.payload) return { state: "fail", message: built.error ?? "测试参数不完整" };
      try {
        const r = await api.post<{ reply: string; model: string; latency_ms: number }>(
          "/api/ai/test",
          built.payload
        );
        return { state: "ok", ms: r.latency_ms, reply: r.reply };
      } catch (e) {
        // 后端已把失败分类成「病因 + 建议」，原样带给行内 title / 建议行
        return { state: "fail", message: (e as Error).message };
      }
    },
    [source.configId, source.savedBaseUrl, source.baseUrl, source.apiKey, source.hasSavedKey]
  );

  /** 逐行「测试」：参数不完整时**一个请求都不发**，直接 toast 说清缺什么。 */
  async function testOne(model: string) {
    if (bulkRun.current) {
      toast.info("请等批量测试结束或先取消");
      return;
    }
    const built = buildTestPayload({
      model,
      savedId: source.configId,
      savedBaseUrl: source.savedBaseUrl,
      formBaseUrl: source.baseUrl,
      formApiKey: source.apiKey,
      hasSavedKey: source.hasSavedKey,
    });
    if (!built.payload) {
      toast.error(built.error ?? "测试参数不完整");
      return;
    }
    setResult(model, { state: "testing" });
    setResult(model, await callOne(model));
  }

  /** 全部测试：先确认（真金白银），再按 batchLimit 串行推进，可随时取消。 */
  async function testAll() {
    if (bulkRun.current) {
      // 同步闸：同一 tick 双击也进不来
      toast.info("已经在测试中，先取消或等它跑完");
      return;
    }
    if (list.length === 0) return;
    // 先用第一条试组装，参数不全就整批不发（省得弹完 confirm 才发现没 key）
    const probe = buildTestPayload({
      model: list[0],
      savedId: source.configId,
      savedBaseUrl: source.savedBaseUrl,
      formBaseUrl: source.baseUrl,
      formApiKey: source.apiKey,
      hasSavedKey: source.hasSavedKey,
    });
    if (!probe.payload) {
      toast.error(probe.error ?? "测试参数不完整");
      return;
    }
    if (!window.confirm(`将对该端点的 ${list.length} 个模型各发一次真实调用（会产生费用），继续？`)) return;

    const run = { cancelled: false };
    bulkRun.current = run;
    setBulk({ done: 0, total: list.length });
    busyCb.current?.(true);

    const limit = Math.max(1, Math.min(batchLimit, BATCH_LIMIT_DEFAULT));
    let done = 0;
    try {
      for (let i = 0; i < list.length; i += limit) {
        if (run.cancelled || bulkRun.current !== run) break;
        const batch = list.slice(i, i + limit);
        const results = await Promise.all(batch.map((m) => callOne(m)));
        for (let k = 0; k < batch.length; k++) {
          // 只有本轮还是"当前轮"才写 state：关掉重开不会看到幽灵进度/旧结果
          if (bulkRun.current !== run) break;
          setResult(batch[k], results[k]);
        }
        if (bulkRun.current !== run) break;
        done = Math.min(i + batch.length, list.length);
        setBulk({ done, total: list.length });
      }
    } finally {
      if (bulkRun.current === run) {
        bulkRun.current = null;
        setBulk(null);
        busyCb.current?.(false);
      }
    }
  }

  /** 收起/展开：收起时停掉批量（面板看不见了还继续烧钱没有道理）。 */
  function toggleExpanded() {
    if (expanded) stopBulk(false);
    setExpanded(!expanded);
  }

  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs tnum text-muted-foreground">共 {list.length} 个模型</span>
        <div className="relative">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索模型…"
            className="h-7 w-36 pl-7 text-xs"
          />
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-7 px-2 text-xs tnum"
          onClick={() => void testAll()}
          disabled={bulk !== null}
        >
          {bulk ? `已测 ${bulk.done}/${bulk.total}` : "全部测试"}
        </Button>
        {bulk && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-xs text-destructive"
            onClick={() => stopBulk(true)}
          >
            取消
          </Button>
        )}
        <button
          type="button"
          className="ml-auto inline-flex items-center gap-0.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
          onClick={toggleExpanded}
        >
          {expanded ? (
            <>
              <ChevronUp className="h-3.5 w-3.5" />
              收起
            </>
          ) : (
            <>
              <ChevronDown className="h-3.5 w-3.5" />
              展开
            </>
          )}
        </button>
      </div>
      {expanded && (
        <div className="max-h-[45vh] overflow-y-auto rounded-md border border-input bg-muted/20">
          {shown.length === 0 ? (
            <p className="px-2 py-3 text-xs text-muted-foreground">没有匹配「{query}」的模型</p>
          ) : (
            shown.map((m) => {
              const result = testResults[m];
              const tone = testResultTone(result);
              return (
                <div
                  key={m}
                  className={
                    "flex items-center gap-2 border-b border-input/60 px-2 py-1 last:border-b-0 " +
                    (selected === m ? "bg-primary/5" : "")
                  }
                >
                  <span className="min-w-0 flex-1 truncate text-xs tnum" title={m}>
                    {m}
                  </span>
                  {tone !== "none" && (
                    <span
                      // 错误摘要有 60 字（中文≈600px），不夹住会把整行撑出对话框
                      className={"inline-block max-w-[45%] shrink-0 truncate text-[11px] " + testToneClass(tone)}
                      title={result?.state === "fail" ? result.message : undefined}
                    >
                      {tone === "testing" ? <Loader2 className="h-3 w-3 animate-spin" /> : testResultLabel(result)}
                    </span>
                  )}
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-6 shrink-0 px-1.5 text-[11px]"
                    onClick={() => void testOne(m)}
                    disabled={tone === "testing" || bulk !== null}
                  >
                    测试
                  </Button>
                  <Button
                    type="button"
                    variant={selected === m ? "secondary" : "outline"}
                    size="sm"
                    className="h-6 shrink-0 px-1.5 text-[11px]"
                    onClick={() => void Promise.resolve(onPick(m))}
                  >
                    {pickLabel}
                  </Button>
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}

/** 行内测试结果的配色（语气 → class）。ok 绿 / fail 红 / testing 灰。 */
function testToneClass(tone: ReturnType<typeof testResultTone>): string {
  switch (tone) {
    case "ok":
      return "text-green-600 dark:text-green-400";
    case "fail":
      return "text-destructive";
    case "testing":
      return "text-muted-foreground";
    default:
      return "";
  }
}
