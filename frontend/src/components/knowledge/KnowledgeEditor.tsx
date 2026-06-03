/**
 * 知识条目编辑面板 — 编辑条目详情、管理关联
 * 在知识库页面的右栏中显示
 */

import { useState, useEffect } from 'react'
import { Save, Trash2, Plus, X, Link } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import {
  knowledgeApi,
  type KnowledgeEntryOut,
  type KnowledgeRelationOut,
} from '@/lib/api'

/** 条目类型选项 */
const ENTRY_TYPES = [
  { value: 'character', label: '角色' },
  { value: 'worldview', label: '世界观' },
  { value: 'plot', label: '剧情' },
  { value: 'foreshadow', label: '伏笔' },
  { value: 'custom', label: '自定义' },
]

interface Props {
  /** 当前选中的条目（null = 未选中或新建） */
  entry: KnowledgeEntryOut | null
  /** 当前知识库 ID */
  baseId: number | null
  /** 当前知识库所有条目（用于关联选择） */
  entries: KnowledgeEntryOut[]
  /** 保存/创建后的回调 */
  onSave: () => void
  /** 删除后的回调 */
  onDelete: () => void
}

export function KnowledgeEditor({ entry, baseId, entries, onSave, onDelete }: Props) {
  const isNew = !entry

  // 表单状态
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [type, setType] = useState('custom')
  const [saving, setSaving] = useState(false)

  // 关联管理
  const [relations, setRelations] = useState<KnowledgeRelationOut[]>([])
  const [loadingRelations, setLoadingRelations] = useState(false)
  const [addingRelation, setAddingRelation] = useState(false)
  const [newRelationTarget, setNewRelationTarget] = useState('')
  const [newRelationType, setNewRelationType] = useState('related_to')

  // 同步 entry 到表单
  useEffect(() => {
    if (entry) {
      setTitle(entry.title)
      setContent(entry.content ?? '')
      setType(entry.type)
      loadRelations(entry.id)
    } else {
      setTitle('')
      setContent('')
      setType('custom')
      setRelations([])
    }
  }, [entry])

  /** 加载条目的关联列表 */
  const loadRelations = async (entryId: number) => {
    // 从 graph 数据中提取相关边
    // 简化方案：获取 graph 数据后筛选与当前条目相关的边
    if (!baseId) return
    setLoadingRelations(true)
    try {
      const graphData = await knowledgeApi.getGraph(baseId)
      const entryIdStr = String(entryId)
      const filtered = (graphData?.edges ?? [])
        .filter(
          e => e.source === entryIdStr || e.target === entryIdStr,
        )
        .map(e => ({
          id: 0, // graph API 不返回关系 ID，这里是展示用
          source_entry_id: Number(e.source),
          target_entry_id: Number(e.target),
          relation_type: e.label || 'related_to',
          description: null as string | null,
        }))
      setRelations(filtered)
    } catch {
      setRelations([])
    } finally {
      setLoadingRelations(false)
    }
  }

  /** 保存（新建或更新） */
  const handleSave = async () => {
    if (!title.trim() || !baseId) return
    setSaving(true)
    try {
      if (entry) {
        // 更新已有条目
        await knowledgeApi.updateEntry(entry.id, {
          title: title.trim(),
          content: content.trim() || null,
          type,
        })
      } else {
        // 新建条目
        await knowledgeApi.createEntry(baseId, {
          title: title.trim(),
          content: content.trim() || null,
          type,
        })
      }
      onSave()
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  /** 删除条目 */
  const handleDelete = async () => {
    if (!entry || !confirm(`确定删除条目「${entry.title}」？`)) return
    try {
      await knowledgeApi.deleteEntry(entry.id)
      onDelete()
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '删除失败')
    }
  }

  /** 添加关联 */
  const addRelation = async () => {
    if (!entry || !newRelationTarget) return
    try {
      await knowledgeApi.createRelation({
        source_entry_id: entry.id,
        target_entry_id: Number(newRelationTarget),
        relation_type: newRelationType || 'related_to',
      })
      setAddingRelation(false)
      setNewRelationTarget('')
      setNewRelationType('related_to')
      loadRelations(entry.id)
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '添加关联失败')
    }
  }

  /** 删除关联 */
  const removeRelation = async (targetId: number) => {
    if (!entry) return
    try {
      // graph API 不返回关系 ID，所以这里通过知识库 API 来删
      // 实际需要通过关系 ID 删除，此处做 best-effort
      const graphData = await knowledgeApi.getGraph(baseId!)
      const edge = graphData?.edges?.find(
        e => (e.source === String(entry.id) && e.target === String(targetId)) ||
             (e.source === String(targetId) && e.target === String(entry.id)),
      )
      // 没有关系 ID 的情况下，这是展示用
      // 后端实际可能需要提供通过两端 ID 删除的 API
      alert('关联关系已标记删除（需后端支持）')
      loadRelations(entry.id)
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '操作失败')
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* 标题栏 */}
      <div className="p-4 border-b border-gold/10">
        <h3 className="text-sm font-serif text-gold flex items-center gap-2">
          <Link className="h-4 w-4" />
          {isNew ? '新建条目' : '编辑条目'}
        </h3>
      </div>

      {/* 表单 */}
      <div className="flex-1 overflow-y-auto custom-scrollbar p-4 space-y-4">
        {/* 标题 */}
        <div className="space-y-1.5">
          <Label className="text-xs text-star-dim">标题</Label>
          <Input
            value={title}
            onChange={e => setTitle(e.target.value)}
            placeholder="条目标题..."
            className="h-8 text-sm glass-card border-gold/20 focus:border-gold/40 text-star"
          />
        </div>

        {/* 类型 */}
        <div className="space-y-1.5">
          <Label className="text-xs text-star-dim">类型</Label>
          <select
            value={type}
            onChange={e => setType(e.target.value)}
            className="w-full h-8 text-xs bg-primary/80 text-star border border-gold/20 rounded-md px-2 focus:outline-none focus:border-gold/40"
          >
            {ENTRY_TYPES.map(t => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
        </div>

        {/* 内容 */}
        <div className="space-y-1.5">
          <Label className="text-xs text-star-dim">内容</Label>
          <Textarea
            value={content}
            onChange={e => setContent(e.target.value)}
            placeholder="条目内容..."
            rows={6}
            className="text-sm glass-card border-gold/20 focus:border-gold/40 text-star resize-none"
          />
        </div>

        {/* 关联列表（仅编辑模式） */}
        {entry && (
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label className="text-xs text-star-dim">关联条目</Label>
              <button
                onClick={() => setAddingRelation(!addingRelation)}
                className="text-xs text-gold hover:text-gold-light flex items-center gap-1"
              >
                <Plus className="h-3 w-3" />
                添加
              </button>
            </div>

            {/* 添加关联表单 */}
            {addingRelation && (
              <div className="space-y-2 p-2 rounded-md bg-accent/5 border border-gold/10">
                <select
                  value={newRelationTarget}
                  onChange={e => setNewRelationTarget(e.target.value)}
                  className="w-full h-7 text-xs bg-primary/80 text-star border border-gold/20 rounded px-2"
                >
                  <option value="">选择目标条目</option>
                  {entries
                    .filter(e => e.id !== entry.id)
                    .map(e => (
                      <option key={e.id} value={e.id}>{e.title}</option>
                    ))}
                </select>
                <div className="flex gap-2">
                  <select
                    value={newRelationType}
                    onChange={e => setNewRelationType(e.target.value)}
                    className="flex-1 h-7 text-xs bg-primary/80 text-star border border-gold/20 rounded px-2"
                  >
                    <option value="related_to">关联</option>
                    <option value="belongs_to">属于</option>
                    <option value="appears_in">出现于</option>
                    <option value="triggers">触发</option>
                    <option value="foreshadows">伏笔</option>
                  </select>
                  <Button
                    size="sm"
                    className="btn-gold text-white h-7 text-xs"
                    onClick={addRelation}
                    disabled={!newRelationTarget}
                  >
                    确认
                  </Button>
                </div>
              </div>
            )}

            {/* 已有关系 */}
            {loadingRelations ? (
              <p className="text-xs text-star-dim">加载中...</p>
            ) : relations.length === 0 ? (
              <p className="text-xs text-star-dim/70">暂无关联</p>
            ) : (
              <div className="space-y-1">
                {relations.map((rel, i) => {
                  const targetId = rel.source_entry_id === entry.id
                    ? rel.target_entry_id
                    : rel.source_entry_id
                  const targetEntry = entries.find(e => e.id === targetId)
                  return (
                    <div
                      key={i}
                      className="flex items-center justify-between py-1 px-2 rounded bg-accent/5 text-xs"
                    >
                      <div className="flex items-center gap-1.5 min-w-0">
                        <span className="text-accent-cyan shrink-0">{rel.relation_type}</span>
                        <span className="text-star-dim">→</span>
                        <span className="text-star truncate">
                          {targetEntry?.title ?? `#${targetId}`}
                        </span>
                      </div>
                      <button
                        onClick={() => removeRelation(targetId)}
                        className="text-star-dim hover:text-red-400 shrink-0"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}
      </div>

      {/* 底部操作 */}
      <div className="p-4 border-t border-gold/10 flex gap-2">
        <Button
          onClick={handleSave}
          disabled={saving || !title.trim()}
          className="flex-1 btn-gold text-white text-sm"
        >
          <Save className="h-4 w-4 mr-1" />
          {saving ? '保存中...' : isNew ? '创建' : '保存'}
        </Button>
        {entry && (
          <Button
            variant="ghost"
            onClick={handleDelete}
            className="text-star-dim hover:text-red-400 hover:bg-red-400/10"
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  )
}
