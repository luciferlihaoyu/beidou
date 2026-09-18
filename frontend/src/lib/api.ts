const TOKEN_KEY = "beidou_token";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  };
  if (options.body) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const resp = await fetch(path, { ...options, headers });
  if (!resp.ok) {
    let message = `请求失败 (${resp.status})`;
    try {
      const data = await resp.json();
      if (typeof data.detail === "string") message = data.detail;
    } catch {
      /* ignore */
    }
    if (resp.status === 401) setToken(null);
    throw new ApiError(resp.status, message);
  }
  return resp.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  /** multipart 文件上传（不设置 Content-Type，交给浏览器生成 boundary） */
  upload: <T>(path: string, files: File[]) => {
    const form = new FormData();
    for (const f of files) form.append("files", f);
    const token = getToken();
    return fetch(path, {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form,
    }).then(async (resp) => {
      if (!resp.ok) {
        let message = `上传失败 (${resp.status})`;
        try {
          const data = await resp.json();
          if (typeof data.detail === "string") message = data.detail;
        } catch {
          /* ignore */
        }
        throw new ApiError(resp.status, message);
      }
      return resp.json() as Promise<T>;
    });
  },
};

/** SSE 流式请求（POST），逐段回调文本。 */
export async function streamPost(
  path: string,
  body: unknown,
  onChunk: (text: string) => void,
  signal?: AbortSignal
): Promise<void> {
  const token = getToken();
  const resp = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok || !resp.body) {
    let message = `请求失败 (${resp.status})`;
    try {
      const data = await resp.json();
      if (typeof data.detail === "string") message = data.detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(resp.status, message);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const event of events) {
      const line = event.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      try {
        const payload = JSON.parse(line.slice(5).trim());
        if (payload.error) throw new ApiError(500, payload.error);
        if (payload.content) onChunk(payload.content);
      } catch (e) {
        if (e instanceof ApiError) throw e;
      }
    }
  }
}

// ---------- 类型 ----------

export interface User {
  id: number;
  username: string;
  role: string;
}

export interface Novel {
  id: number;
  title: string;
  author: string;
  description: string;
  genre: string;
  status: string;
  cover_color: string;
  daily_goal: number;
  chapter_count: number;
  total_words: number;
  updated_at: string;
  /** AI 工厂项目 id（非空 = AI 工厂生成的小说） */
  ai_project_id?: number | null;
}

export interface DailyStat {
  date: string; // YYYY-MM-DD
  words: number;
}

export interface SearchResult {
  chapter_id: number;
  display_title: string;
  count: number;
}

export type ChapterStatus = "draft" | "writing" | "done";

/** 中英双口径字数：中文（汉字/假名/谚文字符数）、English（英文单词数）、总计（去空白所有字符）。
 * 与后端 deps.count_words_split 口径一致。 */
export interface WordCountSplit {
  cjk: number;
  en: number;
  total: number;
}

export interface Chapter {
  id: number;
  volume_id: number | null;
  number: number;
  title: string;
  display_title: string;
  sort_order: number;
  word_count: number;
  word_count_split?: WordCountSplit;  // 后端 _out 实时计算，老数据可缺失
  updated_at: string;
  status: ChapterStatus;
  tags: string[];
  content?: string;
}

export interface Volume {
  id: number;
  title: string;
  sort_order: number;
  chapter_count: number;
  word_count: number;
}

export interface Character {
  id: number;
  name: string;
  role: string;
  tags: string;
  description: string;
  relations: string;
}

/** 人物关系（C 级：人物关系图）。from_name/to_name 为后端联表带出的角色名 */
export interface CharacterRelation {
  id: number;
  from_character_id: number;
  to_character_id: number;
  from_name: string;
  to_name: string;
  relation: string;
  description: string;
  source: string; // manual / ai
}

export interface WorldviewEntry {
  id: number;
  category: string;
  title: string;
  content: string;
}

export interface Foreshadowing {
  id: number;
  title: string;
  content: string;
  status: string;
}

export interface OutlineNode {
  id: number;
  parent_id: number | null;
  title: string;
  content: string;
  sort_order: number;
}

/** 常用词（写作用语库）：人名/地名/招式/法宝/口头禅等，编辑器一键插入 */
export interface NovelTerm {
  id: number;
  category: string;
  name: string;
  note: string;
}

export interface NovelTermIn {
  category: string;
  name: string;
  note?: string;
}

export const termsApi = {
  list: (novelId: number, category?: string) =>
    api.get<NovelTerm[]>(
      `/api/novels/${novelId}/settings/terms${category ? `?category=${encodeURIComponent(category)}` : ""}`
    ),
  create: (novelId: number, data: NovelTermIn) =>
    api.post<NovelTerm>(`/api/novels/${novelId}/settings/terms`, data),
  update: (novelId: number, id: number, data: NovelTermIn) =>
    api.put<NovelTerm>(`/api/novels/${novelId}/settings/terms/${id}`, data),
  remove: (novelId: number, id: number) =>
    api.delete<{ ok: boolean }>(`/api/novels/${novelId}/settings/terms/${id}`),
};

export interface AIConfig {
  id: number;
  name: string;
  base_url: string;
  model: string;
  is_default: boolean;
  has_key: boolean;
}

/** 技能卡包内的参考文件（references/assets 会注入 prompt，scripts 在当前环境不可执行） */
export interface SkillDoc {
  rel: string;
  title: string;
  kind: "references" | "assets" | "scripts";
  chars: number;
  executable: boolean | null;
}

export interface SkillCard {
  slug: string;
  name: string;
  category: "create" | "check";
  category_label: string;
  brief: string;
  description: string;
  docs?: SkillDoc[];
  /** 卡手册里声明「必须加载」的文件，用户不能取消 */
  core_docs?: string[];
}

export interface LibraryFolder {
  id: number;
  novel_id: number | null;
  parent_id: number | null;
  name: string;
  sort_order: number;
}

export interface LibraryItem {
  id: number;
  novel_id: number | null;
  folder_id: number | null;
  title: string;
  content: string;
  tags: string;
  summary: string;
  source: string;
  created_at: string;
  updated_at: string;
  /** B2 全文搜索命中摘要（含 <mark> 高亮），仅 /api/library/search 返回 */
  snippet?: string;
}

export interface OrganizeSuggestion {
  summary: string;
  tags: string[];
  suggested_folder: string;
  reason: string;
}

export interface IntegrationState {
  alist_url: string;
  alist_username: string;
  alist_root: string;
  has_alist_password: boolean;
  xuanji_url: string;
  has_xuanji_key: boolean;
  auto_backup_enabled: boolean;
  last_backup_at: string;
}

// ---------- 章节快照 ----------

/** 列表端：不含 content；详情端在此基础上加 content 字段（见 SnapshotDetail） */
export interface Snapshot {
  id: number;
  created_at: string;
  label: string;
  trigger: "auto" | "manual" | "pre_rollback" | "ai_rewrite";
  word_count: number;
  content_hash: string;
}

export interface SnapshotDetail extends Snapshot {
  content: string;
}

export interface SnapshotRestoreResult {
  ok: boolean;
  pre_rollback_id: number;
  restored_snapshot: SnapshotDetail;
}

/** diff 端点：a 为快照 id，b 传 `current` 表示当前章节正文 */
export interface SnapshotDiffResult {
  html: string;
}

// ---------- 章节 API 助手 ----------

/** listChapters 查询参数：status 精确匹配，tag 数组表示「任一命中」并集 */
export interface ListChaptersQuery {
  status?: ChapterStatus;
  tag?: string[];
}

/**
 * 拉取某小说的全部章节。
 * - status 过滤：单值精确匹配
 * - tag 过滤：多值并集（任一命中），FastAPI 多值参数通过 `?tag=a&tag=b` 重复键传
 */
export function listChapters(
  novelId: number,
  query: ListChaptersQuery = {}
): Promise<Chapter[]> {
  const params = new URLSearchParams();
  if (query.status) params.set("status", query.status);
  if (query.tag && query.tag.length > 0) {
    // URLSearchParams.append 重复键生成 ?tag=a&tag=b，与 FastAPI list[str] 对齐
    for (const t of query.tag) params.append("tag", t);
  }
  const qs = params.toString();
  const path = `/api/novels/${novelId}/chapters${qs ? `?${qs}` : ""}`;
  return api.get<Chapter[]>(path);
}

/** updateChapter 可选字段：未指定则服务端不修改该字段 */
export interface ChapterUpdate {
  title?: string;
  content?: string;
  volume_id?: number | null;
  status?: ChapterStatus;
  tags?: string[];
}

/** 更新章节元数据或正文。返回服务端落盘后的完整章节对象。 */
export function updateChapter(
  novelId: number,
  chapterId: number,
  data: ChapterUpdate
): Promise<Chapter> {
  return api.put<Chapter>(`/api/novels/${novelId}/chapters/${chapterId}`, data);
}

// ---------- AI 工厂（M1） ----------

export interface AiBookSpec {
  titles?: string[];
  genre?: string;
  time?: string;
  place?: string;
  theme?: string;
  tone?: string;
  pov?: string;
  characters?: { name: string; role: string; brief: string }[];
  premise?: string;
}

export interface AiOutlineChapter {
  title: string;
  outline: string;
}

export interface AiOutlineVolume {
  title: string;
  summary: string;
  chapters: AiOutlineChapter[];
}

export interface AiProject {
  id: number;
  novel_id: number | null;
  novel_title: string | null;
  status: string; // draft|setup|outline|writing|reviewing|done|failed
  seed_prompt: string;
  book_spec: AiBookSpec | null;
  genre: string;
  style_notes: string;
  target_total_words: number | null;
  target_volume_words: number | null;
  target_chapter_words: number | null;
  target_volumes: number | null;
  target_chapters: number | null;
  outline: { volumes?: AiOutlineVolume[] } | null;
  global_summary: string;
  auto_mode: boolean;
  setup_llm: string | null;
  outline_llm: string | null;
  chapter_llm: string | null;
  summary_llm: string | null;
  review_llm: string | null;
  author_intent: string;
  current_focus: string;
  particle_ledger: string;
  subplot_board: string;
  synopsis: AiSynopsis | null;
  reference: ReferenceNote | null;
  /** 拆书断点续传：上传的原文（裁剪后）+ 书名提示 + 未保存的范式草稿。
   *  reference 仍是「已确认挂载」的口径，草稿只在保存前存在。 */
  deconstruct_text: string;
  deconstruct_hint: string;
  deconstruct_draft: ReferenceNote | null;
  market: AiMarketReport | null;
  cover_prompt: AiCoverPrompt | null;
  kb_query: string;
  platform: string;
  custom_words: string;
  tokens_prompt: number;
  tokens_completion: number;
  nightly_enabled: boolean;
  nightly_chapters: number;
  nightly_last_run: {
    date: string;
    done: number;
    total: number;
    chapters: { title: string; words: number; deai_score: number }[];
    errors: { title?: string; error: string }[];
  } | null;
  chapter_count: number;
  created_at: string;
  updated_at: string;
}

export interface AiProjectCreate {
  seed_prompt: string;
  genre?: string;
  style_notes?: string;
  target_total_words?: number | null;
  target_volumes?: number | null;
  target_chapters?: number | null;
  target_volume_words?: number | null;
  target_chapter_words?: number | null;
}

export const aiFactoryApi = {
  list: () => api.get<AiProject[]>("/api/ai-factory/projects"),
  get: (id: number) => api.get<AiProject>(`/api/ai-factory/projects/${id}`),
  create: (data: AiProjectCreate) => api.post<AiProject>("/api/ai-factory/projects", data),
  remove: (id: number, deleteNovel = false) =>
    api.delete<{ ok: boolean }>(`/api/ai-factory/projects/${id}${deleteNovel ? "?delete_novel=true" : ""}`),
  saveBookSpecDraft: (id: number, bookSpec: Record<string, unknown>, title: string) =>
    api.put<{ ok: boolean; saved_at: string }>(`/api/ai-factory/projects/${id}/book-spec-draft`, {
      book_spec: bookSpec,
      title,
    }),
  init: (id: number, existingSpec?: Record<string, unknown>) =>
    api.post<AiProject>(`/api/ai-factory/projects/${id}/init`, existingSpec ? { existing_spec: existingSpec } : {}),
  confirmBookSpec: (id: number, title: string, bookSpec: AiBookSpec) =>
    api.put<AiProject>(`/api/ai-factory/projects/${id}/book-spec`, { title, book_spec: bookSpec }),
  setup: (id: number) => api.post<AiProject>(`/api/ai-factory/projects/${id}/setup`),
  outline: (id: number) => api.post<AiProject>(`/api/ai-factory/projects/${id}/outline`),
};

// ---------- AI 工厂 M2：逐章生成 ----------

export interface AiChapterJob {
  id: number;
  chapter_id: number | null;
  chapter_title: string;
  status: string; // pending|writing|reviewing|needs_fix|done|failed
  outline: string;
  actual_words: number;
  attempt: number;
  review_issues: { type?: string; severity?: string; issue?: string; suggestion?: string }[] | null;
  review_score: number | null;
  summary: string;
  finished_at: string | null;
  last_error?: string;
  last_error_code?: string;
  /** 停在「生成中」过久（停止/关页面/重启导致），需要解锁 */
  stuck?: boolean;
  stuck_minutes?: number;
}

export const aiFactoryM2 = {
  jobs: (projectId: number) => api.get<AiChapterJob[]>(`/api/ai-factory/projects/${projectId}/jobs`),
  finalize: (projectId: number, jobId: number, contentText: string) =>
    api.post<{ ok: boolean; word_count: number; state_updated: boolean }>(
      `/api/ai-factory/projects/${projectId}/jobs/${jobId}/finalize`,
      { content_text: contentText }
    ),
  review: (projectId: number, jobId: number) =>
    api.post<{ issues: NonNullable<AiChapterJob["review_issues"]>; has_high: boolean }>(
      `/api/ai-factory/projects/${projectId}/jobs/${jobId}/review`
    ),
  updateProject: (projectId: number, data: Record<string, unknown>) =>
    api.put<AiProject>(`/api/ai-factory/projects/${projectId}`, data),
};

// ---------- AI 工厂 M3：批量连跑 / 增强审校 / 追读力 ----------

export interface RetentionDashboard {
  chapters_done: number;
  hooks: { job_id: number; type?: string; desc?: string }[];
  cool_points: { job_id: number; type?: string; desc?: string }[];
  hook_score: number;
  cool_score: number;
  retention_score: number;
}

export type BatchEvent =
  | { event: "start"; total: number }
  | { event: "chapter_start"; job_id: number; title: string; index: number; total: number }
  | { event: "content"; job_id: number; text: string }
  | { event: "deai"; job_id: number; score: number; rewritten?: boolean }
  | { event: "rewrite"; job_id: number; score: number }
  | { event: "chapter_done"; job_id: number; title: string; words: number; state_updated: boolean; done: number; total: number }
  | { event: "quality_warn"; job_id: number; title: string; score: number; streak: number }
  | { event: "paused"; reason: string; done: number; total: number }
  | { event: "error"; message: string; job_id?: number; title?: string }
  | { event: "done"; completed: number; total: number };

/** SSE 事件流（结构化事件版 streamPost，用于批量连跑） */
export async function streamPostEvents(
  path: string,
  body: unknown,
  onEvent: (ev: BatchEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const token = getToken();
  const resp = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok || !resp.body) {
    let message = `请求失败 (${resp.status})`;
    try {
      const data = await resp.json();
      if (typeof data.detail === "string") message = data.detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(resp.status, message);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const event of events) {
      const line = event.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      try {
        const payload = JSON.parse(line.slice(5).trim());
        if (payload.event) onEvent(payload as BatchEvent);
      } catch {
        /* ignore malformed line */
      }
    }
  }
}

export const aiFactoryM3 = {
  reviewFull: (projectId: number, jobId: number) =>
    api.post<{
      score: number | null;
      issues: NonNullable<AiChapterJob["review_issues"]>;
      has_high: boolean;
      deai_score: number;
      lint_score?: number;
      retention: { hooks?: { type?: string; desc?: string }[]; cool_points?: { type?: string; desc?: string }[] } | null;
    }>(`/api/ai-factory/projects/${projectId}/jobs/${jobId}/review-full`),
  retention: (projectId: number) =>
    api.get<RetentionDashboard>(`/api/ai-factory/projects/${projectId}/retention`),
  batchRun: (projectId: number, count: number, onEvent: (ev: BatchEvent) => void, signal?: AbortSignal) =>
    streamPostEvents(`/api/ai-factory/projects/${projectId}/batch-run?count=${count}`, {}, onEvent, signal),
};

// ---------- AI 配置模型清单（供 AI 工厂路由手动切换） ----------
export const aiConfigApi = {
  list: () => api.get<AIConfig[]>("/api/ai/configs"),
  /** 从配置指向的端点（天枢等 OpenAI 兼容端点）拉 /models 清单 */
  models: (configId: number) =>
    api.get<{ models: string[]; error?: string }>(`/api/ai/configs/${configId}/models`),
};

// ---------- 人工写作 AI 助手（编辑器内续写 / 润色 / 头脑风暴，SSE 流式） ----------
export type AiAssistAction = "continue" | "polish" | "brainstorm";
export interface AiAssistRequest {
  action: AiAssistAction;
  selected_text?: string;
  instruction?: string;
  chapter_id?: number | null;
}
/** 编辑器 AI 助手统一入口：逐段回调文本，{done}/{error} 由 streamPost 消化 */
export function aiAssist(
  novelId: number,
  body: AiAssistRequest,
  onChunk: (text: string) => void,
  signal?: AbortSignal
): Promise<void> {
  return streamPost(`/api/novels/${novelId}/ai-assist`, body, onChunk, signal);
}

// ---------- AI 工厂 M4：导入续写 ----------

export type ImportEvent =
  | { event: "split"; project_id: number; chapters: number; total_words: number }
  | { event: "extract"; batch: number; total_batches: number }
  | { event: "extract_warn"; batch: number; message: string }
  | { event: "merge" }
  | { event: "merge_warn"; message: string }
  | { event: "done"; project_id: number; chapters: number; total_words: number; characters: number; worldview: number }
  | { event: "error"; message: string };

export async function importNovel(
  data: { title: string; genre?: string; style_notes?: string; text: string; target_chapter_words?: number | null },
  onEvent: (ev: ImportEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const token = getToken();
  const resp = await fetch("/api/ai-factory/projects/import", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(data),
    signal,
  });
  if (!resp.ok || !resp.body) {
    let message = `请求失败 (${resp.status})`;
    try {
      const d = await resp.json();
      if (typeof d.detail === "string") message = d.detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(resp.status, message);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const event of events) {
      const line = event.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      try {
        const payload = JSON.parse(line.slice(5).trim());
        if (payload.event) onEvent(payload as ImportEvent);
      } catch {
        /* ignore */
      }
    }
  }
}

// ---------- AI 工厂 M5：简介 / 市场雷达 / 封面 ----------

export interface AiSynopsis {
  short?: string;
  standard?: string;
  promotion?: string;
  douyin?: string;
}

export interface AiMarketReport {
  genre_heat?: string;
  trending_elements?: string[];
  hot_hooks?: string[];
  cool_point_trends?: string[];
  reader_profile?: string;
  update_advice?: string;
  differentiation?: string;
  verdict?: string;
}

export interface AiCoverPrompt {
  concept?: string;
  prompt_en?: string;
  negative?: string;
  tiangong_task?: { ref: string; status: string } | null;
}

export const aiFactoryM5 = {
  synopsis: (projectId: number) =>
    api.post<AiSynopsis>(`/api/ai-factory/projects/${projectId}/synopsis`),
  marketScan: (projectId: number) =>
    api.post<AiMarketReport>(`/api/ai-factory/projects/${projectId}/market-scan`),
  coverPrompt: (projectId: number, style: string) =>
    api.post<AiCoverPrompt>(`/api/ai-factory/projects/${projectId}/cover-prompt`, { style }),
};

// ---------- AI 工厂 M6：修订闭环 + 局部重写 ----------
export const aiFactoryM6 = {
  /** 一键按审校意见修订全文 */
  revise: (projectId: number, jobId: number) =>
    api.post<{ word_count: number; deai_score: number; fixed_issues: number }>(
      `/api/ai-factory/projects/${projectId}/jobs/${jobId}/revise`
    ),
  /** 局部重写摘段 */
  rewritePartial: (projectId: number, jobId: number, excerpt: string, instruction: string) =>
    api.post<{ word_count: number; new_excerpt: string; deai_score: number }>(
      `/api/ai-factory/projects/${projectId}/jobs/${jobId}/rewrite-partial`,
      { excerpt, instruction }
    ),
};

// ---------- 伏笔提醒 + 文本规范检测 ----------

export interface HookAlert {
  title: string;
  status: string;
  note: string;
  planted_chapter: number;
  age: number;
  level: "aging" | "overdue";
}

export const aiFactoryM7 = {
  hookAlerts: (projectId: number) =>
    api.get<{
      done_chapters: number;
      alerts: HookAlert[];
      aging_count: number;
      overdue_count: number;
    }>(`/api/ai-factory/projects/${projectId}/hook-alerts`),
  lint: (projectId: number, text: string) =>
    api.post<{ score: number; issues: { type: string; severity: string; detail: string }[] }>(
      `/api/ai-factory/projects/${projectId}/lint`,
      { text }
    ),
};

// ---------- 璇玑知识库联动 ----------
export const xuanjiApi = {
  upload: (title: string, content: string, folderName = "") =>
    api.post<{ ok: boolean; result: unknown }>("/api/integrations/xuanji/upload", {
      title,
      content,
      folder_name: folderName,
    }),
  kbSync: (projectId: number) =>
    api.post<{ ok: boolean; title: string; chars: number }>(
      `/api/ai-factory/projects/${projectId}/kb-sync`
    ),
};

// ---------- 平台敏感词 + 去AI味 ----------
/** 平台清单。harden = 该平台额外加严的类别（同一处证据在这类平台上升档处置），
 *  与后端 app/textlint.py 的 escalate 声明保持一致，供作者在界面上看到检查范围。 */
export const PLATFORM_CHOICES: { key: string; name: string; harden?: string[] }[] = [
  { key: "", name: "通用（不指定平台）" },
  { key: "qidian", name: "起点中文网", harden: ["涉政与影射现实从严"] },
  {
    key: "fanqie",
    name: "番茄小说",
    harden: ["暴力血腥升为必改", "擦边描写升档", "涉黑违法升档", "引流最严", "未成年零容忍"],
  },
  { key: "qimao", name: "七猫小说", harden: ["暴力血腥升档", "擦边描写升档"] },
  { key: "jjwxc", name: "晋江文学城", harden: ["擦边描写升为必改", "情欲描写从严"] },
  { key: "feilu", name: "飞卢小说", harden: ["暴力血腥升档"] },
  { key: "qunxiang", name: "QQ阅读/阅文系", harden: ["涉政从严", "引流从严"] },
];

export interface DeflavorResult {
  saved: boolean;
  before_score: number;
  after_score: number;
  word_count?: number;
  before_issues?: string[];
  after_issues?: string[];
  message?: string;
}

export const aiFactoryM9 = {
  deflavor: (projectId: number, jobId: number) =>
    api.post<DeflavorResult>(`/api/ai-factory/projects/${projectId}/jobs/${jobId}/deflavor`),
};

// ---------- AI 工厂：投稿导出包 ----------

/** 触发浏览器下载投稿包 zip（GET /api/ai-factory/projects/{id}/export-pack）。 */
export async function downloadExportPack(projectId: number): Promise<void> {
  const token = getToken();
  const resp = await fetch(`/api/ai-factory/projects/${projectId}/export-pack`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!resp.ok) {
    let message = `导出失败 (${resp.status})`;
    try {
      const data = await resp.json();
      if (typeof data.detail === "string") message = data.detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(resp.status, message);
  }
  const blob = await resp.blob();
  // 优先用服务端 RFC 5987 文件名（投稿包_书名_日期.zip），拿不到再用兜底名
  let filename = `投稿包_${projectId}.zip`;
  const cd = resp.headers.get("Content-Disposition") ?? "";
  const m = cd.match(/filename\*=UTF-8''([^;]+)/i);
  if (m) {
    try {
      filename = decodeURIComponent(m[1]);
    } catch {
      /* ignore */
    }
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// ---------- 后台批量连跑（窗口可关） ----------
export interface BgBatchStatus {
  running: boolean;
  done: number;
  total: number;
  current: string;
  results: { title?: string; ok: boolean; words?: number; deai_score?: number; error?: string }[];
  error: string;
  started_at?: string;
  finished_at?: string;
}

export const aiFactoryBg = {
  start: (projectId: number, count: number, jobIds?: number[]) =>
    api.post<{ ok: boolean }>(`/api/ai-factory/projects/${projectId}/batch-bg/start`, {
      count,
      ...(jobIds?.length ? { job_ids: jobIds } : {}),
    }),
  status: (projectId: number) =>
    api.get<BgBatchStatus>(`/api/ai-factory/projects/${projectId}/batch-bg/status`),
  stop: (projectId: number) =>
    api.post<{ ok: boolean }>(`/api/ai-factory/projects/${projectId}/batch-bg/stop`),
};

/** 拆书学习：范式笔记（可编辑，注入立项/设定） */
export interface ReferenceNote {
  title?: string;
  worldview_framework?: string;
  power_system?: string;
  character_config?: { role?: string; archetype?: string; traits?: string }[];
  pacing?: string;
  hooks?: string[];
  voice?: string;
  avoid?: string[];
  borrow_notes?: string;
}

export const REFERENCE_FIELDS: { key: keyof ReferenceNote; label: string; hint: string; list?: boolean }[] = [
  { key: "worldview_framework", label: "世界观结构", hint: "如：宗门林立+位面晋升" },
  { key: "power_system", label: "力量体系", hint: "等级阶梯/晋升方式/代价" },
  { key: "pacing", label: "节奏与爽点", hint: "多少章一个小高潮" },
  { key: "voice", label: "语言调性", hint: "句长/对白比例/视角" },
  { key: "hooks", label: "钩子手法", hint: "每行一条", list: true },
  { key: "avoid", label: "必须避开", hint: "参考书已用烂的桥段，每行一条", list: true },
  { key: "borrow_notes", label: "借鉴建议", hint: "借什么、换什么、怎么差异化" },
];

export const deconstructApi = {
  run: (text: string, titleHint = "") =>
    api.post<{
      reference: ReferenceNote;
      usage?: { model?: string; promptTokens?: number; completionTokens?: number };
    }>("/api/ai-factory/deconstruct", { text, title_hint: titleHint }),
  save: (projectId: number, reference: ReferenceNote) =>
    api.put<{ ok: boolean; reference: ReferenceNote }>(`/api/ai-factory/projects/${projectId}/reference`, { reference }),
  clear: (projectId: number) => api.delete<{ ok: boolean }>(`/api/ai-factory/projects/${projectId}/reference`),
  /** 拆书步骤暂存（防抖调用）：刷新页面后从这里恢复断点 */
  saveDraft: (projectId: number, payload: { text: string; hint: string; note: ReferenceNote | null }) =>
    api.put<{ ok: boolean; chars: number; has_note: boolean }>(
      `/api/ai-factory/projects/${projectId}/deconstruct-draft`,
      payload
    ),
};

/** 章节生成失败：分类后的「病因 + 怎么办」 */
export interface FailureReason {
  code: string;
  title: string;
  hint: string;
  raw: string;
}

/** 单章诊断结果（为什么失败 / 本次上下文规模） */
export interface JobDiagnosis {
  status: string;
  attempt: number;
  last_error: string;
  reason: FailureReason | null;
  context_chars: number;
  context_tokens_est: number;
  context_error: string;
  orphan: boolean;
  orphan_hint: string;
  own_configs: { chapter_llm: string; summary_llm: string; review_llm: string };
}

export const aiFactoryFailApi = {
  /** 前台流式生成失败时回写原因（否则服务端只剩一个 status=failed） */
  markFailed: (projectId: number, jobId: number, error: string) =>
    api.post<{ ok: boolean; reason: FailureReason }>(
      `/api/ai-factory/projects/${projectId}/jobs/${jobId}/fail`,
      { error }
    ),
  diagnose: (projectId: number, jobId: number) =>
    api.get<JobDiagnosis>(`/api/ai-factory/projects/${projectId}/jobs/${jobId}/diagnose`),
  /** 解锁卡死任务：状态置回待生成（此前没有任何接口能把 writing 改回去） */
  resetJob: (projectId: number, jobId: number) =>
    api.post<{ ok: boolean; status: string }>(
      `/api/ai-factory/projects/${projectId}/jobs/${jobId}/reset`,
      {}
    ),
  /** 删除任务（清理失去关联章节、永远生成不了的死结任务） */
  deleteJob: (projectId: number, jobId: number) =>
    api.delete<{ ok: boolean }>(`/api/ai-factory/projects/${projectId}/jobs/${jobId}`),
  /** 一键重试全部失败章节（后台执行） */
  retryFailed: (projectId: number, count = 3) =>
    api.post<{ queued: number; job_ids: number[] }>(
      `/api/ai-factory/projects/${projectId}/retry-failed`,
      { count }
    ),
};
