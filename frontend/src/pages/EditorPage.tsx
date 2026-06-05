import { useEffect, useCallback, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  Plus, Trash2, GripVertical, Loader2, ArrowLeft,
  BookOpen, MessageSquare, Save, Wand2, ListTree, ShieldCheck,
  Download, ChevronDown,
} from 'lucide-react'
import {
  DndContext,
  closestCenter,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import {
  arrayMove,
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useEditorStore } from '@/store/editor'
import { TipTapEditor } from '@/components/editor/TipTapEditor'
import { AiChatPanel } from '@/components/ai/AiChatPanel'
import { exportApi, aiApi } from '@/lib/api'
import type { ChapterOut } from '@/lib/api'

const EXPORT_FORMATS = [
  { id: 'txt', label: '纯文本 TXT' },
  { id: 'epub', label: '电子书 EPUB' },
  { id: 'docx', label: 'Word 文档 DOCX' },
  { id: 'pdf', label: 'PDF 文档' },
  { id: 'html', label: 'HTML 网页' },
]

// Sortable chapter item component
function SortableChapterItem({
  chapter,
  isActive,
  onSelect,
  onDelete,
}: {
  chapter: ChapterOut
  isActive: boolean
  onSelect: () => void
  onDelete: () => void
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: chapter.id })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`flex items-center gap-1 group rounded-md px-2 py-1.5 cursor-pointer transition-colors ${
        isActive ? 'bg-primary text-primary-foreground' : 'hover:bg-accent'
      }`}
      onClick={onSelect}
    >
      <button
        {...attributes}
        {...listeners}
        className="shrink-0 cursor-grab active:cursor-grabbing"
        onClick={(e) => e.stopPropagation()}
      >
        <GripVertical className="h-3 w-3 opacity-30" />
      </button>
      <span className="text-sm truncate flex-1">{chapter.title}</span>
      <span className="text-xs opacity-50 shrink-0">{chapter.word_count}字</span>
      <Button
        variant="ghost"
        size="icon"
        className="h-6 w-6 opacity-0 group-hover:opacity-100 shrink-0"
        onClick={(e) => {
          e.stopPropagation()
          if (confirm('删除本章？')) onDelete()
        }}
      >
        <Trash2 className="h-3 w-3" />
      </Button>
    </div>
  )
}

export function EditorPage() {
  const { novelId } = useParams<{ novelId: string }>()
  const id = Number(novelId)
  const {
    novel,
    chapters,
    activeChapter,
    loading,
    saving,
    selectNovel,
    loadChapters,
    setActiveChapter,
    createChapter,
    deleteChapter,
    updateChapter,
    scheduleAutoSave,
    reorderChapters,
  } = useEditorStore()

  // DnD sensors
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } })
  )

  const [showAI, setShowAI] = useState(false)
  const [newChapterTitle, setNewChapterTitle] = useState('')
  const [aiContent, setAiContent] = useState<string | undefined>(undefined)
  const [aiLoading, setAiLoading] = useState(false)

  // Export state
  const [showExportDropdown, setShowExportDropdown] = useState(false)
  const [exporting, setExporting] = useState(false)

  // DnD drag end handler
  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event
    if (!over || active.id === over.id) return

    const oldIndex = chapters.findIndex((c) => c.id === active.id)
    const newIndex = chapters.findIndex((c) => c.id === over.id)
    if (oldIndex === -1 || newIndex === -1) return

    const reordered = arrayMove(chapters, oldIndex, newIndex)
    // Build order map: chapter_id -> new_order_index
    const orderMap: Record<number, number> = {}
    reordered.forEach((ch, i) => {
      orderMap[ch.id] = i
    })
    reorderChapters(orderMap)
  }

  // Load novel data on mount
  useEffect(() => {
    if (id) selectNovel(id)
  }, [id, selectNovel])

  const handleCreateChapter = async () => {
    if (!newChapterTitle.trim()) return
    const ch = await createChapter({
      title: newChapterTitle.trim(),
      content: '',
      order_index: chapters.length,
    })
    setNewChapterTitle('')
    setActiveChapter(ch)
  }

  const handleContentChange = useCallback(
    (content: string) => {
      const wordCount = content.replace(/<[^>]*>/g, '').split(/\s+/).filter(Boolean).length
      scheduleAutoSave(content, wordCount)
    },
    [scheduleAutoSave],
  )

  // ── Export handler ──
  const handleExport = async (format: string) => {
    if (!id) return
    setExporting(true)
    setShowExportDropdown(false)
    try {
      await exportApi.downloadNovel(id, format)
    } catch (err) {
      console.error('Export failed:', err)
      alert('导出失败，请重试')
    } finally {
      setExporting(false)
    }
  }

  // ── AI helper: call endpoint and show result ──
  const callAiAction = async (endpoint: 'continue' | 'outline' | 'review', body: Record<string, unknown>) => {
    setAiLoading(true)
    setShowAI(true)
    try {
      const data = await aiApi[endpoint](body.novel_id as number, body.chapter_id as number)
      setAiContent(data.response ?? 'AI 未返回内容')
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '请求失败'
      setAiContent(`⚠️ 错误: ${msg}`)
    } finally {
      setAiLoading(false)
    }
  }

  const handleContinue = () => {
    if (!activeChapter) return
    callAiAction('continue', { novel_id: id, chapter_id: activeChapter.id })
  }

  const handleOutline = () => {
    callAiAction('outline', { novel_id: id })
  }

  const handleReview = () => {
    if (!activeChapter) return
    callAiAction('review', { novel_id: id, chapter_id: activeChapter.id })
  }

  if (!novelId) return null

  if (loading || !novel) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="flex h-full">
      {/* Chapter sidebar */}
      <aside className="w-64 border-r bg-card flex flex-col shrink-0">
        <div className="p-3 border-b">
          <Link to="/" className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground mb-2">
            <ArrowLeft className="h-4 w-4" />
            返回项目列表
          </Link>
          <h2 className="font-semibold truncate">{novel.title}</h2>
        </div>

        {/* Chapter list */}
        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
            <SortableContext items={chapters.map((c) => c.id)} strategy={verticalListSortingStrategy}>
              {chapters.map((ch) => (
                <SortableChapterItem
                  key={ch.id}
                  chapter={ch}
                  isActive={activeChapter?.id === ch.id}
                  onSelect={() => setActiveChapter(ch)}
                  onDelete={() => deleteChapter(ch.id)}
                />
              ))}
            </SortableContext>
          </DndContext>
        </div>

        {/* New chapter input */}
        <div className="p-2 border-t">
          <form
            onSubmit={(e) => { e.preventDefault(); handleCreateChapter() }}
            className="flex gap-1"
          >
            <Input
              className="h-8 text-sm"
              placeholder="新章节标题"
              value={newChapterTitle}
              onChange={(e) => setNewChapterTitle(e.target.value)}
            />
            <Button type="submit" size="icon" className="h-8 w-8 shrink-0" disabled={!newChapterTitle.trim()}>
              <Plus className="h-4 w-4" />
            </Button>
          </form>
        </div>
      </aside>

      {/* Editor area */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Toolbar */}
        <div className="flex items-center justify-between h-12 px-4 border-b bg-card shrink-0">
          <div className="flex items-center gap-2">
            <BookOpen className="h-4 w-4 text-muted-foreground" />
            <span className="font-medium text-sm">
              {activeChapter?.title ?? '选择章节开始写作'}
            </span>
            {saving && (
              <span className="text-xs text-muted-foreground flex items-center gap-1">
                <Save className="h-3 w-3" />
                保存中…
              </span>
            )}
          </div>
          <div className="flex items-center gap-1">
            {/* AI toolbar buttons */}
            <Button
              variant="outline"
              size="sm"
              onClick={handleContinue}
              disabled={!activeChapter || aiLoading}
              title="AI 续写"
            >
              <Wand2 className="h-4 w-4 mr-1" />
              续写
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleOutline}
              disabled={aiLoading}
              title="AI 大纲"
            >
              <ListTree className="h-4 w-4 mr-1" />
              大纲
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleReview}
              disabled={!activeChapter || aiLoading}
              title="AI 审查"
            >
              <ShieldCheck className="h-4 w-4 mr-1" />
              审查
            </Button>

            {/* Export dropdown */}
            <div className="relative">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setShowExportDropdown(!showExportDropdown)}
              >
                <Download className="h-4 w-4 mr-1" />
                导出
                <ChevronDown className="h-3 w-3 ml-1" />
              </Button>
              {showExportDropdown && (
                <div className="absolute right-0 top-full mt-1 bg-popover border rounded-md shadow-md z-50 w-44">
                  {EXPORT_FORMATS.map((fmt) => (
                    <button
                      key={fmt.id}
                      className="w-full text-left px-3 py-2 text-sm hover:bg-accent transition-colors disabled:opacity-50"
                      disabled={exporting}
                      onClick={() => handleExport(fmt.id)}
                    >
                      {fmt.label}
                    </button>
                  ))}
                </div>
              )}
            </div>

            <Button
              variant={showAI ? 'default' : 'outline'}
              size="sm"
              onClick={() => setShowAI(!showAI)}
            >
              <MessageSquare className="h-4 w-4 mr-1" />
              AI 助手
            </Button>
          </div>
        </div>

        {/* Editor content */}
        <div className="flex-1 flex overflow-hidden">
          <div className="flex-1 overflow-auto">
            {activeChapter ? (
              <div className="max-w-3xl mx-auto p-6">
                <TipTapEditor
                  key={activeChapter.id}
                  content={activeChapter.content ?? ''}
                  onChange={handleContentChange}
                />
              </div>
            ) : (
              <div className="flex items-center justify-center h-full text-muted-foreground">
                <div className="text-center">
                  <BookOpen className="h-12 w-12 mx-auto mb-3 opacity-30" />
                  <p>选择或创建章节开始写作</p>
                </div>
              </div>
            )}
          </div>

          {/* AI Chat Panel */}
          {showAI && (
            <div className="w-80 border-l bg-card shrink-0">
              {aiLoading ? (
                <div className="flex items-center justify-center h-full text-muted-foreground">
                  <Loader2 className="h-6 w-6 animate-spin" />
                </div>
              ) : (
                <AiChatPanel
                  externalContent={aiContent}
                  onExternalContentConsumed={() => setAiContent(undefined)}
                />
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
