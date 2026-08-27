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

export interface Chapter {
  id: number;
  volume_id: number | null;
  number: number;
  title: string;
  display_title: string;
  sort_order: number;
  word_count: number;
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

export interface AIConfig {
  id: number;
  name: string;
  base_url: string;
  model: string;
  is_default: boolean;
  has_key: boolean;
}

export interface SkillCard {
  slug: string;
  name: string;
  category: "create" | "check";
  category_label: string;
  brief: string;
  description: string;
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
}

// ---------- 章节快照 ----------

/** 列表端：不含 content；详情端在此基础上加 content 字段（见 SnapshotDetail） */
export interface Snapshot {
  id: number;
  created_at: string;
  label: string;
  trigger: "auto" | "manual" | "pre_rollback";
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
