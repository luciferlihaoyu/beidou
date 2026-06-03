/**
 * 管理仪表盘 — 统计概览 + 快捷入口
 * 展示系统整体运行状态的毛玻璃卡片视图
 */

import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Users, BookOpen, Cpu, Database, Brain,
  ChevronRight, Loader2, HardDrive, Table2, FileText,
} from 'lucide-react'
import { databaseApi, novelsApi, agentsApi, knowledgeApi } from '@/lib/api'
import { cn } from '@/lib/utils'

/** 统计卡片属性 */
interface StatCard {
  label: string
  value: number | string
  icon: React.ElementType
  color: string
  link: string
}

export function AdminDashboard() {
  const [loading, setLoading] = useState(true)
  const [stats, setStats] = useState<StatCard[]>([])
  const [dbStats, setDbStats] = useState<{
    tables: Record<string, number>
    total_records: number
    db_size_mb: number
  } | null>(null)

  useEffect(() => {
    loadAllStats()
  }, [])

  const loadAllStats = async () => {
    setLoading(true)
    try {
      // 并行请求所有统计数据
      const [dbData, novelList, agentList, kbList] = await Promise.all([
        databaseApi.getStats().catch(() => null),
        novelsApi.list().catch(() => []),
        agentsApi.list().catch(() => []),
        knowledgeApi.listBases().catch(() => []),
      ])

      if (dbData) {
        setDbStats(dbData)
      }

      const cards: StatCard[] = [
        {
          label: '小说项目',
          value: novelList.length,
          icon: BookOpen,
          color: 'text-accent-emerald',
          link: '/',
        },
        {
          label: 'AI Agent',
          value: agentList.length,
          icon: Cpu,
          color: 'text-accent-purple',
          link: '/admin/agents',
        },
        {
          label: '知识库',
          value: kbList.length,
          icon: Brain,
          color: 'text-accent-cyan',
          link: '/knowledge',
        },
        {
          label: '数据库大小',
          value: dbData ? `${dbData.db_size_mb.toFixed(1)} MB` : '--',
          icon: Database,
          color: 'text-gold',
          link: '/admin/database',
        },
      ]
      setStats(cards)
    } catch {
      // 静默处理
    } finally {
      setLoading(false)
    }
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
      <div className="mb-8">
        <h1 className="text-2xl font-serif text-gold mb-1">管理仪表盘</h1>
        <p className="text-star-dim text-sm">系统运行概览与快捷管理入口</p>
      </div>

      {/* 统计卡片网格 */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        {stats.map((card) => (
          <Link
            key={card.label}
            to={card.link}
            className={cn(
              'glass-card p-5 transition-all duration-300',
              'hover:border-gold/30 hover:shadow-lg hover:shadow-gold/5',
              'group',
            )}
          >
            <div className="flex items-start justify-between">
              <div>
                <p className="text-star-dim text-xs mb-1">{card.label}</p>
                <p className="text-2xl font-bold text-star">{card.value}</p>
              </div>
              <div className={cn('p-2 rounded-lg bg-accent/5', card.color)}>
                <card.icon className="h-5 w-5" />
              </div>
            </div>
            <div className="flex items-center gap-1 mt-3 text-xs text-star-dim opacity-0 group-hover:opacity-100 transition-opacity">
              查看详情 <ChevronRight className="h-3 w-3" />
            </div>
          </Link>
        ))}
      </div>

      {/* 数据库详情 & 快捷操作 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* 数据库概览 */}
        {dbStats && (
          <div className="glass-card p-6">
            <h2 className="text-lg font-serif text-gold mb-4 flex items-center gap-2">
              <Database className="h-5 w-5" />
              数据库概览
            </h2>
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-star-dim text-sm flex items-center gap-2">
                  <HardDrive className="h-4 w-4" />
                  总大小
                </span>
                <span className="text-star font-mono">{dbStats.db_size_mb.toFixed(2)} MB</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-star-dim text-sm flex items-center gap-2">
                  <Table2 className="h-4 w-4" />
                  数据表
                </span>
                <span className="text-star font-mono">{Object.keys(dbStats.tables).length} 张</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-star-dim text-sm flex items-center gap-2">
                  <FileText className="h-4 w-4" />
                  总记录数
                </span>
                <span className="text-star font-mono">{dbStats.total_records.toLocaleString()}</span>
              </div>

              {/* 各表记录数 */}
              <div className="mt-4 pt-4 border-t border-gold/10">
                <p className="text-xs text-star-dim mb-2">各表记录数</p>
                <div className="grid grid-cols-2 gap-2">
                  {Object.entries(dbStats.tables).map(([table, count]) => (
                    <div key={table} className="flex justify-between text-xs">
                      <span className="text-star-dim">{table}</span>
                      <span className="text-star font-mono">{count}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* 快捷操作 */}
        <div className="glass-card p-6">
          <h2 className="text-lg font-serif text-gold mb-4 flex items-center gap-2">
            <Cpu className="h-5 w-5" />
            快捷操作
          </h2>
          <div className="grid grid-cols-2 gap-3">
            <Link
              to="/admin/agents"
              className="flex flex-col items-center justify-center p-4 rounded-lg border border-gold/10 hover:border-gold/30 transition-colors text-center group"
            >
              <Cpu className="h-6 w-6 text-accent-purple mb-2 group-hover:scale-110 transition-transform" />
              <span className="text-sm text-star">Agent 管理</span>
            </Link>
            <Link
              to="/admin/models"
              className="flex flex-col items-center justify-center p-4 rounded-lg border border-gold/10 hover:border-gold/30 transition-colors text-center group"
            >
              <Brain className="h-6 w-6 text-accent-cyan mb-2 group-hover:scale-110 transition-transform" />
              <span className="text-sm text-star">模型配置</span>
            </Link>
            <Link
              to="/admin/database"
              className="flex flex-col items-center justify-center p-4 rounded-lg border border-gold/10 hover:border-gold/30 transition-colors text-center group"
            >
              <Database className="h-6 w-6 text-gold mb-2 group-hover:scale-110 transition-transform" />
              <span className="text-sm text-star">数据库管理</span>
            </Link>
            <Link
              to="/admin/users"
              className="flex flex-col items-center justify-center p-4 rounded-lg border border-gold/10 hover:border-gold/30 transition-colors text-center group"
            >
              <Users className="h-6 w-6 text-accent-emerald mb-2 group-hover:scale-110 transition-transform" />
              <span className="text-sm text-star">用户管理</span>
            </Link>
            <Link
              to="/admin/backup"
              className="flex flex-col items-center justify-center p-4 rounded-lg border border-gold/10 hover:border-gold/30 transition-colors text-center group col-span-2"
            >
              <HardDrive className="h-6 w-6 text-gold mb-2 group-hover:scale-110 transition-transform" />
              <span className="text-sm text-star">备份管理</span>
            </Link>
          </div>
        </div>
      </div>
    </div>
  )
}
