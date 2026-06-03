import { useState } from 'react'
import { Plus, Edit2, Trash2, Eye, EyeOff, Clock } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useEditorStore } from '@/store/editor'
import { type SettingOut, type SettingType } from '@/lib/api'

/** 伏笔追踪组件 — 表格展示，按状态筛选，行展开编辑 */

/* ── 伏笔状态 ──────────────────────────── */
type ForeshadowStatus = 'unresolved' | 'in_progress' | 'resolved'

const statusConfig: Record<ForeshadowStatus, {
  label: string
  className: string
  icon: React.ReactNode
}> = {
  unresolved: {
    label: '未回收',
    className: 'bg-amber-500/20 text-amber-400',
    icon: <EyeOff className="h-3 w-3" />,
  },
  in_progress: {
    label: '进行中',
    className: 'bg-sky-500/20 text-sky-400',
    icon: <Clock className="h-3 w-3" />,
  },
  resolved: {
    label: '已回收',
    className: 'bg-emerald-500/20 text-emerald-400',
    icon: <Eye className="h-3 w-3" />,
  },
}

/* ── 伏笔元数据 ────────────────────────── */
interface ForeshadowMeta {
  plantChapter?: string   // 埋设章节
  resolveChapter?: string // 回收章节
  status?: ForeshadowStatus
}

function parseForeshadowMeta(metaJson: string | null): ForeshadowMeta {
  if (!metaJson) return { status: 'unresolved' }
  try { return JSON.parse(metaJson) } catch { return { status: 'unresolved' } }
}

function serializeForeshadowMeta(meta: ForeshadowMeta): string {
  return JSON.stringify(meta)
}

/* ── 单个伏笔行 ────────────────────────── */
function ForeshadowRow({
  item,
  onUpdate,
  onDelete,
}: {
  item: SettingOut
  onUpdate: (id: number, data: Partial<SettingOut>) => Promise<void>
  onDelete: (id: number) => Promise<void>
}) {
  const [expanded, setExpanded] = useState(false)
  const [editing, setEditing] = useState(false)
  const meta = parseForeshadowMeta(item.metadata_json)
  const status: ForeshadowStatus = meta.status ?? 'unresolved'
  const cfg = statusConfig[status]

  // 编辑表单
  const [form, setForm] = useState({
    name: item.title,
    description: item.content ?? '',
    plantChapter: meta.plantChapter ?? '',
    resolveChapter: meta.resolveChapter ?? '',
    status: status,
  })

  const startEdit = () => {
    setForm({
      name: item.title,
      description: item.content ?? '',
      plantChapter: meta.plantChapter ?? '',
      resolveChapter: meta.resolveChapter ?? '',
      status: status,
    })
    setEditing(true)
    setExpanded(true)
  }

  const handleSave = async () => {
    const newMeta = serializeForeshadowMeta({
      plantChapter: form.plantChapter,
      resolveChapter: form.resolveChapter,
      status: form.status,
    })

    await onUpdate(item.id, {
      title: form.name.trim(),
      content: form.description,
      metadata_json: newMeta,
    })
    setEditing(false)
    setExpanded(false)
  }

  return (
    <>
      {/* 表格行 */}
      <tr
        className={`border-b border-white/5 hover:bg-white/5 cursor-pointer transition-colors ${expanded ? 'bg-white/5' : ''}`}
        onClick={() => !editing && setExpanded(!expanded)}
      >
        <td className="py-3 px-3">
          <span className="font-medium text-sm">{item.title}</span>
        </td>
        <td className="py-3 px-3 text-sm text-muted-foreground max-w-[200px] truncate hidden md:table-cell">
          {item.content ?? '—'}
        </td>
        <td className="py-3 px-3 text-sm text-muted-foreground hidden md:table-cell">
          {meta.plantChapter ?? '—'}
        </td>
        <td className="py-3 px-3 text-sm text-muted-foreground hidden md:table-cell">
          {meta.resolveChapter ?? '—'}
        </td>
        <td className="py-3 px-3">
          <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${cfg.className}`}>
            {cfg.icon}
            {cfg.label}
          </span>
        </td>
        <td className="py-3 px-3">
          <div className="flex gap-0.5 opacity-0 group-hover:opacity-100" onClick={(e) => e.stopPropagation()}>
            <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={startEdit}>
              <Edit2 className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="ghost" size="sm" className="h-7 w-7 p-0 text-red-400"
              onClick={() => onDelete(item.id)}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          </div>
        </td>
      </tr>

      {/* 展开编辑区 */}
      {expanded && editing && (
        <tr>
          <td colSpan={6} className="p-4 bg-white/[0.02]">
            <div className="space-y-3">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">伏笔名</label>
                  <Input
                    value={form.name}
                    onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
                  />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">状态</label>
                  <Select
                    value={form.status}
                    onValueChange={(v) => setForm((p) => ({ ...p, status: v as ForeshadowStatus }))}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="unresolved">🔶 未回收</SelectItem>
                      <SelectItem value="in_progress">🔷 进行中</SelectItem>
                      <SelectItem value="resolved">🟢 已回收</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">埋设章节</label>
                  <Input
                    value={form.plantChapter}
                    onChange={(e) => setForm((p) => ({ ...p, plantChapter: e.target.value }))}
                    placeholder="如：第3章"
                  />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">回收章节</label>
                  <Input
                    value={form.resolveChapter}
                    onChange={(e) => setForm((p) => ({ ...p, resolveChapter: e.target.value }))}
                    placeholder="如：第20章"
                  />
                </div>
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">描述</label>
                <Textarea
                  value={form.description}
                  onChange={(e) => setForm((p) => ({ ...p, description: e.target.value }))}
                  rows={3}
                  placeholder="伏笔详细描述..."
                />
              </div>
              <div className="flex gap-2">
                <Button size="sm" className="btn-gold" onClick={handleSave}>保存</Button>
                <Button size="sm" variant="outline" onClick={() => { setEditing(false); setExpanded(false) }}>
                  取消
                </Button>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

/* ── 伏笔追踪主组件 ──────────────────────── */
export function ForeshadowTracker() {
  const { novel, settings, createSetting, updateSetting, deleteSetting } = useEditorStore()
  const [statusFilter, setStatusFilter] = useState<ForeshadowStatus | 'all'>('all')
  const [showNew, setShowNew] = useState(false)
  const [newName, setNewName] = useState('')
  const [newDesc, setNewDesc] = useState('')

  const foreshadows = settings
    .filter((s) => s.type === 'foreshadow')
    .filter((s) => {
      if (statusFilter === 'all') return true
      const meta = parseForeshadowMeta(s.metadata_json)
      return (meta.status ?? 'unresolved') === statusFilter
    })

  const handleCreate = async () => {
    if (!novel || !newName.trim()) return
    const meta = serializeForeshadowMeta({
      status: 'unresolved',
      plantChapter: '',
      resolveChapter: '',
    })
    await createSetting({
      type: 'foreshadow',
      title: newName.trim(),
      content: newDesc,
      metadata_json: meta,
    })
    setNewName('')
    setNewDesc('')
    setShowNew(false)
  }

  const handleUpdate = async (id: number, data: Partial<SettingOut>) => {
    if (!novel) return
    await updateSetting(id, data)
  }

  const handleDelete = async (id: number) => {
    if (!novel) return
    if (!confirm('确定删除该伏笔？')) return
    await deleteSetting(id)
  }

  // 统计
  const allForeshadows = settings.filter((s) => s.type === 'foreshadow')
  const counts = {
    all: allForeshadows.length,
    unresolved: allForeshadows.filter((s) => {
      const m = parseForeshadowMeta(s.metadata_json)
      return (m.status ?? 'unresolved') === 'unresolved'
    }).length,
    in_progress: allForeshadows.filter((s) => {
      const m = parseForeshadowMeta(s.metadata_json)
      return m.status === 'in_progress'
    }).length,
    resolved: allForeshadows.filter((s) => {
      const m = parseForeshadowMeta(s.metadata_json)
      return m.status === 'resolved'
    }).length,
  }

  return (
    <div className="space-y-4">
      {/* 筛选栏 + 统计 */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-1.5 bg-white/5 rounded-lg p-1">
          {([
            { key: 'all', label: '全部', count: counts.all },
            { key: 'unresolved', label: '未回收', count: counts.unresolved },
            { key: 'in_progress', label: '进行中', count: counts.in_progress },
            { key: 'resolved', label: '已回收', count: counts.resolved },
          ] as const).map((f) => (
            <button
              key={f.key}
              className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                statusFilter === f.key
                  ? 'bg-amber-500/20 text-amber-400'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
              onClick={() => setStatusFilter(f.key)}
            >
              {f.label}
              <span className="ml-1 opacity-50">{f.count}</span>
            </button>
          ))}
        </div>

        <Button variant="outline" size="sm" onClick={() => setShowNew(true)}>
          <Plus className="h-3.5 w-3.5 mr-1" />
          添加伏笔
        </Button>
      </div>

      {/* 新建表单 */}
      {showNew && (
        <div className="glass-card p-4 space-y-3">
          <h4 className="font-semibold text-sm">新建伏笔</h4>
          <Input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="伏笔名称"
            autoFocus
          />
          <Textarea
            value={newDesc}
            onChange={(e) => setNewDesc(e.target.value)}
            placeholder="伏笔描述（可选）"
            rows={2}
          />
          <div className="flex gap-2">
            <Button size="sm" className="btn-gold" onClick={handleCreate}>创建</Button>
            <Button size="sm" variant="outline" onClick={() => { setShowNew(false); setNewName(''); setNewDesc('') }}>
              取消
            </Button>
          </div>
        </div>
      )}

      {/* 表格 */}
      {foreshadows.length === 0 ? (
        <div className="text-center py-12 text-muted-foreground text-sm">
          <Eye className="h-10 w-10 mx-auto mb-2 opacity-20" />
          {statusFilter === 'all' ? '暂无伏笔记录' : `暂无「${statusFilter === 'unresolved' ? '未回收' : statusFilter === 'in_progress' ? '进行中' : '已回收'}」状态的伏笔`}
        </div>
      ) : (
        <div className="glass-card overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-white/10 text-xs text-muted-foreground">
                <th className="py-2.5 px-3 text-left font-medium">伏笔名</th>
                <th className="py-2.5 px-3 text-left font-medium hidden md:table-cell">描述</th>
                <th className="py-2.5 px-3 text-left font-medium hidden md:table-cell">埋设章节</th>
                <th className="py-2.5 px-3 text-left font-medium hidden md:table-cell">回收章节</th>
                <th className="py-2.5 px-3 text-left font-medium">状态</th>
                <th className="py-2.5 px-3 text-left font-medium w-[60px]" />
              </tr>
            </thead>
            <tbody>
              {foreshadows.map((item) => (
                <ForeshadowRow
                  key={item.id}
                  item={item}
                  onUpdate={handleUpdate}
                  onDelete={handleDelete}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
