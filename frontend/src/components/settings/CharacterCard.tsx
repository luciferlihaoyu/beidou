import { useState } from 'react'
import { Plus, X, Edit2, Trash2, ChevronDown, ChevronUp } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { useEditorStore } from '@/store/editor'
import { type SettingOut, type SettingType } from '@/lib/api'

/** 角色卡片组件 — 卡片式展示，点击展开详情编辑 */

/* ── 角色元数据 ──────────────────────────── */
interface CharacterMeta {
  appearance?: string    // 外貌
  personality?: string   // 性格
  relationship?: string  // 关系
  arc?: string           // 成长弧线
  tags?: string[]        // 性格标签
}

/** 解析角色的 metadata_json 字段 */
function parseCharacterMeta(metaJson: string | null): CharacterMeta {
  if (!metaJson) return {}
  try { return JSON.parse(metaJson) } catch { return {} }
}

/** 序列化角色元数据 */
function serializeCharacterMeta(meta: CharacterMeta): string {
  return JSON.stringify(meta)
}

/* ── 角色标签色 ─────────────────────────── */
const tagColors = [
  'bg-pink-500/20 text-pink-300',
  'bg-purple-500/20 text-purple-300',
  'bg-sky-500/20 text-sky-300',
  'bg-emerald-500/20 text-emerald-300',
  'bg-amber-500/20 text-amber-300',
  'bg-rose-500/20 text-rose-300',
  'bg-indigo-500/20 text-indigo-300',
]

/* ── 单个角色卡片 ────────────────────────── */
function CharacterCardItem({
  character,
  onUpdate,
  onDelete,
}: {
  character: SettingOut
  onUpdate: (id: number, data: Partial<SettingOut>) => Promise<void>
  onDelete: (id: number) => Promise<void>
}) {
  const [expanded, setExpanded] = useState(false)
  const [editing, setEditing] = useState(false)
  const meta = parseCharacterMeta(character.metadata_json)

  // 编辑表单
  const [form, setForm] = useState({
    name: character.title,
    appearance: meta.appearance ?? '',
    personality: meta.personality ?? '',
    relationship: meta.relationship ?? '',
    arc: meta.arc ?? '',
    tagsInput: (meta.tags ?? []).join('、'),
  })

  const initials = character.title.charAt(0)

  const handleSave = async () => {
    const tags = form.tagsInput
      .split(/[、,]/)
      .map((t) => t.trim())
      .filter(Boolean)

    const newMeta = serializeCharacterMeta({
      appearance: form.appearance,
      personality: form.personality,
      relationship: form.relationship,
      arc: form.arc,
      tags,
    })

    await onUpdate(character.id, {
      title: form.name.trim(),
      metadata_json: newMeta,
    })
    setEditing(false)
  }

  const startEdit = () => {
    setForm({
      name: character.title,
      appearance: meta.appearance ?? '',
      personality: meta.personality ?? '',
      relationship: meta.relationship ?? '',
      arc: meta.arc ?? '',
      tagsInput: (meta.tags ?? []).join('、'),
    })
    setEditing(true)
  }

  return (
    <div className="glass-card overflow-hidden transition-all">
      {/* 卡片头部 */}
      <div
        className="p-4 flex items-start gap-3 cursor-pointer"
        onClick={() => !editing && setExpanded(!expanded)}
      >
        {/* 圆形头像占位 */}
        <div className="w-12 h-12 rounded-full bg-gradient-to-br from-amber-500/30 to-rose-500/30 flex items-center justify-center text-lg font-bold text-amber-300 shrink-0 border border-amber-500/20">
          {initials}
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between">
            <h4 className="font-semibold text-sm truncate">{character.title}</h4>
            <div className="flex gap-0.5">
              <Button
                variant="ghost" size="sm" className="h-7 w-7 p-0"
                onClick={(e) => { e.stopPropagation(); startEdit() }}
              >
                <Edit2 className="h-3.5 w-3.5" />
              </Button>
              <Button
                variant="ghost" size="sm" className="h-7 w-7 p-0 text-red-400"
                onClick={(e) => { e.stopPropagation(); onDelete(character.id) }}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>

          {/* 性格标签 */}
          {(meta.tags ?? []).length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1.5">
              {meta.tags!.map((tag, i) => (
                <span
                  key={i}
                  className={`px-2 py-0.5 rounded-full text-xs font-medium ${tagColors[i % tagColors.length]}`}
                >
                  {tag}
                </span>
              ))}
            </div>
          )}

          {/* 展开按钮 */}
          <div className="mt-1 text-muted-foreground">
            {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          </div>
        </div>
      </div>

      {/* 展开详情 / 编辑表单 */}
      {expanded && (
        <div className="px-4 pb-4 border-t border-white/5 pt-3">
          {editing ? (
            /* ── 编辑表单 ── */
            <div className="space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">角色名</label>
                <Input
                  value={form.name}
                  onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">性格标签（用顿号分隔）</label>
                <Input
                  value={form.tagsInput}
                  onChange={(e) => setForm((p) => ({ ...p, tagsInput: e.target.value }))}
                  placeholder="例如：坚毅、善良、腹黑"
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">外貌</label>
                <Textarea
                  value={form.appearance}
                  onChange={(e) => setForm((p) => ({ ...p, appearance: e.target.value }))}
                  rows={2}
                  placeholder="角色的外貌特征..."
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">性格</label>
                <Textarea
                  value={form.personality}
                  onChange={(e) => setForm((p) => ({ ...p, personality: e.target.value }))}
                  rows={2}
                  placeholder="角色的性格描述..."
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">关系</label>
                <Textarea
                  value={form.relationship}
                  onChange={(e) => setForm((p) => ({ ...p, relationship: e.target.value }))}
                  rows={2}
                  placeholder="与其他角色的关系..."
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">成长弧线</label>
                <Textarea
                  value={form.arc}
                  onChange={(e) => setForm((p) => ({ ...p, arc: e.target.value }))}
                  rows={2}
                  placeholder="角色成长轨迹..."
                />
              </div>
              <div className="flex gap-2">
                <Button size="sm" className="btn-gold" onClick={handleSave}>保存</Button>
                <Button size="sm" variant="outline" onClick={() => setEditing(false)}>取消</Button>
              </div>
            </div>
          ) : (
            /* ── 详情展示 ── */
            <div className="space-y-3 text-sm">
              {meta.appearance && (
                <div>
                  <span className="text-xs text-muted-foreground font-medium">外貌</span>
                  <p className="text-muted-foreground mt-0.5 whitespace-pre-wrap">{meta.appearance}</p>
                </div>
              )}
              {meta.personality && (
                <div>
                  <span className="text-xs text-muted-foreground font-medium">性格</span>
                  <p className="text-muted-foreground mt-0.5 whitespace-pre-wrap">{meta.personality}</p>
                </div>
              )}
              {meta.relationship && (
                <div>
                  <span className="text-xs text-muted-foreground font-medium">关系</span>
                  <p className="text-muted-foreground mt-0.5 whitespace-pre-wrap">{meta.relationship}</p>
                </div>
              )}
              {meta.arc && (
                <div>
                  <span className="text-xs text-muted-foreground font-medium">成长弧线</span>
                  <p className="text-muted-foreground mt-0.5 whitespace-pre-wrap">{meta.arc}</p>
                </div>
              )}
              <Button size="sm" variant="outline" onClick={startEdit}>
                <Edit2 className="h-3.5 w-3.5 mr-1" />
                编辑
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/* ── 角色列表主组件 ──────────────────────── */
export function CharacterCards() {
  const { novel, settings, createSetting, updateSetting, deleteSetting, loadSettings } = useEditorStore()
  const [showNew, setShowNew] = useState(false)
  const [newName, setNewName] = useState('')
  const [newTags, setNewTags] = useState('')

  const characters = settings.filter((s) => s.type === 'character')

  const handleCreate = async () => {
    if (!novel || !newName.trim()) return
    const tags = newTags.split(/[、,]/).map((t) => t.trim()).filter(Boolean)
    await createSetting({
      type: 'character',
      title: newName.trim(),
      metadata_json: JSON.stringify({ tags }),
    })
    setNewName('')
    setNewTags('')
    setShowNew(false)
  }

  const handleUpdate = async (id: number, data: Partial<SettingOut>) => {
    if (!novel) return
    await updateSetting(id, data)
  }

  const handleDelete = async (id: number) => {
    if (!novel) return
    if (!confirm('确定删除这个角色？')) return
    await deleteSetting(id)
  }

  return (
    <div className="space-y-3">
      {/* 新建按钮 */}
      {!showNew ? (
        <Button variant="outline" className="w-full" onClick={() => setShowNew(true)}>
          <Plus className="h-4 w-4 mr-2" />
          添加角色
        </Button>
      ) : (
        <div className="glass-card p-4 space-y-3">
          <h4 className="font-semibold text-sm">新建角色</h4>
          <Input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="角色名"
            autoFocus
          />
          <Input
            value={newTags}
            onChange={(e) => setNewTags(e.target.value)}
            placeholder="性格标签（用顿号分隔）"
          />
          <div className="flex gap-2">
            <Button size="sm" className="btn-gold" onClick={handleCreate}>创建</Button>
            <Button size="sm" variant="outline" onClick={() => { setShowNew(false); setNewName(''); setNewTags('') }}>
              取消
            </Button>
          </div>
        </div>
      )}

      {/* 角色卡片列表 */}
      {characters.length === 0 && !showNew ? (
        <div className="text-center py-12 text-muted-foreground text-sm">
          <Users className="h-10 w-10 mx-auto mb-2 opacity-20" />
          暂无角色，点击「添加角色」创建
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {characters.map((c) => (
            <CharacterCardItem
              key={c.id}
              character={c}
              onUpdate={handleUpdate}
              onDelete={handleDelete}
            />
          ))}
        </div>
      )}
    </div>
  )
}

/* 占位图标组件 */
function Users({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
      <path d="M16 3.13a4 4 0 0 1 0 7.75" />
    </svg>
  )
}
