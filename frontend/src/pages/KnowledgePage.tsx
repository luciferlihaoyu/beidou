/**
 * 知识库页面 — 三栏布局：知识库列表 | 脑图 | 条目编辑
 * 管理世界观设定、角色、剧情、伏笔等知识图谱
 */

import { useEffect, useState } from 'react'
import {
  Plus, Trash2, BookOpen, Loader2, Search,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { knowledgeApi, type KnowledgeBaseOut, type KnowledgeEntryOut } from '@/lib/api'
import { KnowledgeGraph } from '@/components/knowledge/KnowledgeGraph'
import { KnowledgeEditor } from '@/components/knowledge/KnowledgeEditor'
import { cn } from '@/lib/utils'

export function KnowledgePage() {
  // 知识库列表
  const [bases, setBases] = useState<KnowledgeBaseOut[]>([])
  const [selectedBaseId, setSelectedBaseId] = useState<number | null>(null)
  const [newBaseName, setNewBaseName] = useState('')
  const [loadingBases, setLoadingBases] = useState(true)

  // 条目
  const [entries, setEntries] = useState<KnowledgeEntryOut[]>([])
  const [selectedEntry, setSelectedEntry] = useState<KnowledgeEntryOut | null>(null)
  const [loadingEntries, setLoadingEntries] = useState(false)

  useEffect(() => {
    loadBases()
  }, [])

  const loadBases = async () => {
    setLoadingBases(true)
    try {
      const list = await knowledgeApi.listBases()
      setBases(list)
    } catch {
      // 静默处理
    } finally {
      setLoadingBases(false)
    }
  }

  /** 选择知识库时加载条目 */
  const selectBase = async (baseId: number) => {
    setSelectedBaseId(baseId)
    setSelectedEntry(null)
    setLoadingEntries(true)
    try {
      const list = await knowledgeApi.listEntries(baseId)
      setEntries(list)
    } catch {
      setEntries([])
    } finally {
      setLoadingEntries(false)
    }
  }

  /** 新建知识库 */
  const createBase = async () => {
    const name = newBaseName.trim()
    if (!name) return
    try {
      const created = await knowledgeApi.createBase({ name })
      setBases(prev => [...prev, created])
      setNewBaseName('')
      selectBase(created.id)
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '创建失败')
    }
  }

  /** 删除知识库 */
  const deleteBase = async (id: number) => {
    if (!confirm('确定删除此知识库？所有条目将被删除。')) return
    try {
      await knowledgeApi.deleteBase(id)
      setBases(prev => prev.filter(b => b.id !== id))
      if (selectedBaseId === id) {
        setSelectedBaseId(null)
        setEntries([])
        setSelectedEntry(null)
      }
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '删除失败')
    }
  }

  /** 条目变更后刷新 */
  const refreshEntries = async () => {
    if (!selectedBaseId) return
    try {
      const list = await knowledgeApi.listEntries(selectedBaseId)
      setEntries(list)
    } catch {
      // 静默
    }
  }

  return (
    <div className="flex h-full overflow-hidden">
      {/* 左栏 — 知识库列表 */}
      <aside className="w-64 shrink-0 glass-card border-0 border-r border-gold/15 rounded-none flex flex-col">
        <div className="p-4 border-b border-gold/10">
          <h2 className="text-sm font-serif text-gold mb-3 flex items-center gap-2">
            <BookOpen className="h-4 w-4" />
            知识库
          </h2>
          {/* 新建知识库 */}
          <div className="flex gap-2">
            <Input
              value={newBaseName}
              onChange={e => setNewBaseName(e.target.value)}
              placeholder="新建知识库..."
              className="h-8 text-xs glass-card border-gold/20 focus:border-gold/40 text-star"
              onKeyDown={e => e.key === 'Enter' && createBase()}
            />
            <Button
              size="sm"
              className="btn-gold text-white h-8 w-8 p-0 shrink-0"
              onClick={createBase}
            >
              <Plus className="h-4 w-4" />
            </Button>
          </div>
        </div>

        {/* 知识库列表 */}
        <div className="flex-1 overflow-y-auto custom-scrollbar p-2">
          {loadingBases ? (
            <div className="flex justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-star-dim" />
            </div>
          ) : bases.length === 0 ? (
            <p className="text-xs text-star-dim text-center py-8">暂无知识库</p>
          ) : (
            <div className="space-y-1">
              {bases.map(base => (
                <div
                  key={base.id}
                  className={cn(
                    'flex items-center justify-between px-3 py-2 rounded-md cursor-pointer transition-colors group',
                    selectedBaseId === base.id
                      ? 'bg-gold/15 text-gold border border-gold/20'
                      : 'text-star-dim hover:bg-accent/10 hover:text-star',
                  )}
                  onClick={() => selectBase(base.id)}
                >
                  <div className="flex-1 min-w-0">
                    <p className="text-sm truncate">{base.name}</p>
                    {base.description && (
                      <p className="text-xs text-star-dim/70 truncate">{base.description}</p>
                    )}
                  </div>
                  <button
                    onClick={e => { e.stopPropagation(); deleteBase(base.id); }}
                    className="opacity-0 group-hover:opacity-100 text-star-dim hover:text-red-400 transition-all"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </aside>

      {/* 中间 — 脑图 / 条目编辑器（切换） */}
      <div className="flex-1 flex flex-col min-w-0">
        {selectedBaseId ? (
          <>
            {/* 工具栏 */}
            <div className="h-10 glass-card border-0 border-b border-gold/15 rounded-none flex items-center px-4 shrink-0 gap-2">
              <span className="text-xs text-star-dim font-serif">
                {bases.find(b => b.id === selectedBaseId)?.name ?? '知识库'}
              </span>

              <button
                onClick={() => setSelectedEntry(null)}
                className={cn(
                  'text-xs px-3 py-1 rounded transition-colors',
                  !selectedEntry ? 'bg-gold/15 text-gold' : 'text-star-dim hover:text-star',
                )}
              >
                关系图
              </button>
              <button
                onClick={() => {
                  if (entries.length > 0) setSelectedEntry(entries[0])
                }}
                className={cn(
                  'text-xs px-3 py-1 rounded transition-colors',
                  selectedEntry ? 'bg-gold/15 text-gold' : 'text-star-dim hover:text-star',
                )}
              >
                条目编辑
              </button>

              {loadingEntries && (
                <Loader2 className="h-3 w-3 animate-spin text-star-dim ml-2" />
              )}
            </div>

            {/* 内容区 */}
            <div className="flex-1 overflow-hidden">
              {selectedEntry ? (
                <KnowledgeEditor
                  entry={selectedEntry}
                  baseId={selectedBaseId}
                  entries={entries}
                  onSave={refreshEntries}
                  onDelete={() => {
                    setSelectedEntry(null)
                    refreshEntries()
                  }}
                />
              ) : (
                <KnowledgeGraph
                  baseId={selectedBaseId}
                  entries={entries}
                  onEntryClick={setSelectedEntry}
                  onEntriesChange={refreshEntries}
                />
              )}
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-star-dim">
            <div className="text-center">
              <BookOpen className="h-12 w-12 mx-auto mb-3 opacity-30" />
              <p>选择或创建一个知识库开始</p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
