import { useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { AppLayout } from '@/components/layout/AppLayout'
import { LoginPage } from '@/pages/LoginPage'
import { RegisterPage } from '@/pages/RegisterPage'
import { NovelListPage } from '@/pages/NovelListPage'
import { EditorPage } from '@/pages/EditorPage'
import { SettingsPage } from '@/pages/SettingsPage'
import { KnowledgePage } from '@/pages/KnowledgePage'
import { AdminDashboard } from '@/pages/AdminDashboard'
import { AdminAgentPage } from '@/pages/AdminAgentPage'
import { AdminModelPage } from '@/pages/AdminModelPage'
import { AdminDatabasePage } from '@/pages/AdminDatabasePage'
import { AdminBackupPage } from '@/pages/AdminBackupPage'
import { AdminUserPage } from '@/pages/AdminUserPage'
import { useAuthStore } from '@/store/auth'

/** 需要登录的页面包裹 */
function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuthStore()

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen">
        <Loader2 className="h-8 w-8 animate-spin text-star-dim" />
      </div>
    )
  }

  if (!user) return <Navigate to="/login" replace />
  return <>{children}</>
}

/** 仅管理员可访问 */
function AdminRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuthStore()

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen">
        <Loader2 className="h-8 w-8 animate-spin text-star-dim" />
      </div>
    )
  }

  if (!user) return <Navigate to="/login" replace />
  if (user.role !== 'admin') return <Navigate to="/" replace />
  return <>{children}</>
}

export default function App() {
  const { restoreSession, user, loading } = useAuthStore()
  const [initialized, setInitialized] = useState(false)

  useEffect(() => {
    restoreSession().finally(() => setInitialized(true))
  }, [restoreSession])

  if (!initialized) {
    return (
      <div className="flex items-center justify-center h-screen bg-bg">
        <Loader2 className="h-8 w-8 animate-spin text-star-dim" />
      </div>
    )
  }

  return (
    <BrowserRouter>
      <Routes>
        {/* 公开路由 */}
        <Route path="/login" element={user ? <Navigate to="/" replace /> : <LoginPage />} />
        <Route path="/register" element={user ? <Navigate to="/" replace /> : <RegisterPage />} />

        {/* 需要登录的路由 */}
        <Route path="/" element={<ProtectedRoute><AppLayout><NovelListPage /></AppLayout></ProtectedRoute>} />
        <Route path="/novel/:novelId/editor" element={<ProtectedRoute><AppLayout><EditorPage /></AppLayout></ProtectedRoute>} />
        <Route path="/novel/:novelId/settings" element={<ProtectedRoute><AppLayout><SettingsPage /></AppLayout></ProtectedRoute>} />
        <Route path="/knowledge" element={<ProtectedRoute><AppLayout><KnowledgePage /></AppLayout></ProtectedRoute>} />
        <Route path="/settings-profile" element={<ProtectedRoute><AppLayout><SettingsPage /></AppLayout></ProtectedRoute>} />

        {/* 管理控制台 */}
        <Route path="/admin" element={<AdminRoute><AppLayout><AdminDashboard /></AppLayout></AdminRoute>} />
        <Route path="/admin/agents" element={<AdminRoute><AppLayout><AdminAgentPage /></AppLayout></AdminRoute>} />
        <Route path="/admin/models" element={<AdminRoute><AppLayout><AdminModelPage /></AppLayout></AdminRoute>} />
        <Route path="/admin/database" element={<AdminRoute><AppLayout><AdminDatabasePage /></AppLayout></AdminRoute>} />
        <Route path="/admin/users" element={<AdminRoute><AppLayout><AdminUserPage /></AppLayout></AdminRoute>} />
        <Route path="/admin/backup" element={<AdminRoute><AppLayout><AdminBackupPage /></AppLayout></AdminRoute>} />

        {/* 404 */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
