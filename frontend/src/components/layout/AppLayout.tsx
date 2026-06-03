import { Link, useNavigate, useLocation } from 'react-router-dom'
import {
  BookOpen, Settings, Shield, LogOut, Menu, X, PenLine,
  Brain, Database, Cpu, Users, LayoutDashboard, ChevronDown, ChevronRight,
} from 'lucide-react'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { useAuthStore } from '@/store/auth'
import { cn } from '@/lib/utils'

interface AppLayoutProps {
  children: React.ReactNode
}

/** 管理控制台子菜单项 */
const adminSubItems = [
  { to: '/admin', label: '仪表盘', icon: LayoutDashboard },
  { to: '/admin/agents', label: 'Agent 管理', icon: Cpu },
  { to: '/admin/models', label: '模型配置', icon: Brain },
  { to: '/admin/database', label: '数据库', icon: Database },
  { to: '/admin/users', label: '用户管理', icon: Users },
]

export function AppLayout({ children }: AppLayoutProps) {
  const { user, logout } = useAuthStore()
  const navigate = useNavigate()
  const location = useLocation()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [adminExpanded, setAdminExpanded] = useState(
    adminSubItems.some(item => location.pathname === item.to)
  )

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  /** 判断当前是否在管理员子页面 */
  const isAdminArea = location.pathname.startsWith('/admin')
  /** 判断精确匹配 */
  const isAdminRoot = location.pathname === '/admin'

  const navItems = [
    { to: '/', label: '项目', icon: BookOpen },
    { to: '/knowledge', label: '知识库', icon: Brain },
    { to: '/settings-profile', label: '设置', icon: Settings },
  ]

  return (
    <div className="flex h-screen overflow-hidden">
      {/* 移动端遮罩 */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* 侧边栏 — 深蓝半透明 + 金色高亮 */}
      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-50 w-64 flex flex-col transition-transform lg:relative lg:translate-x-0',
          'glass-card border-0 border-r border-gold/15 rounded-none',
          sidebarOpen ? 'translate-x-0' : '-translate-x-full',
        )}
      >
        {/* Logo */}
        <div className="flex items-center gap-2 px-6 py-5 border-b border-gold/15">
          <PenLine className="h-6 w-6 text-gold" />
          <span className="text-lg font-bold beidou-logo">✦ 北斗</span>
          <button
            className="ml-auto lg:hidden text-star-dim hover:text-star"
            onClick={() => setSidebarOpen(false)}
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* 导航 */}
        <nav className="flex-1 px-3 py-4 space-y-1 custom-scrollbar overflow-y-auto">
          {navItems.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              onClick={() => setSidebarOpen(false)}
              className={cn(
                'flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors',
                location.pathname === item.to
                  ? 'bg-gold/15 text-gold border border-gold/20'
                  : 'text-star-dim hover:bg-accent/10 hover:text-star',
              )}
            >
              <item.icon className="h-5 w-5" />
              {item.label}
            </Link>
          ))}

          {/* 管理控制台 — 仅管理员可见 */}
          {user?.role === 'admin' && (
            <div className="pt-2">
              <button
                onClick={() => setAdminExpanded(!adminExpanded)}
                className={cn(
                  'flex items-center gap-3 px-3 py-2 rounded-md text-sm w-full transition-colors',
                  isAdminArea
                    ? 'bg-gold/10 text-gold border border-gold/15'
                    : 'text-star-dim hover:bg-accent/10 hover:text-star',
                )}
              >
                <Shield className="h-5 w-5" />
                <span className="flex-1 text-left">管理控制台</span>
                {adminExpanded ? (
                  <ChevronDown className="h-4 w-4" />
                ) : (
                  <ChevronRight className="h-4 w-4" />
                )}
              </button>

              {adminExpanded && (
                <div className="ml-4 mt-1 space-y-0.5 border-l border-gold/10 pl-3">
                  {adminSubItems.map((sub) => (
                    <Link
                      key={sub.to}
                      to={sub.to}
                      onClick={() => setSidebarOpen(false)}
                      className={cn(
                        'flex items-center gap-2 px-3 py-1.5 rounded-md text-xs transition-colors',
                        location.pathname === sub.to
                          ? 'bg-gold/15 text-gold'
                          : 'text-star-dim hover:text-star hover:bg-accent/5',
                      )}
                    >
                      <sub.icon className="h-3.5 w-3.5" />
                      {sub.label}
                    </Link>
                  ))}
                </div>
              )}
            </div>
          )}
        </nav>

        {/* 用户信息 & 退出 */}
        {user && (
          <div className="border-t border-gold/10 p-4 space-y-2">
            <div className="text-sm">
              <p className="font-medium text-star truncate">{user.username}</p>
              <p className="text-star-dim text-xs capitalize">{user.role}</p>
            </div>
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-start text-star-dim hover:text-star hover:bg-accent/10"
              onClick={handleLogout}
            >
              <LogOut className="h-4 w-4 mr-2" />
              退出登录
            </Button>
          </div>
        )}
      </aside>

      {/* 主内容区 */}
      <main className="flex-1 flex flex-col overflow-hidden">
        {/* 顶栏 */}
        <header className="flex items-center justify-between h-14 px-4 glass-card border-0 border-b border-gold/15 rounded-none shrink-0">
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden text-star-dim hover:text-star"
            onClick={() => setSidebarOpen(true)}
          >
            <Menu className="h-5 w-5" />
          </Button>
          <div className="flex-1" />
          {user && (
            <span className="text-sm text-star-dim">{user.username}</span>
          )}
        </header>

        {/* 页面内容 */}
        <div className="flex-1 overflow-auto">{children}</div>
      </main>
    </div>
  )
}
