/**
 * 备份管理页面 — WebDAV 配置、手动备份、远程备份与历史记录
 * 通过毛玻璃卡片布局管理 NAS / WebDAV 远程备份
 */

import { useEffect, useState } from 'react'
import {
  HardDrive, Download, Upload, History, Loader2,
  RefreshCw, AlertCircle, CheckCircle2, Wifi, Save,
  Settings, Trash2,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { backupApi, WebDAVConfig, BackupHistoryItem, BackupResult, WebDAVStatusResult } from '@/lib/api'

export function AdminBackupPage() {
  const [loading, setLoading] = useState(true)

  // WebDAV config form
  const [config, setConfig] = useState<WebDAVConfig>({ webdav_url: '', username: '', password: '' })
  const [configSaved, setConfigSaved] = useState(false)

  // WebDAV status
  const [testingConnection, setTestingConnection] = useState(false)
  const [connectionStatus, setConnectionStatus] = useState<WebDAVStatusResult | null>(null)

  // Backup operations
  const [backingUpLocal, setBackingUpLocal] = useState(false)
  const [backingUpRemote, setBackingUpRemote] = useState(false)
  const [backupResult, setBackupResult] = useState<string | null>(null)

  // History
  const [backups, setBackups] = useState<BackupHistoryItem[]>([])

  // Errors
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    loadData()
  }, [])

  const loadData = async () => {
    setLoading(true)
    setError(null)
    try {
      const [cfg, historyData] = await Promise.all([
        backupApi.getConfig().catch(() => ({ webdav_url: '', username: '', password: '' })),
        backupApi.getHistory().catch(() => ({ backups: [] })),
      ])
      setConfig(cfg)
      setBackups(historyData.backups ?? [])
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }

  /** 保存 WebDAV 配置 */
  const handleSaveConfig = async () => {
    setError(null)
    setConfigSaved(false)
    try {
      await backupApi.saveConfig(config)
      setConfigSaved(true)
      setTimeout(() => setConfigSaved(false), 3000)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '保存配置失败')
    }
  }

  /** 测试 WebDAV 连接 */
  const handleTestConnection = async () => {
    if (!config.webdav_url || !config.username) return
    setTestingConnection(true)
    setConnectionStatus(null)
    setError(null)
    try {
      const result = await backupApi.webdavStatus(config)
      setConnectionStatus(result)
    } catch (e: unknown) {
      setConnectionStatus({
        connected: false,
        message: e instanceof Error ? e.message : '连接测试失败',
        free_space_mb: null,
      })
    } finally {
      setTestingConnection(false)
    }
  }

  /** 手动本地备份 */
  const handleManualBackup = async () => {
    setBackingUpLocal(true)
    setError(null)
    setBackupResult(null)
    try {
      const result = await backupApi.manualBackup()
      if (result.success) {
        setBackupResult(`本地备份完成 (${formatBytes(result.size_bytes)})`)
        const historyData = await backupApi.getHistory()
        setBackups(historyData.backups ?? [])
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '本地备份失败')
    } finally {
      setBackingUpLocal(false)
    }
  }

  /** WebDAV 远程备份 */
  const handleWebDAVBackup = async () => {
    if (!config.webdav_url || !config.username) {
      setError('请先配置并保存 WebDAV 连接信息')
      return
    }
    setBackingUpRemote(true)
    setError(null)
    setBackupResult(null)
    try {
      const result = await backupApi.webdavBackup(config)
      if (result.success) {
        setBackupResult(`远程备份完成 — ${result.backup_path} (${formatBytes(result.size_bytes)})`)
        const historyData = await backupApi.getHistory()
        setBackups(historyData.backups ?? [])
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '远程备份失败')
    } finally {
      setBackingUpRemote(false)
    }
  }

  /** 格式化字节 */
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
          <h1 className="text-2xl font-serif text-gold">备份管理</h1>
          <p className="text-star-dim text-sm mt-1">管理 NAS / WebDAV 远程备份</p>
        </div>
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
      </div>

      {/* 错误提示 */}
      {error && (
        <div className="glass-card p-4 mb-6 border-red-400/30 flex items-center gap-2 text-red-300 text-sm">
          <AlertCircle className="h-4 w-4" />
          {error}
        </div>
      )}

      {/* 成功提示 */}
      {backupResult && (
        <div className="glass-card p-4 mb-6 border-emerald-400/30 flex items-center gap-2 text-emerald-300 text-sm">
          <CheckCircle2 className="h-4 w-4" />
          {backupResult}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* ── WebDAV 配置 ── */}
        <div className="glass-card p-6">
          <h2 className="text-lg font-serif text-gold mb-4 flex items-center gap-2">
            <Settings className="h-5 w-5" />
            WebDAV 配置
          </h2>

          <div className="space-y-4">
            <div>
              <label className="text-xs text-star-dim block mb-1">WebDAV 地址</label>
              <input
                type="text"
                value={config.webdav_url}
                onChange={(e) => setConfig({ ...config, webdav_url: e.target.value })}
                placeholder="https://nas.local/backup/"
                className="w-full bg-accent/10 border border-gold/10 rounded-lg px-3 py-2 text-sm text-star
                  placeholder:text-star-dim/40 focus:outline-none focus:border-gold/40 transition-colors"
              />
            </div>

            <div>
              <label className="text-xs text-star-dim block mb-1">用户名</label>
              <input
                type="text"
                value={config.username}
                onChange={(e) => setConfig({ ...config, username: e.target.value })}
                placeholder="WebDAV 用户名"
                className="w-full bg-accent/10 border border-gold/10 rounded-lg px-3 py-2 text-sm text-star
                  placeholder:text-star-dim/40 focus:outline-none focus:border-gold/40 transition-colors"
              />
            </div>

            <div>
              <label className="text-xs text-star-dim block mb-1">密码</label>
              <input
                type="password"
                value={config.password}
                onChange={(e) => setConfig({ ...config, password: e.target.value })}
                placeholder="WebDAV 密码"
                className="w-full bg-accent/10 border border-gold/10 rounded-lg px-3 py-2 text-sm text-star
                  placeholder:text-star-dim/40 focus:outline-none focus:border-gold/40 transition-colors"
              />
            </div>

            <div className="flex gap-2 pt-2">
              <Button
                onClick={handleSaveConfig}
                className="gap-1"
                variant="ghost"
              >
                {configSaved ? (
                  <CheckCircle2 className="h-4 w-4 text-emerald-400" />
                ) : (
                  <Save className="h-4 w-4" />
                )}
                {configSaved ? '已保存' : '保存配置'}
              </Button>
              <Button
                onClick={handleTestConnection}
                disabled={testingConnection || !config.webdav_url}
                variant="ghost"
                className="gap-1"
              >
                {testingConnection ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Wifi className="h-4 w-4" />
                )}
                测试连接
              </Button>
            </div>

            {/* 连接状态 */}
            {connectionStatus && (
              <div
                className={`p-3 rounded-lg text-sm flex items-center gap-2 ${
                  connectionStatus.connected
                    ? 'bg-emerald-400/10 border border-emerald-400/20 text-emerald-300'
                    : 'bg-red-400/10 border border-red-400/20 text-red-300'
                }`}
              >
                {connectionStatus.connected ? (
                  <CheckCircle2 className="h-4 w-4" />
                ) : (
                  <AlertCircle className="h-4 w-4" />
                )}
                <span>{connectionStatus.message}</span>
                {connectionStatus.free_space_mb != null && (
                  <span className="ml-auto text-xs opacity-70">
                    可用: {connectionStatus.free_space_mb.toFixed(0)} MB
                  </span>
                )}
              </div>
            )}
          </div>
        </div>

        {/* ── 备份操作 ── */}
        <div className="glass-card p-6">
          <h2 className="text-lg font-serif text-gold mb-4 flex items-center gap-2">
            <HardDrive className="h-5 w-5" />
            备份操作
          </h2>

          <div className="space-y-4">
            {/* 本地备份 */}
            <div className="p-4 rounded-lg border border-gold/10 bg-accent/5">
              <h3 className="text-sm font-medium text-star mb-2 flex items-center gap-2">
                <Download className="h-4 w-4 text-gold" />
                本地完整备份
              </h3>
              <p className="text-xs text-star-dim mb-3">
                备份数据库与数据文件（小说内容、设定等），保存为 tar.gz 到本地备份目录。
              </p>
              <Button
                onClick={handleManualBackup}
                disabled={backingUpLocal}
                className="btn-gold text-white gap-1 text-sm"
              >
                {backingUpLocal ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Download className="h-4 w-4" />
                )}
                {backingUpLocal ? '备份中...' : '执行本地备份'}
              </Button>
            </div>

            {/* WebDAV 备份 */}
            <div className="p-4 rounded-lg border border-gold/10 bg-accent/5">
              <h3 className="text-sm font-medium text-star mb-2 flex items-center gap-2">
                <Upload className="h-4 w-4 text-accent-cyan" />
                WebDAV 远程备份
              </h3>
              <p className="text-xs text-star-dim mb-3">
                创建完整备份并自动上传到配置的 WebDAV 服务器（如 NAS）。请先在上方配置并测试连接。
              </p>
              <Button
                onClick={handleWebDAVBackup}
                disabled={backingUpRemote || !config.webdav_url}
                variant="ghost"
                className="gap-1 text-sm"
              >
                {backingUpRemote ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Upload className="h-4 w-4" />
                )}
                {backingUpRemote ? '上传中...' : '备份到 WebDAV'}
              </Button>
            </div>
          </div>
        </div>
      </div>

      {/* ── 备份历史 ── */}
      <div className="glass-card p-6 mt-6">
        <h2 className="text-lg font-serif text-gold mb-4 flex items-center gap-2">
          <History className="h-5 w-5" />
          备份历史
        </h2>

        {backups.length === 0 ? (
          <div className="text-center py-8">
            <HardDrive className="h-10 w-10 text-star-dim mx-auto mb-2" />
            <p className="text-star-dim text-sm">暂无备份记录</p>
            <p className="text-star-dim/50 text-xs mt-1">执行本地备份后将在此显示历史记录</p>
          </div>
        ) : (
          <div className="space-y-2">
            {backups.map((backup) => (
              <div
                key={backup.filename}
                className="flex items-center justify-between py-3 px-4 rounded-lg bg-accent/5 border border-gold/5"
              >
                <div className="flex items-center gap-3">
                  <HardDrive className="h-4 w-4 text-star-dim" />
                  <div>
                    <p className="text-sm text-star font-mono">{backup.filename}</p>
                    <p className="text-xs text-star-dim">
                      {backup.created_at}
                    </p>
                  </div>
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
  )
}
