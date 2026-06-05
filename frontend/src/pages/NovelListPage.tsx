import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { BookOpen, Plus, Trash2, Edit3, Download, Loader2, Search } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogClose,
} from '@/components/ui/dialog'
import { useEditorStore } from '@/store/editor'
import { exportApi } from '@/lib/api'

const EXPORT_FORMATS = [
  { id: 'txt', label: '纯文本 TXT' },
  { id: 'epub', label: '电子书 EPUB' },
  { id: 'docx', label: 'Word 文档 DOCX' },
  { id: 'pdf', label: 'PDF 文档' },
  { id: 'html', label: 'HTML 网页' },
]

export function NovelListPage() {
  const navigate = useNavigate()
  const { novels, loading, loadNovels, searchNovels, createNovel, deleteNovel } = useEditorStore()
  const [showCreate, setShowCreate] = useState(false)
  const [title, setTitle] = useState('')
  const [synopsis, setSynopsis] = useState('')
  const [genre, setGenre] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')

  // Export dialog state
  const [exportNovelId, setExportNovelId] = useState<number | null>(null)
  const [exporting, setExporting] = useState(false)

  useEffect(() => {
    loadNovels()
  }, [loadNovels])

  // Debounced search
  const handleSearch = useCallback(
    (value: string) => {
      setSearchQuery(value)
      if (value.trim()) {
        searchNovels(value.trim())
      } else {
        loadNovels()
      }
    },
    [searchNovels, loadNovels],
  )

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!title.trim()) return
    setSubmitting(true)
    try {
      const novel = await createNovel({ title: title.trim(), synopsis, genre })
      setShowCreate(false)
      setTitle('')
      setSynopsis('')
      setGenre('')
      navigate(`/editor/${novel.id}`)
    } finally {
      setSubmitting(false)
    }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('确定要删除这本小说吗？所有章节和设定将被永久删除。')) return
    await deleteNovel(id)
  }

  const handleExport = async (format: string) => {
    if (exportNovelId === null) return
    setExporting(true)
    try {
      await exportApi.downloadNovel(exportNovelId, format)
      setExportNovelId(null)
    } catch (err) {
      console.error('Export failed:', err)
      alert('导出失败，请重试')
    } finally {
      setExporting(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">我的项目</h1>
        <div className="flex items-center gap-3">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              className="pl-9 h-9 w-56"
              placeholder="搜索小说..."
              value={searchQuery}
              onChange={(e) => handleSearch(e.target.value)}
            />
          </div>
          <Button onClick={() => setShowCreate(true)}>
            <Plus className="h-4 w-4 mr-2" />
            新建小说
          </Button>
        </div>
      </div>

      {novels.length === 0 ? (
        <div className="text-center py-20 text-muted-foreground">
          <BookOpen className="h-16 w-16 mx-auto mb-4 opacity-30" />
          <p className="text-lg">还没有小说项目</p>
          <p className="text-sm mt-1">点击「新建小说」开始创作</p>
          <Button className="mt-4" onClick={() => setShowCreate(true)}>
            <Plus className="h-4 w-4 mr-2" />
            新建小说
          </Button>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {novels.map((novel) => (
            <div
              key={novel.id}
              className="bg-card border rounded-lg p-5 hover:shadow-md transition-shadow cursor-pointer group"
              onClick={() => navigate(`/editor/${novel.id}`)}
            >
              <div className="flex items-start justify-between">
                <h3 className="font-semibold text-lg truncate flex-1">{novel.title}</h3>
                <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity ml-2">
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8"
                    onClick={(e) => {
                      e.stopPropagation()
                      setExportNovelId(novel.id)
                    }}
                    title="导出"
                  >
                    <Download className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8"
                    onClick={(e) => { e.stopPropagation(); /* edit */ }}
                  >
                    <Edit3 className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8 text-destructive"
                    onClick={(e) => { e.stopPropagation(); handleDelete(novel.id) }}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </div>
              {novel.genre && (
                <span className="inline-block text-xs bg-secondary px-2 py-0.5 rounded mt-2">
                  {novel.genre}
                </span>
              )}
              {novel.synopsis && (
                <p className="text-sm text-muted-foreground mt-3 line-clamp-3">{novel.synopsis}</p>
              )}
              <p className="text-xs text-muted-foreground mt-4">
                更新于 {new Date(novel.updated_at).toLocaleDateString('zh-CN')}
              </p>
            </div>
          ))}
        </div>
      )}

      {/* Create novel dialog */}
      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新建小说</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleCreate} className="space-y-4">
            <div>
              <Label htmlFor="title">书名 *</Label>
              <Input
                id="title"
                placeholder="输入小说标题"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                required
              />
            </div>
            <div>
              <Label htmlFor="genre">分类</Label>
              <Input
                id="genre"
                placeholder="如：玄幻、都市、科幻"
                value={genre}
                onChange={(e) => setGenre(e.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="synopsis">简介</Label>
              <Input
                id="synopsis"
                placeholder="简短描述你的故事"
                value={synopsis}
                onChange={(e) => setSynopsis(e.target.value)}
              />
            </div>
            <div className="flex justify-end gap-2">
              <DialogClose asChild>
                <Button type="button" variant="outline">取消</Button>
              </DialogClose>
              <Button type="submit" disabled={submitting || !title.trim()}>
                {submitting ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
                创建
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      {/* Export format selection dialog */}
      <Dialog open={exportNovelId !== null} onOpenChange={(open) => { if (!open) setExportNovelId(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>选择导出格式</DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            {EXPORT_FORMATS.map((fmt) => (
              <Button
                key={fmt.id}
                variant="outline"
                className="w-full justify-start"
                disabled={exporting}
                onClick={() => handleExport(fmt.id)}
              >
                {exporting ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
                {fmt.label}
              </Button>
            ))}
          </div>
          <div className="flex justify-end">
            <DialogClose asChild>
              <Button variant="ghost" size="sm">取消</Button>
            </DialogClose>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
