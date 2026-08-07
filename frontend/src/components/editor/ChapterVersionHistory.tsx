/**
 * 章节版本历史 — 从编辑器工具栏打开，浏览历史版本并支持一键恢复。
 */

import { useEffect, useState } from 'react'
import { History, RotateCcw, Loader2, CheckCircle2 } from 'lucide-react'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import {
  fetchChapterVersions,
  fetchChapterVersionDetail,
  restoreChapterVersion,
  type ChapterVersionSummary,
  type ChapterVersionDetail,
  type ChapterOut,
} from '@/lib/api'
import { cn } from '@/lib/utils'

interface ChapterVersionHistoryProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  novelId: number
  chapterId: number
  /** 恢复成功后，把服务端返回的最新章节对象交还给编辑器 */
  onRestored: (chapter: ChapterOut) => void
}

/** 将 HTML 章节内容转为纯文本预览（保留段落换行） */
function htmlToPlainText(html: string): string {
  return html
    .replace(/<\/(p|div|h[1-6]|li|blockquote|tr)>/gi, '\n')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<[^>]*>/g, '')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

interface ListState {
  key: string
  versions: ChapterVersionSummary[] | null
  error: string | null
}

const EMPTY_LIST_STATE: ListState = { key: '', versions: null, error: null }

export function ChapterVersionHistory({ open, onOpenChange, novelId, chapterId, onRestored }: ChapterVersionHistoryProps) {
  const listKey = `${novelId}:${chapterId}`
  const [listState, setListState] = useState<ListState>(EMPTY_LIST_STATE)
  const [reloadTick, setReloadTick] = useState(0)
  const [selectedId, setSelectedId] = useState<number | null>(null)

  const detailKey = selectedId === null ? '' : `${novelId}:${chapterId}:${selectedId}`
  const [detail, setDetail] = useState<ChapterVersionDetail | null>(null)
  const [detailFor, setDetailFor] = useState('')
  const [detailError, setDetailError] = useState<string | null>(null)

  const [confirming, setConfirming] = useState(false)
  const [restoring, setRestoring] = useState(false)
  const [restoreError, setRestoreError] = useState<string | null>(null)

  // 打开面板 / 切换章节 / 点击重试时刷新版本列表
  useEffect(() => {
    if (!open) return
    let cancelled = false
    fetchChapterVersions(novelId, chapterId)
      .then((versions) => {
        if (cancelled) return
        setListState({ key: listKey, versions, error: null })
        setSelectedId(versions[0]?.id ?? null)
        setConfirming(false)
        setRestoreError(null)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setListState({ key: listKey, versions: [], error: err instanceof Error ? err.message : '加载失败' })
      })
    return () => { cancelled = true }
  }, [open, novelId, chapterId, listKey, reloadTick])

  // 选中版本后懒加载内容
  useEffect(() => {
    if (!open || selectedId === null) return
    let cancelled = false
    fetchChapterVersionDetail(novelId, chapterId, selectedId)
      .then((versionDetail) => {
        if (cancelled) return
        setDetail(versionDetail)
        setDetailFor(detailKey)
        setDetailError(null)
        setConfirming(false)
        setRestoreError(null)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setDetailFor(detailKey)
        setDetail(null)
        setDetailError(err instanceof Error ? err.message : '加载失败')
      })
    return () => { cancelled = true }
  }, [open, novelId, chapterId, selectedId, detailKey])

  const listLoading = listState.key !== listKey
  const list = listState.key === listKey ? listState.versions : null
  const listError = listState.key === listKey ? listState.error : null

  const detailReady = detailFor === detailKey
  const detailLoading = open && selectedId !== null && !detailReady && detailError === null

  const handleRetryList = () => {
    setListState(EMPTY_LIST_STATE)
    setReloadTick((t) => t + 1)
  }

  const handleRestore = async () => {
    if (selectedId === null) return
    setRestoring(true)
    setRestoreError(null)
    try {
      const restored = await restoreChapterVersion(novelId, chapterId, selectedId)
      onRestored(restored)
    } catch (err: unknown) {
      setRestoreError(err instanceof Error ? err.message : '恢复失败')
      setConfirming(false)
    } finally {
      setRestoring(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="glass-card text-star sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="text-gold font-serif">版本历史</DialogTitle>
        </DialogHeader>

        <div className="grid grid-cols-1 sm:grid-cols-[220px_1fr] gap-4 h-[60vh] overflow-hidden">
          {/* 版本列表 */}
          <div className="flex flex-col overflow-hidden rounded-md border border-gold/15 bg-card">
            <div className="px-3 py-2 border-b border-gold/10 text-xs text-star-dim shrink-0">
              {listError ? '加载失败' : list ? `共 ${list.length} 个版本` : '版本列表'}
            </div>
            <div className="flex-1 overflow-y-auto custom-scrollbar p-2 space-y-1">
              {listLoading ? (
                <div className="flex items-center justify-center py-10 text-star-dim">
                  <Loader2 className="h-5 w-5 animate-spin" />
                </div>
              ) : listError ? (
                <div className="text-center py-8 px-3">
                  <p className="text-xs text-red-400 mb-2">{listError}</p>
                  <Button size="sm" variant="outline" onClick={handleRetryList}>
                    <RotateCcw className="h-3.5 w-3.5 mr-1" />
                    重试
                  </Button>
                </div>
              ) : list && list.length === 0 ? (
                <div className="text-center py-10 text-star-dim">
                  <History className="h-8 w-8 mx-auto mb-2 opacity-30" />
                  <p className="text-sm">暂无历史版本</p>
                </div>
              ) : (
                list?.map((v) => (
                  <button
                    key={v.id}
                    onClick={() => setSelectedId(v.id)}
                    className={cn(
                      'w-full text-left rounded-md px-3 py-2 border transition-colors',
                      selectedId === v.id
                        ? 'bg-gold/15 border-gold/25 text-star'
                        : 'border-transparent text-star-dim hover:bg-accent/10 hover:text-star',
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium">第 {v.version} 版</span>
                      <span className="text-xs shrink-0">≈{v.word_count} 字</span>
                    </div>
                    <div className="text-xs mt-0.5 opacity-80">
                      {new Date(v.created_at).toLocaleString('zh-CN')}
                    </div>
                    {v.created_by && (
                      <div className="text-xs text-gold/80 mt-0.5">作者：{v.created_by}</div>
                    )}
                  </button>
                ))
              )}
            </div>
          </div>

          {/* 内容预览 */}
          <div className="flex flex-col overflow-hidden rounded-md border border-gold/15 bg-card">
            <div className="px-3 py-2 border-b border-gold/10 shrink-0">
              {detailReady && detail ? (
                <>
                  <div className="font-medium text-sm truncate">{detail.title}</div>
                  <div className="text-xs text-star-dim mt-0.5">
                    第 {detail.version} 版 · {new Date(detail.created_at).toLocaleString('zh-CN')} · ≈{detail.word_count} 字
                    {detail.created_by ? ` · 作者 ${detail.created_by}` : ''}
                  </div>
                </>
              ) : detailError ? (
                <div className="text-xs text-red-400">加载失败：{detailError}</div>
              ) : (
                <div className="text-xs text-star-dim">选择左侧版本查看内容</div>
              )}
            </div>
            <div className="flex-1 overflow-y-auto custom-scrollbar p-4">
              {detailLoading ? (
                <div className="flex items-center justify-center h-full text-star-dim">
                  <Loader2 className="h-5 w-5 animate-spin" />
                </div>
              ) : detailError ? (
                <div className="text-sm text-red-400">{detailError}</div>
              ) : detailReady && detail ? (
                <div className="text-sm leading-relaxed whitespace-pre-wrap text-star/90">
                  {htmlToPlainText(detail.content ?? '') || '（该版本无内容）'}
                </div>
              ) : (
                <div className="text-sm text-star-dim">请选择左侧版本查看内容</div>
              )}
            </div>
            <div className="px-3 py-2 border-t border-gold/10 shrink-0">
              {restoreError && (
                <p className="text-xs text-red-400 mb-2">恢复失败：{restoreError}</p>
              )}
              {confirming ? (
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-xs text-star-dim">确定恢复该版本？当前内容将被覆盖</span>
                  <Button size="sm" variant="destructive" disabled={restoring || selectedId === null} onClick={handleRestore}>
                    {restoring ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" /> : <CheckCircle2 className="h-3.5 w-3.5 mr-1" />}
                    {restoring ? '恢复中…' : '确认恢复'}
                  </Button>
                  <Button size="sm" variant="ghost" disabled={restoring} onClick={() => setConfirming(false)}>
                    取消
                  </Button>
                </div>
              ) : (
                <div className="flex justify-end">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={selectedId === null || restoring}
                    onClick={() => setConfirming(true)}
                  >
                    <RotateCcw className="h-3.5 w-3.5 mr-1" />
                    恢复此版本
                  </Button>
                </div>
              )}
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
