/** API client — wraps fetch with JWT auth. */

const API_BASE = import.meta.env.VITE_API_BASE || '/api'

let authToken: string | null = localStorage.getItem('token')

export function setToken(token: string | null) {
  authToken = token
  if (token) {
    localStorage.setItem('token', token)
  } else {
    localStorage.removeItem('token')
  }
}

export function getToken(): string | null {
  return authToken
}

/** Standard JSON request helper. Throws on non-2xx. */
export async function apiFetch<T = unknown>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  }

  if (authToken) {
    headers['Authorization'] = `Bearer ${authToken}`
  }

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers })

  if (res.status === 204) return undefined as T

  const data = await res.json()

  if (!res.ok) {
    const detail = data?.detail ?? res.statusText
    throw new ApiError(res.status, typeof detail === 'string' ? detail : JSON.stringify(detail))
  }

  return data as T
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

// ── Auth ──────────────────────────────────────────────
export interface UserOut {
  id: number
  username: string
  email: string
  role: 'admin' | 'author' | 'editor' | 'reader'
  status: 'pending' | 'approved' | 'rejected'
  created_at: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
}

export const authApi = {
  register: (body: { username: string; email: string; password: string }) =>
    apiFetch<UserOut>('/auth/register', { method: 'POST', body: JSON.stringify(body) }),

  login: (body: { username: string; password: string }) =>
    apiFetch<TokenResponse>('/auth/login', { method: 'POST', body: JSON.stringify(body) }),

  me: () => apiFetch<UserOut>('/auth/me'),
}

// ── Novels ────────────────────────────────────────────
export interface NovelOut {
  id: number
  title: string
  synopsis: string | null
  genre: string | null
  tags: string | null
  cover_url: string | null
  user_id: number
  created_at: string
  updated_at: string
}

export const novelsApi = {
  list: () => apiFetch<NovelOut[]>('/novels'),
  get: (id: number) => apiFetch<NovelOut>(`/novels/${id}`),
  create: (body: Partial<NovelOut>) =>
    apiFetch<NovelOut>('/novels', { method: 'POST', body: JSON.stringify(body) }),
  update: (id: number, body: Partial<NovelOut>) =>
    apiFetch<NovelOut>(`/novels/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  delete: (id: number) =>
    apiFetch<void>(`/novels/${id}`, { method: 'DELETE' }),
}

// ── Chapters ──────────────────────────────────────────
export interface ChapterOut {
  id: number
  novel_id: number
  title: string
  content: string | null
  order_index: number
  word_count: number
  created_at: string
  updated_at: string
}

export const chaptersApi = {
  list: (novelId: number) => apiFetch<ChapterOut[]>(`/novels/${novelId}/chapters`),
  get: (novelId: number, chapterId: number) =>
    apiFetch<ChapterOut>(`/novels/${novelId}/chapters/${chapterId}`),
  create: (novelId: number, body: Partial<ChapterOut>) =>
    apiFetch<ChapterOut>(`/novels/${novelId}/chapters`, {
      method: 'POST', body: JSON.stringify(body),
    }),
  update: (novelId: number, chapterId: number, body: Partial<ChapterOut>) =>
    apiFetch<ChapterOut>(`/novels/${novelId}/chapters/${chapterId}`, {
      method: 'PUT', body: JSON.stringify(body),
    }),
  delete: (novelId: number, chapterId: number) =>
    apiFetch<void>(`/novels/${novelId}/chapters/${chapterId}`, { method: 'DELETE' }),
  reorder: (novelId: number, order: Record<number, number>) =>
    apiFetch<ChapterOut[]>(`/novels/${novelId}/chapters/reorder`, {
      method: 'POST', body: JSON.stringify({ order }),
    }),
}

// ── Settings ──────────────────────────────────────────
/** 设定类型 */
export type SettingType = 'worldview' | 'character' | 'outline' | 'plot' | 'foreshadow'
/** 大纲层级类型 */
export type OutlineType = 'volume' | 'chapter' | 'scene'

export interface SettingOut {
  id: number
  novel_id: number
  type: SettingType
  title: string
  content: string | null
  metadata_json: string | null
  outline_type: string | null
  parent_id: number | null
  order_index: number
  created_at: string
  updated_at: string
}

/** 大纲树节点 */
export interface SettingOutlineNode {
  id: number
  title: string
  outline_type: string | null
  content: string | null
  order_index: number
  children: SettingOutlineNode[]
}

export interface SettingCreate {
  type: SettingType
  title: string
  content?: string | null
  metadata_json?: string | null
  outline_type?: string | null
  parent_id?: number | null
}

export const settingsApi = {
  list: (novelId: number, type?: string) =>
    apiFetch<SettingOut[]>(`/novels/${novelId}/settings${type ? `?type=${type}` : ''}`),
  get: (novelId: number, settingId: number) =>
    apiFetch<SettingOut>(`/novels/${novelId}/settings/${settingId}`),
  create: (novelId: number, body: Partial<SettingOut>) =>
    apiFetch<SettingOut>(`/novels/${novelId}/settings`, {
      method: 'POST', body: JSON.stringify(body),
    }),
  update: (novelId: number, settingId: number, body: Partial<SettingOut>) =>
    apiFetch<SettingOut>(`/novels/${novelId}/settings/${settingId}`, {
      method: 'PUT', body: JSON.stringify(body),
    }),
  delete: (novelId: number, settingId: number) =>
    apiFetch<void>(`/novels/${novelId}/settings/${settingId}`, { method: 'DELETE' }),
  /** 获取大纲树形结构 */
  getOutline: (novelId: number) =>
    apiFetch<SettingOutlineNode[]>(`/novels/${novelId}/settings/outline`),
  /** 批量创建设定 */
  batchCreate: (novelId: number, items: SettingCreate[]) =>
    apiFetch<SettingOut[]>(`/novels/${novelId}/settings/batch`, {
      method: 'POST', body: JSON.stringify({ items }),
    }),
}

// ── AI ────────────────────────────────────────────────
export interface AiChatMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
}

export const aiApi = {
  chat: (novelId: number, messages: AiChatMessage[], agentId?: number) =>
    apiFetch<{ response: string }>(`/ai/chat`, {
      method: 'POST',
      body: JSON.stringify({ novel_id: novelId, messages, agent_id: agentId }),
    }),
}

// ── Admin ─────────────────────────────────────────────
export const adminApi = {
  listUsers: () => apiFetch<UserOut[]>('/admin/users'),
  approveUser: (userId: number) =>
    apiFetch<UserOut>(`/admin/users/${userId}/approve`, { method: 'POST' }),
  rejectUser: (userId: number) =>
    apiFetch<UserOut>(`/admin/users/${userId}/reject`, { method: 'POST' }),
  updateUserRole: (userId: number, role: string) =>
    apiFetch<UserOut>(`/admin/users/${userId}/role`, {
      method: 'PUT', body: JSON.stringify({ role }),
    }),
}

// ── Agents ────────────────────────────────────────────
export interface AgentOut {
  id: number
  name: string
  description: string | null
  system_prompt: string | null
  model_config_id: number | null
  tools_json: string | null
  status: string
  created_at: string
}

export const agentsApi = {
  list: () => apiFetch<AgentOut[]>('/agents'),
  create: (body: Partial<AgentOut>) =>
    apiFetch<AgentOut>('/agents', { method: 'POST', body: JSON.stringify(body) }),
  update: (id: number, body: Partial<AgentOut>) =>
    apiFetch<AgentOut>(`/agents/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  delete: (id: number) =>
    apiFetch<void>(`/agents/${id}`, { method: 'DELETE' }),
  test: (id: number) =>
    apiFetch<{ success: boolean; message: string }>(`/agents/${id}/test`, { method: 'POST' }),
}

// ── Model Configs ─────────────────────────────────────
export interface ModelConfigOut {
  id: number
  provider_name: string
  api_base: string | null
  api_key: string | null
  model_id: string
  is_default: boolean
  created_at: string
}

export const modelsConfigApi = {
  list: () => apiFetch<ModelConfigOut[]>('/models'),
  create: (body: Partial<ModelConfigOut>) =>
    apiFetch<ModelConfigOut>('/models', { method: 'POST', body: JSON.stringify(body) }),
  update: (id: number, body: Partial<ModelConfigOut>) =>
    apiFetch<ModelConfigOut>(`/models/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  delete: (id: number) =>
    apiFetch<void>(`/models/${id}`, { method: 'DELETE' }),
  test: (id: number) =>
    apiFetch<{ success: boolean; message: string }>(`/models/${id}/test`, { method: 'POST' }),
  setDefault: (id: number) =>
    apiFetch<ModelConfigOut>(`/models/${id}/default`, { method: 'PUT' }),
}

// ── Knowledge Bases ───────────────────────────────────
export interface KnowledgeBaseOut {
  id: number
  name: string
  description: string | null
  novel_id: number | null
  created_at: string
}

export interface KnowledgeEntryOut {
  id: number
  knowledge_base_id: number
  title: string
  content: string | null
  type: string
  metadata_json: string | null
  created_at: string
  updated_at: string
}

export interface KnowledgeRelationOut {
  id: number
  source_entry_id: number
  target_entry_id: number
  relation_type: string
  description: string | null
}

export interface GraphNode {
  id: string
  label: string
  type: string
  val: number
}

export interface GraphEdge {
  source: string
  target: string
  label: string
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export const knowledgeApi = {
  listBases: () => apiFetch<KnowledgeBaseOut[]>('/knowledge-bases'),
  createBase: (body: Partial<KnowledgeBaseOut>) =>
    apiFetch<KnowledgeBaseOut>('/knowledge-bases', { method: 'POST', body: JSON.stringify(body) }),
  updateBase: (id: number, body: Partial<KnowledgeBaseOut>) =>
    apiFetch<KnowledgeBaseOut>(`/knowledge-bases/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteBase: (id: number) =>
    apiFetch<void>(`/knowledge-bases/${id}`, { method: 'DELETE' }),
  listEntries: (kbId: number) =>
    apiFetch<KnowledgeEntryOut[]>(`/knowledge-bases/${kbId}/entries`),
  createEntry: (kbId: number, body: Partial<KnowledgeEntryOut>) =>
    apiFetch<KnowledgeEntryOut>(`/knowledge-bases/${kbId}/entries`, {
      method: 'POST', body: JSON.stringify(body),
    }),
  updateEntry: (entryId: number, body: Partial<KnowledgeEntryOut>) =>
    apiFetch<KnowledgeEntryOut>(`/knowledge-bases/entries/${entryId}`, {
      method: 'PUT', body: JSON.stringify(body),
    }),
  deleteEntry: (entryId: number) =>
    apiFetch<void>(`/knowledge-bases/entries/${entryId}`, { method: 'DELETE' }),
  getGraph: (kbId: number) =>
    apiFetch<GraphData>(`/knowledge-bases/${kbId}/graph`),
  createRelation: (body: Partial<KnowledgeRelationOut>) =>
    apiFetch<KnowledgeRelationOut>('/knowledge-bases/relations', {
      method: 'POST', body: JSON.stringify(body),
    }),
  deleteRelation: (relId: number) =>
    apiFetch<void>(`/knowledge-bases/relations/${relId}`, { method: 'DELETE' }),
}

// ── Database ──────────────────────────────────────────
export const databaseApi = {
  getStats: () => apiFetch<{
    tables: Record<string, number>
    total_records: number
    db_size_mb: number
    db_path: string
  }>('/database/stats'),
  backup: () => apiFetch<{
    success: boolean
    backup_path: string
    size_bytes: number
    timestamp: string
  }>('/database/backup', { method: 'POST' }),
  listBackups: () => apiFetch<{
    backups: { filename: string; size_bytes: number; created_at: string }[]
  }>('/database/backups'),
}

// ── Export ────────────────────────────────────────────
export const exportApi = {
  exportNovel: (novelId: number, format: string) =>
    `/api/novels/${novelId}/export?format=${format}`,

  /** Download a novel export via fetch with auth, triggering browser download. */
  downloadNovel: async (novelId: number, format: string) => {
    const url = `/api/novels/${novelId}/export?format=${format}`
    const token = getToken()
    const headers: Record<string, string> = {}
    if (token) headers['Authorization'] = `Bearer ${token}`

    const res = await fetch(url, { headers })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new ApiError(res.status, data?.detail ?? res.statusText)
    }

    const blob = await res.blob()
    const disposition = res.headers.get('Content-Disposition') ?? ''
    const match = disposition.match(/filename\*=UTF-8''(.+)/) || disposition.match(/filename="?(.+?)"?$/)
    const filename = match ? decodeURIComponent(match[1]) : `novel_${novelId}.${format}`

    const objUrl = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = objUrl
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(objUrl)
  },
}

// ── Backup ────────────────────────────────────────────
export interface WebDAVConfig {
  webdav_url: string
  username: string
  password: string
}

export interface BackupResult {
  success: boolean
  backup_path: string
  size_bytes: number
  timestamp: string
}

export interface WebDAVStatusResult {
  connected: boolean
  message: string
  free_space_mb: number | null
}

export interface BackupHistoryItem {
  filename: string
  size_bytes: number
  created_at: string
}

export const backupApi = {
  getConfig: () => apiFetch<WebDAVConfig>('/backup/config'),
  saveConfig: (body: WebDAVConfig) =>
    apiFetch<WebDAVConfig>('/backup/config', { method: 'POST', body: JSON.stringify(body) }),
  manualBackup: () =>
    apiFetch<BackupResult>('/backup/manual', { method: 'POST' }),
  getHistory: () =>
    apiFetch<{ backups: BackupHistoryItem[] }>('/backup/history'),
  webdavStatus: (params: { webdav_url: string; username: string; password: string }) => {
    const qs = new URLSearchParams(params).toString()
    return apiFetch<WebDAVStatusResult>(`/backup/webdav/status?${qs}`)
  },
  webdavBackup: (body: { webdav_url: string; username: string; password: string }) =>
    apiFetch<BackupResult>('/backup/webdav', { method: 'POST', body: JSON.stringify(body) }),
}
