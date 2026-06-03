/**
 * 数据库管理页面 — 统计信息、备份管理与备份历史
 * 展示数据库整体状态并提供手动备份功能
 */

import { useEffect, useState } from 'react'
import {
  Database, HardDrive, Table2, FileText, Download,
  Loader2, RefreshCw, History, AlertCircle,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { databaseApi } from '@/lib/api'

interface DbStats {
  tables: Record<string, number>
  total_records: number
  db_size_mb: number
  db_path: string
}

interface BackupItem {
  filename: string
  size_bytes: number
  created_at: string
}

export function AdminDatabasePage() {
  const [loading, setLoading] = useState(true)
  const [stats, setStats] = useState<DbStats | null>(null)
  const [backups, setBackups] = useState<BackupItem[]>([])
  const [backingUp, setBackingUp] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    loadData()
  }, [])

  const loadData = async () => {
    setLoading(true)
    setError(null)
    try {
      const [dbStats, backupList] = await Promise.all([
        databaseApi.getStats(),
        databaseApi.listBackups(),
      ])
      setStats(dbStats)
      setBackups(backupList?.backups ?? [])
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }

  /** 执行备份 */
  const handleBackup = async () => {
    setBackingUp(true)
    setError(null)
    try {
      const result = await databaseApi.backup()
      if (result.success) {
        // 刷新备份列表
        const backupList = await databaseApi.listBackups()
        setBackups(backupList?.backups ?? [])
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '备份失败')
    } finally {
      setBackingUp(false)
    }
  }

  /** 格式化字节大小 */
  const formatBytes = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="h-8 w-8 animate-spin text-star-dim" />
      </div>
    )
  }

  return (
    <div className="p-6 max-w-6xl mx-auto animate-fade-in">
      {/* 页面标题 */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-serif text-gold">数据库管理</h1>
          <p className="text-star-dim text-sm mt-1">管理系统数据库及备份</p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="ghost"
            size="sm"
            className="text-star-dim hover:text-star"
            onClick={loadData}
            disabled={loading}
          >
            <RefreshCw className="h-4 w-4 mr-1" />
            刷新
          </Button>
          <Button
            onClick={handleBackup}
            disabled={backingUp}
            className="btn-gold text-white gap-1"
          >
            {backingUp ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Download className="h-4 w-4" />
            )}
            {backingUp ? '备份中...' : '立即备份'}
          </Button>
        </div>
      </div>

      {/* 错误提示 */}
      {error && (
        <div className="glass-card p-4 mb-6 border-red-400/30 flex items-center gap-2 text-red-300 text-sm">
          <AlertCircle className="h-4 w-4" />
          {error}
        </div>
      )}

      {/* 统计卡片 */}
      {stats && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-8">
          <div className="glass-card p-5">
            <div className="flex items-center gap-2 text-gold mb-2">
              <HardDrive className="h-5 w-5" />
              <span className="text-sm font-medium">数据库大小</span>
            </div>
            <p className="text-3xl font-bold text-star">{stats.db_size_mb.toFixed(2)} MB</p>
            <p className="text-xs text-star-dim mt-1">路径: {stats.db_path}</p>
          </div>

          <div className="glass-card p-5">
            <div className="flex items-center gap-2 text-accent-cyan mb-2">
              <Table2 className="h-5 w-5" />
              <span className="text-sm font-medium">数据表</span>
            </div>
            <p className="text-3xl font-bold text-star">{Object.keys(stats.tables).length}</p>
            <p className="text-xs text-star-dim mt-1">张数据表</p>
          </div>

          <div className="glass-card p-5">
            <div className="flex items-center gap-2 text-accent-emerald mb-2">
              <FileText className="h-5 w-5" />
              <span className="text-sm font-medium">总记录数</span>
            </div>
            <p className="text-3xl font-bold text-star">{stats.total_records.toLocaleString()}</p>
            <p className="text-xs text-star-dim mt-1">条记录</p>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* 各表记录数 */}
        {stats && (
          <div className="glass-card p-6">
            <h2 className="text-lg font-serif text-gold mb-4 flex items-center gap-2">
              <Database className="h-5 w-5" />
              各表记录数
            </h2>
            <div className="space-y-2">
              {Object.entries(stats.tables)
                .sort(([, a], [, b]) => b - a)
                .map(([table, count]) => (
                  <div
                    key={table}
                    className="flex items-center justify-between py-2 border-b border-gold/5 last:border-0"
                  >
                    <span className="text-sm text-star">{table}</span>
                    <div className="flex items-center gap-3">
                      {/* 进度条示意 */}
                      <div className="w-24 h-1.5 rounded-full bg-accent/10 overflow-hidden hidden sm:block">
                        <div
                          className="h-full rounded-full bg-gold/40"
                          style={{
                            width: `${Math.min((count / stats.total_records) * 100, 100)}%`,
                          }}
                        />
                      </div>
                      <span className="text-sm text-star font-mono">{count.toLocaleString()}</span>
                    </div>
                  </div>
                ))}
            </div>
          </div>
        )}

        {/* 备份历史 */}
        <div className="glass-card p-6">
          <h2 className="text-lg font-serif text-gold mb-4 flex items-center gap-2">
            <History className="h-5 w-5" />
            备份历史
          </h2>

          {backups.length === 0 ? (
            <div className="text-center py-8">
              <Database className="h-10 w-10 text-star-dim mx-auto mb-2" />
              <p className="text-star-dim text-sm">暂无备份记录</p>
              <p className="text-star-dim/50 text-xs mt-1">点击「立即备份」创建数据库快照</p>
            </div>
          ) : (
            <div className="space-y-2">
              {backups.map((backup, i) => (
                <div
                  key={backup.filename}
                  className="flex items-center justify-between py-2 px-3 rounded-lg bg-accent/5 border border-gold/5"
                >
                  <div>
                    <p className="text-sm text-star font-mono">{backup.filename}</p>
                    <p className="text-xs text-star-dim">
                      {new Date(backup.created_at).toLocaleString('zh-CN')}
                    </p>
                  </div>
                  <span className="text-xs text-star-dim font-mono">
                    {formatBytes(backup.size_bytes)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
