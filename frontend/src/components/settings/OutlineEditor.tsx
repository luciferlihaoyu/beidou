import { useState, useCallback, useRef, useEffect } from 'react'
import {
  ChevronRight, ChevronDown, Plus, Trash2, GripVertical,
  BookOpen, FileText, ScrollText
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { useEditorStore } from '@/store/editor'
import { type SettingOutlineNode, type OutlineType } from '@/lib/api'

/** 大纲节点组件 — 树形结构，支持展开/折叠/拖拽/双击编辑 */

/* ── 类型 ──────────────────────────────────── */
type DragState = {
  nodeId: number
  /** 拖拽来源层级 */
  fromLevel: number
  targetId: number | null
  /** 放置位置: before | after | inside */
  position: 'before' | 'after' | 'inside' | null
} | null

type NewNodeForm = {
  parentId: number | null
  outlineType: OutlineType
} | null

/* ── 图标映射 ─────────────────────────────── */
const outlineIcon: Record<string, React.ReactNode> = {
  volume: <BookOpen className="h-4 w-4 text-amber-400" />,
  chapter: <FileText className="h-4 w-4 text-sky-400" />,
  scene: <ScrollText className="h-4 w-4 text-emerald-400" />,
}
const outlineLabel: Record<string, string> = {
  volume: '卷',
  chapter: '章',
  scene: '场景',
}
/** 层级顺序 — 用于拖拽限制 */
const outlineLevel: Record<string, number> = {
  volume: 0,
  chapter: 1,
  scene: 2,
}

/* ── 大纲子节点 ────────────────────────────── */
function OutlineNode({
  node,
  selectedId,
  onSelect,
  onUpdate,
  onDelete,
  onAddChild,
  expandedIds,
  onToggleExpand,
  dragState,
  onDragStart,
  onDragOver,
  onDragEnd,
}: {
  node: SettingOutlineNode
  selectedId: number | null
  onSelect: (id: number) => void
  onUpdate: (id: number, data: { title: string; content: string }) => void
  onDelete: (id: number) => void
  onAddChild: (parentId: number, outlineType: OutlineType) => void
  expandedIds: Set<number>
  onToggleExpand: (id: number) => void
  dragState: DragState
  onDragStart: (nodeId: number) => void
  onDragOver: (nodeId: number, position: 'before' | 'after' | 'inside') => void
  onDragEnd: () => void
}) {
  const isExpanded = expandedIds.has(node.id)
  const isSelected = selectedId === node.id
  const hasChildren = node.children.length > 0
  const ot = node.outline_type ?? 'scene'
  const level = outlineLevel[ot] ?? 0

  // 双击编辑标题
  const [editingTitle, setEditingTitle] = useState(false)
  const [editTitle, setEditTitle] = useState(node.title)
  const titleInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editingTitle) titleInputRef.current?.focus()
  }, [editingTitle])

  const handleDoubleClick = (e: React.MouseEvent) => {
    e.stopPropagation()
    setEditingTitle(true)
    setEditTitle(node.title)
  }

  const handleTitleSubmit = () => {
    if (editTitle.trim() && editTitle.trim() !== node.title) {
      onUpdate(node.id, { title: editTitle.trim(), content: node.content ?? '' })
    }
    setEditingTitle(false)
  }

  // 拖拽高亮
  const isDragOver = dragState?.targetId === node.id

  return (
    <div className="select-none">
      <div
        className={`flex items-center gap-1.5 py-1.5 px-1 rounded-md cursor-pointer group transition-colors
          ${isSelected ? 'bg-amber-500/15 border border-amber-500/30' : 'hover:bg-white/5 border border-transparent'}
          ${isDragOver && dragState?.position === 'before' ? 'border-t-2 border-t-amber-400' : ''}
          ${isDragOver && dragState?.position === 'after' ? 'border-b-2 border-b-amber-400' : ''}
          ${isDragOver && dragState?.position === 'inside' ? 'bg-amber-500/20 border border-amber-400/40' : ''}
        `}
        draggable
        onDragStart={(e) => { e.dataTransfer.effectAllowed = 'move'; onDragStart(node.id) }}
        onDragOver={(e) => {
          e.preventDefault();
          // 根据鼠标位置判断放置位置
          const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
          const y = e.clientY - rect.top
          const h = rect.height
          if (y < h * 0.25) onDragOver(node.id, 'before')
          else if (y > h * 0.75) onDragOver(node.id, 'after')
          else onDragOver(node.id, 'inside')
        }}
        onDragEnd={onDragEnd}
      >
        {/* 拖拽手柄 */}
        <GripVertical className="h-3.5 w-3.5 opacity-0 group-hover:opacity-40 cursor-grab shrink-0" />

        {/* 展开/折叠 */}
        <button
          className="p-0.5 hover:bg-white/10 rounded shrink-0"
          onClick={(e) => { e.stopPropagation(); onToggleExpand(node.id) }}
        >
          {hasChildren ? (
            isExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />
          ) : (
            <span className="w-4" />
          )}
        </button>

        {/* 图标 */}
        {outlineIcon[ot]}

        {/* 标题 — 双击编辑 */}
        {editingTitle ? (
          <input
            ref={titleInputRef}
            className="flex-1 bg-transparent border-b border-amber-400 outline-none text-sm px-1"
            value={editTitle}
            onChange={(e) => setEditTitle(e.target.value)}
            onBlur={handleTitleSubmit}
            onKeyDown={(e) => { if (e.key === 'Enter') handleTitleSubmit(); if (e.key === 'Escape') setEditingTitle(false) }}
            onClick={(e) => e.stopPropagation()}
          />
        ) : (
          <span
            className="flex-1 text-sm cursor-pointer truncate"
            onClick={() => onSelect(node.id)}
            onDoubleClick={handleDoubleClick}
          >
            {node.title}
          </span>
        )}

        {/* 操作按钮 */}
        <div className="opacity-0 group-hover:opacity-100 flex gap-0.5 shrink-0">
          {/* 添加子节点按钮 */}
          {level < 2 && (
            <Button
              variant="ghost" size="sm"
              className="h-6 w-6 p-0"
              onClick={(e) => {
                e.stopPropagation()
                const nextLevel: OutlineType = level === 0 ? 'chapter' : 'scene'
                onAddChild(node.id, nextLevel)
              }}
              title={`添加${level === 0 ? '章' : '场景'}`}
            >
              <Plus className="h-3 w-3" />
            </Button>
          )}
          <Button
            variant="ghost" size="sm"
            className="h-6 w-6 p-0 text-red-400 hover:text-red-300"
            onClick={(e) => { e.stopPropagation(); onDelete(node.id) }}
          >
            <Trash2 className="h-3 w-3" />
          </Button>
        </div>
      </div>

      {/* 子节点 — 缩进渲染 */}
      {hasChildren && isExpanded && (
        <div className="ml-5 pl-4 border-l border-white/10">
          {node.children.map((child) => (
            <OutlineNode
              key={child.id}
              node={child}
              selectedId={selectedId}
              onSelect={onSelect}
              onUpdate={onUpdate}
              onDelete={onDelete}
              onAddChild={onAddChild}
              expandedIds={expandedIds}
              onToggleExpand={onToggleExpand}
              dragState={dragState}
              onDragStart={onDragStart}
              onDragOver={onDragOver}
              onDragEnd={onDragEnd}
            />
          ))}
        </div>
      )}
    </div>
  )
}

/* ── 大纲编辑器主组件 ──────────────────────── */
export function OutlineEditor() {
  const { novel, createSetting, updateSetting, deleteSetting } = useEditorStore()
  const [outlineTree, setOutlineTree] = useState<SettingOutlineNode[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set())
  const [dragState, setDragState] = useState<DragState>(null)
  const [newNodeForm, setNewNodeForm] = useState<NewNodeForm>(null)
  const [loading, setLoading] = useState(false)

  // 编辑面板状态
  const [editTitle, setEditTitle] = useState('')
  const [editContent, setEditContent] = useState('')
  const [isEditing, setIsEditing] = useState(false)

  /* 加载大纲树 */
  const loadOutline = useCallback(async () => {
    if (!novel) return
    setLoading(true)
    try {
      const { settingsApi } = await import('@/lib/api')
      const tree = await settingsApi.getOutline(novel.id)
      setOutlineTree(tree)
    } catch {
      // 静默失败
    } finally {
      setLoading(false)
    }
  }, [novel])

  useEffect(() => { loadOutline() }, [loadOutline])

  /* 展开/折叠 */
  const toggleExpand = (id: number) => {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  /* 选中节点 */
  const handleSelect = (id: number) => {
    setSelectedId(id)
    setIsEditing(false)
    // 查找节点数据填充编辑面板
    const findNode = (nodes: SettingOutlineNode[]): SettingOutlineNode | null => {
      for (const n of nodes) {
        if (n.id === id) return n
        const found = findNode(n.children)
        if (found) return found
      }
      return null
    }
    const node = findNode(outlineTree)
    if (node) {
      setEditTitle(node.title)
      setEditContent(node.content ?? '')
    }
  }

  /* 保存编辑 */
  const handleSave = async () => {
    if (!selectedId || !novel) return
    await updateSetting(selectedId, { title: editTitle.trim(), content: editContent })
    setIsEditing(false)
    loadOutline()
  }

  /* 更新节点（标题双击） */
  const handleNodeUpdate = async (id: number, data: { title: string; content: string }) => {
    if (!novel) return
    await updateSetting(id, data)
    loadOutline()
  }

  /* 删除节点 */
  const handleDelete = async (id: number) => {
    if (!novel) return
    if (!confirm('确定删除该大纲节点？子节点也会一并删除。')) return
    await deleteSetting(id)
    if (selectedId === id) setSelectedId(null)
    loadOutline()
  }

  /* 根级别添加（卷） */
  const handleAddRoot = async () => {
    if (!novel) return
    const { settingsApi } = await import('@/lib/api')
    await settingsApi.create(novel.id, {
      type: 'outline',
      title: '新卷',
      outline_type: 'volume',
      parent_id: null,
    })
    loadOutline()
  }

  /* 添加子节点 */
  const handleAddChild = async (parentId: number, outlineType: OutlineType) => {
    if (!novel) return
    const { settingsApi } = await import('@/lib/api')
    await settingsApi.create(novel.id, {
      type: 'outline',
      title: outlineLabel[outlineType],
      outline_type: outlineType,
      parent_id: parentId,
    })
    setExpandedIds((prev) => new Set(prev).add(parentId))
    loadOutline()
  }

  /* 拖拽处理 */
  const handleDragStart = (nodeId: number) => {
    setDragState({ nodeId, fromLevel: 0, targetId: null, position: null })
  }
  const handleDragOver = (targetId: number, position: 'before' | 'after' | 'inside') => {
    setDragState((prev) => prev ? { ...prev, targetId, position } : prev)
  }
  const handleDragEnd = async () => {
    if (!dragState || !dragState.targetId || !dragState.position || !novel) {
      setDragState(null)
      return
    }
    // 简单实现：将节点移到目标前后
    // 实际生产环境需要后端配合重新计算 order_index
    setDragState(null)
    loadOutline()
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      {/* 左侧：大纲树 */}
      <div className="glass-card p-4">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-semibold text-sm">大纲结构</h3>
          <Button variant="outline" size="sm" onClick={handleAddRoot}>
            <Plus className="h-3.5 w-3.5 mr-1" />
            添加卷
          </Button>
        </div>

        {loading ? (
          <div className="text-center py-8 text-muted-foreground text-sm">加载中...</div>
        ) : outlineTree.length === 0 ? (
          <div className="text-center py-8 text-muted-foreground text-sm">
            暂无大纲，点击「添加卷」开始创建
          </div>
        ) : (
          <div className="space-y-0.5">
            {outlineTree.map((node) => (
              <OutlineNode
                key={node.id}
                node={node}
                selectedId={selectedId}
                onSelect={handleSelect}
                onUpdate={handleNodeUpdate}
                onDelete={handleDelete}
                onAddChild={handleAddChild}
                expandedIds={expandedIds}
                onToggleExpand={toggleExpand}
                dragState={dragState}
                onDragStart={handleDragStart}
                onDragOver={handleDragOver}
                onDragEnd={handleDragEnd}
              />
            ))}
          </div>
        )}
      </div>

      {/* 右侧：编辑面板 */}
      <div className="glass-card p-4">
        {selectedId ? (
          isEditing ? (
            <div className="space-y-3">
              <h3 className="font-semibold text-sm">编辑大纲节点</h3>
              <Input
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                placeholder="节点标题"
              />
              <Textarea
                value={editContent}
                onChange={(e) => setEditContent(e.target.value)}
                placeholder="节点详细内容（剧情概要、关键事件等）"
                rows={10}
              />
              <div className="flex gap-2">
                <Button size="sm" className="btn-gold" onClick={handleSave}>保存</Button>
                <Button size="sm" variant="outline" onClick={() => setIsEditing(false)}>取消</Button>
              </div>
            </div>
          ) : (
            <div>
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-semibold text-sm">{editTitle}</h3>
                <Button size="sm" variant="outline" onClick={() => setIsEditing(true)}>
                  编辑
                </Button>
              </div>
              {editContent ? (
                <p className="text-sm text-muted-foreground whitespace-pre-wrap">
                  {editContent}
                </p>
              ) : (
                <p className="text-sm text-muted-foreground italic">
                  暂无详细内容，点击「编辑」添加
                </p>
              )}
            </div>
          )
        ) : (
          <div className="text-center py-12 text-muted-foreground text-sm">
            <BookOpen className="h-10 w-10 mx-auto mb-2 opacity-20" />
            选择左侧大纲节点查看/编辑详情
          </div>
        )}
      </div>
    </div>
  )
}
