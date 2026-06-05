import { create } from 'zustand'
import { novelsApi, chaptersApi, settingsApi, type NovelOut, type ChapterOut, type SettingOut } from '@/lib/api'

interface EditorState {
  /** Currently selected novel */
  novel: NovelOut | null
  novels: NovelOut[]
  chapters: ChapterOut[]
  /** Currently active chapter in the editor */
  activeChapter: ChapterOut | null
  settings: SettingOut[]
  loading: boolean
  saving: boolean
  autoSaveTimer: ReturnType<typeof setTimeout> | null

  loadNovels: () => Promise<void>
  searchNovels: (q: string) => Promise<void>
  selectNovel: (id: number) => Promise<void>
  createNovel: (data: Partial<NovelOut>) => Promise<NovelOut>
  updateNovel: (id: number, data: Partial<NovelOut>) => Promise<void>
  deleteNovel: (id: number) => Promise<void>

  loadChapters: () => Promise<void>
  setActiveChapter: (chapter: ChapterOut | null) => void
  createChapter: (data: Partial<ChapterOut>) => Promise<ChapterOut>
  updateChapter: (id: number, data: Partial<ChapterOut>) => Promise<void>
  deleteChapter: (id: number) => Promise<void>
  reorderChapters: (order: Record<number, number>) => Promise<void>

  loadSettings: (type?: string) => Promise<void>
  createSetting: (data: Partial<SettingOut>) => Promise<SettingOut>
  updateSetting: (id: number, data: Partial<SettingOut>) => Promise<void>
  deleteSetting: (id: number) => Promise<void>

  /** Schedule auto-save for current chapter */
  scheduleAutoSave: (content: string, wordCount: number) => void
}

export const useEditorStore = create<EditorState>((set, get) => ({
  novel: null,
  novels: [],
  chapters: [],
  activeChapter: null,
  settings: [],
  loading: false,
  saving: false,
  autoSaveTimer: null,

  loadNovels: async () => {
    set({ loading: true })
    const novels = await novelsApi.list()
    set({ novels, loading: false })
  },

  searchNovels: async (q: string) => {
    set({ loading: true })
    const novels = await novelsApi.list(q)
    set({ novels, loading: false })
  },

  selectNovel: async (id) => {
    set({ loading: true })
    const novel = await novelsApi.get(id)
    const chapters = await chaptersApi.list(id)
    const settings = await settingsApi.list(id)
    set({
      novel,
      chapters,
      settings,
      activeChapter: chapters.length > 0 ? chapters[0] : null,
      loading: false,
    })
  },

  createNovel: async (data) => {
    const novel = await novelsApi.create(data)
    set((s) => ({ novels: [...s.novels, novel] }))
    return novel
  },

  updateNovel: async (id, data) => {
    const novel = await novelsApi.update(id, data)
    set((s) => ({
      novels: s.novels.map((n) => (n.id === id ? novel : n)),
      novel: s.novel?.id === id ? novel : s.novel,
    }))
  },

  deleteNovel: async (id) => {
    await novelsApi.delete(id)
    set((s) => ({
      novels: s.novels.filter((n) => n.id !== id),
      novel: s.novel?.id === id ? null : s.novel,
    }))
  },

  loadChapters: async () => {
    const { novel } = get()
    if (!novel) return
    const chapters = await chaptersApi.list(novel.id)
    set({ chapters })
  },

  setActiveChapter: (chapter) => set({ activeChapter: chapter }),

  createChapter: async (data) => {
    const { novel } = get()
    if (!novel) throw new Error('No novel selected')
    const chapter = await chaptersApi.create(novel.id, data)
    set((s) => ({ chapters: [...s.chapters, chapter] }))
    return chapter
  },

  updateChapter: async (id, data) => {
    const { novel } = get()
    if (!novel) return
    const chapter = await chaptersApi.update(novel.id, id, data)
    set((s) => ({
      chapters: s.chapters.map((c) => (c.id === id ? chapter : c)),
      activeChapter: s.activeChapter?.id === id ? chapter : s.activeChapter,
    }))
  },

  deleteChapter: async (id) => {
    const { novel } = get()
    if (!novel) return
    await chaptersApi.delete(novel.id, id)
    set((s) => ({
      chapters: s.chapters.filter((c) => c.id !== id),
      activeChapter: s.activeChapter?.id === id ? null : s.activeChapter,
    }))
  },

  reorderChapters: async (order) => {
    const { novel } = get()
    if (!novel) return
    const chapters = await chaptersApi.reorder(novel.id, order)
    set({ chapters })
  },

  loadSettings: async (type?) => {
    const { novel } = get()
    if (!novel) return
    const settings = await settingsApi.list(novel.id, type)
    set({ settings })
  },

  createSetting: async (data) => {
    const { novel } = get()
    if (!novel) throw new Error('No novel selected')
    const setting = await settingsApi.create(novel.id, data)
    set((s) => ({ settings: [...s.settings, setting] }))
    return setting
  },

  updateSetting: async (id, data) => {
    const { novel } = get()
    if (!novel) return
    const setting = await settingsApi.update(novel.id, id, data)
    set((s) => ({
      settings: s.settings.map((st) => (st.id === id ? setting : st)),
    }))
  },

  deleteSetting: async (id) => {
    const { novel } = get()
    if (!novel) return
    await settingsApi.delete(novel.id, id)
    set((s) => ({
      settings: s.settings.filter((st) => st.id !== id),
    }))
  },

  scheduleAutoSave: (content, wordCount) => {
    const { autoSaveTimer, novel, activeChapter, updateChapter } = get()
    if (!novel || !activeChapter) return

    // Clear existing timer
    if (autoSaveTimer) clearTimeout(autoSaveTimer)

    // Schedule new auto-save after 2 seconds of inactivity
    const timer = setTimeout(async () => {
      set({ saving: true })
      try {
        await updateChapter(activeChapter.id, {
          content,
          word_count: wordCount,
        })
      } catch {
        // silently fail auto-save
      } finally {
        set({ saving: false })
      }
    }, 2000)

    set({ autoSaveTimer: timer })
  },
}))
