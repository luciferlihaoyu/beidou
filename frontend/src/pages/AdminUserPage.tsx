/**
 * 用户管理页面 — 管理员审批/拒绝用户、修改角色
 * 利用 adminApi 管理用户状态和权限
 */

import { useEffect, useState } from 'react'
import {
  CheckCircle, XCircle, Shield, Loader2, Users,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { adminApi, type UserOut } from '@/lib/api'

/** 角色选项 */
const ROLES = ['admin', 'author', 'editor', 'reader'] as const

export function AdminUserPage() {
  const [users, setUsers] = useState<UserOut[]>([])
  const [loading, setLoading] = useState(true)
  const [changingRole, setChangingRole] = useState<number | null>(null)

  useEffect(() => {
    loadUsers()
  }, [])

  const loadUsers = async () => {
    setLoading(true)
    try {
      const list = await adminApi.listUsers()
      setUsers(list)
    } catch {
      // 静默处理
    } finally {
      setLoading(false)
    }
  }

  /** 批准/拒绝用户 */
  const handleStatus = async (userId: number, status: 'approved' | 'rejected') => {
    try {
      const updated = status === 'approved' ? await adminApi.approveUser(userId) : await adminApi.rejectUser(userId)
      setUsers(prev => prev.map(u => (u.id === userId ? updated : u)))
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '操作失败')
    }
  }

  /** 修改用户角色 */
  const handleRoleChange = async (userId: number, role: string) => {
    setChangingRole(userId)
    try {
      const updated = await adminApi.updateUserRole(userId, role)
      setUsers(prev => prev.map(u => (u.id === userId ? updated : u)))
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '修改角色失败')
    } finally {
      setChangingRole(null)
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
      <div className="mb-6">
        <h1 className="text-2xl font-serif text-gold">用户管理</h1>
        <p className="text-star-dim text-sm mt-1">管理用户注册、审批及角色权限</p>
      </div>

      {/* 用户表格 */}
      {users.length === 0 ? (
        <div className="glass-card p-12 text-center">
          <Users className="h-12 w-12 text-star-dim mx-auto mb-3" />
          <p className="text-star-dim">暂无用户</p>
        </div>
      ) : (
        <div className="glass-card overflow-hidden">
          {/* 响应式：大屏用表格，小屏用卡片 */}
          {/* 桌面端表格 */}
          <div className="hidden md:block">
            <table className="w-full">
              <thead>
                <tr className="border-b border-gold/10">
                  <th className="text-left px-4 py-3 text-xs font-medium text-star-dim">用户名</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-star-dim">邮箱</th>
                  <th className="text-center px-4 py-3 text-xs font-medium text-star-dim">角色</th>
                  <th className="text-center px-4 py-3 text-xs font-medium text-star-dim">状态</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-star-dim">注册时间</th>
                  <th className="text-right px-4 py-3 text-xs font-medium text-star-dim">操作</th>
                </tr>
              </thead>
              <tbody>
                {users.map((user) => (
                  <tr key={user.id} className="border-b border-gold/5 last:border-0 hover:bg-accent/5 transition-colors">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <div className="w-7 h-7 rounded-full bg-gold/15 flex items-center justify-center">
                          <span className="text-xs text-gold font-medium">
                            {user.username.charAt(0).toUpperCase()}
                          </span>
                        </div>
                        <span className="text-sm font-medium text-star">{user.username}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-sm text-star-dim">{user.email}</td>
                    <td className="px-4 py-3 text-center">
                      <select
                        value={user.role}
                        onChange={e => handleRoleChange(user.id, e.target.value)}
                        disabled={changingRole === user.id}
                        className="text-xs bg-accent/10 text-star border border-gold/15 rounded px-2 py-1 focus:outline-none focus:border-gold/40"
                      >
                        {ROLES.map(r => (
                          <option key={r} value={r}>{r}</option>
                        ))}
                      </select>
                    </td>
                    <td className="px-4 py-3 text-center">
                      <span className={cn(
                        'inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full',
                        user.status === 'approved' ? 'bg-accent-emerald/10 text-accent-emerald' :
                        user.status === 'rejected' ? 'bg-red-500/10 text-red-400' :
                        'bg-gold/10 text-gold',
                      )}>
                        <span className={cn(
                          'w-1.5 h-1.5 rounded-full',
                          user.status === 'approved' ? 'bg-accent-emerald' :
                          user.status === 'rejected' ? 'bg-red-400' :
                          'bg-gold animate-pulse',
                        )} />
                        {user.status === 'approved' ? '已批准' :
                         user.status === 'rejected' ? '已拒绝' : '待审核'}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-sm text-star-dim">
                      {new Date(user.created_at).toLocaleDateString('zh-CN')}
                    </td>
                    <td className="px-4 py-3 text-right">
                      {user.status === 'pending' ? (
                        <div className="flex gap-1 justify-end">
                          <Button
                            size="sm"
                            variant="outline"
                            className="text-accent-emerald border-accent-emerald/30 hover:bg-accent-emerald/10 text-xs h-7"
                            onClick={() => handleStatus(user.id, 'approved')}
                          >
                            <CheckCircle className="h-3.5 w-3.5 mr-1" />
                            批准
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            className="text-red-400 border-red-400/30 hover:bg-red-400/10 text-xs h-7"
                            onClick={() => handleStatus(user.id, 'rejected')}
                          >
                            <XCircle className="h-3.5 w-3.5 mr-1" />
                            拒绝
                          </Button>
                        </div>
                      ) : (
                        <span className="text-xs text-star-dim">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* 移动端卡片 */}
          <div className="md:hidden space-y-3 p-4">
            {users.map((user) => (
              <div key={user.id} className="glass-card p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className="w-7 h-7 rounded-full bg-gold/15 flex items-center justify-center">
                      <span className="text-xs text-gold font-medium">
                        {user.username.charAt(0).toUpperCase()}
                      </span>
                    </div>
                    <div>
                      <p className="text-sm font-medium text-star">{user.username}</p>
                      <p className="text-xs text-star-dim">{user.email}</p>
                    </div>
                  </div>
                  <span className={cn(
                    'text-xs px-2 py-0.5 rounded-full',
                    user.status === 'approved' ? 'bg-accent-emerald/10 text-accent-emerald' :
                    user.status === 'rejected' ? 'bg-red-500/10 text-red-400' :
                    'bg-gold/10 text-gold',
                  )}>
                    {user.status === 'approved' ? '已批准' :
                     user.status === 'rejected' ? '已拒绝' : '待审核'}
                  </span>
                </div>

                <div className="flex items-center gap-2">
                  <span className="text-xs text-star-dim">角色:</span>
                  <select
                    value={user.role}
                    onChange={e => handleRoleChange(user.id, e.target.value)}
                    disabled={changingRole === user.id}
                    className="text-xs bg-accent/10 text-star border border-gold/15 rounded px-2 py-0.5"
                  >
                    {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                  </select>
                  <span className="text-xs text-star-dim ml-auto">
                    {new Date(user.created_at).toLocaleDateString('zh-CN')}
                  </span>
                </div>

                {user.status === 'pending' && (
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      className="flex-1 text-xs h-7 bg-accent-emerald/20 text-accent-emerald hover:bg-accent-emerald/30"
                      onClick={() => handleStatus(user.id, 'approved')}
                    >
                      <CheckCircle className="h-3.5 w-3.5 mr-1" />
                      批准
                    </Button>
                    <Button
                      size="sm"
                      className="flex-1 text-xs h-7 bg-red-500/10 text-red-400 hover:bg-red-500/20"
                      onClick={() => handleStatus(user.id, 'rejected')}
                    >
                      <XCircle className="h-3.5 w-3.5 mr-1" />
                      拒绝
                    </Button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

/** 简易 class 合并 */
function cn(...classes: (string | boolean | undefined | null)[]): string {
  return classes.filter(Boolean).join(' ')
}
