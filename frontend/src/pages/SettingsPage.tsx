import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  BookOpen, Users, Globe, Eye, Plus, Trash2, Edit2
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { useEditorStore } from '@/store/editor'
import { type SettingOut } from '@/lib/api'
import { OutlineEditor } from '@/components/settings/OutlineEditor'
import { CharacterCards } from '@/components/settings/CharacterCard'
import { ForeshadowTracker } from '@/components/settings/ForeshadowTracker'

/** Tab 定义 */
interface TabDef {
  key: string
  label: string
  icon: React.ReactNode
}

const TABS: TabDef[] = [
  { key: 'outline', label: '大纲', icon: <BookOpen className="h-4 w-4" /> },
  { key: 'character', label: '角色', icon: <Users className="h-4 w-4" /> },
  { key: 'worldview', label: '世界观', icon: <Globe className="h-4 w-4" /> },
  { key: 'foreshadow', label: '伏笔', icon: <Eye className="h-4 w-4" /> },
]

/* ── 世界观 Tab — 分类列表 ──────────────── */
function WorldviewTab() {
  const { novel, settings, createSetting, updateSetting, deleteSetting } = useEditorStore()
  const [activeCategory, setActiveCategory] = useState<string>('all')
  const [editingId, setEditingId] = useState<number | null>(null)
  const [showNew, setShowNew] = useState(false)
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')

  /* 通过 metadata_json 中的 category 字段分类 */
  const getCategory = (s: SettingOut): string => {
    if (!s.metadata_json) return '其他'
    try {
      const m = JSON.parse(s.metadata_json)
      return m.category ?? '其他'
    } catch { return '其他' }
  }

  const worldviews = settings.filter((s) => s.type === 'worldview')
  const categories = ['all', ...new Set(worldviews.map(getCategory))]
  const filtered = activeCategory === 'all'
    ? worldviews
    : worldviews.filter((s) => getCategory(s) === activeCategory)

  const categoryLabels: Record<string, string> = {
    all: '全部',
    '势力': '势力',
    '地理': '地理',
    '历史': '历史',
    '规则': '规则',
    '其他': '其他',
  }

  const handler = {
    new: async () => {
      if (!novel || !title.trim()) return
      const meta = JSON.stringify({ category: activeCategory === 'all' ? '其他' : activeCategory })
      await createSetting({ type: 'worldview', title: title.trim(), content, metadata_json: meta })
      setShowNew(false)
      setTitle('')
      setContent('')
    },
    update: async (id: number) => {
      if (!title.trim() || !novel) return
      const s = settings.find((x) => x.id === id)
      await updateSetting(id, { title: title.trim(), content, metadata_json: s?.metadata_json })
      setEditingId(null)
      setTitle('')
      setContent('')
    },
    startEdit: (s: SettingOut) => {
      setEditingId(s.id)
      setTitle(s.title)
      setContent(s.content ?? '')
    },
    cancel: () => {
      setEditingId(null)
      setShowNew(false)
      setTitle('')
      setContent('')
    },
  }

  return (
    <div className="space-y-4">
      {/* 分类标签 */}
      <div className="flex flex-wrap gap-1.5">
        {categories.map((cat) => (
          <button
            key={cat}
            className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
              activeCategory === cat
                ? 'bg-amber-500/20 text-amber-400'
                : 'text-muted-foreground hover:text-foreground bg-white/5'
            }`}
            onClick={() => setActiveCategory(cat)}
          >
            {categoryLabels[cat] ?? cat}
          </button>
        ))}
      </div>

      {/* 添加按钮 */}
      {!showNew && (
        <Button variant="outline" className="w-full" onClick={() => setShowNew(true)}>
          <Plus className="h-4 w-4 mr-2" />
          添加世界观条目
        </Button>
      )}

      {/* 新建表单 */}
      {showNew && (
        <div className="glass-card p-4 space-y-3">
          <h4 className="font-semibold text-sm">新建世界观条目</h4>
          <Input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="标题（设定名称）"
            autoFocus
          />
          <Textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder="详细描述..."
            rows={5}
          />
          <div className="flex gap-2">
            <Button size="sm" className="btn-gold" onClick={handler.new}>创建</Button>
            <Button size="sm" variant="outline" onClick={handler.cancel}>取消</Button>
          </div>
        </div>
      )}

      {/* 条目列表 */}
      {filtered.length === 0 && !showNew ? (
        <div className="text-center py-12 text-muted-foreground text-sm">
          <Globe className="h-10 w-10 mx-auto mb-2 opacity-20" />
          暂{activeCategory === 'all' ? '无' : `无「${categoryLabels[activeCategory] ?? activeCategory}」分类的`}世界观设定
        </div>
      ) : (
        <div className="space-y-3">
          {filtered.map((s) => (
            <div key={s.id} className="glass-card p-4">
              {editingId === s.id ? (
                <div className="space-y-3">
                  <Input
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    placeholder="标题"
                  />
                  <Textarea
                    value={content}
                    onChange={(e) => setContent(e.target.value)}
                    placeholder="内容"
                    rows={5}
                  />
                  <div className="flex gap-2">
                    <Button size="sm" onClick={() => handler.update(s.id)}>保存</Button>
                    <Button size="sm" variant="outline" onClick={handler.cancel}>取消</Button>
                  </div>
                </div>
              ) : (
                <div>
                  <div className="flex items-start justify-between">
                    <div>
                      <div className="flex items-center gap-2">
                        <h3 className="font-semibold">{s.title}</h3>
                        <span className="text-xs px-1.5 py-0.5 rounded bg-white/10 text-muted-foreground">
                          {getCategory(s)}
                        </span>
                      </div>
                      {s.content && (
                        <p className="text-sm text-muted-foreground mt-2 whitespace-pre-wrap">
                          {s.content}
                        </p>
                      )}
                    </div>
                    <div className="flex gap-1">
                      <Button variant="ghost" size="sm" onClick={() => handler.startEdit(s)}>
                        <Edit2 className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost" size="sm" className="text-destructive"
                        onClick={() => deleteSetting(s.id)}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/* ── 设定管理主页面 ──────────────────────── */
export function SettingsPage() {
  const { novel } = useEditorStore()
  const [activeTab, setActiveTab] = useState('outline')

  if (!novel) {
    return (
      <div className="p-6 max-w-6xl mx-auto">
        <h1 className="text-2xl font-bold mb-6">设定管理</h1>
        <div className="text-center py-12 text-muted-foreground">
          <BookOpen className="h-12 w-12 mx-auto mb-3 opacity-30" />
          <p>请先从项目列表打开小说</p>
        </div>
      </div>
    )
  }

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <h1 className="text-2xl font-bold mb-6">设定管理</h1>

      {/* Tabs */}
      <div className="flex border-b border-white/10 mb-6 overflow-x-auto">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            className={`flex items-center gap-2 px-5 py-2.5 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
              activeTab === tab.key
                ? 'border-amber-400 text-amber-400'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
            onClick={() => setActiveTab(tab.key)}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab 内容 */}
      {activeTab === 'outline' && <OutlineEditor />}
      {activeTab === 'character' && <CharacterCards />}
      {activeTab === 'worldview' && <WorldviewTab />}
      {activeTab === 'foreshadow' && <ForeshadowTracker />}
    </div>
  )
}
