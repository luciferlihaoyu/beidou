/**
 * 模型配置页面 — 管理 AI 模型提供商及配置
 * 支持增删改查、设为默认、测试连通性
 */

import { useEffect, useState } from 'react'
import {
  Plus, Trash2, Play, Loader2, Star, Brain, CheckCircle, XCircle,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import { modelsConfigApi, type ModelConfigOut } from '@/lib/api'

export function AdminModelPage() {
  const [models, setModels] = useState<ModelConfigOut[]>([])
  const [loading, setLoading] = useState(true)

  // 对话框
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState({
    provider_name: '',
    api_base: '',
    api_key: '',
    model_id: '',
    is_default: false,
  })
  const [testing, setTesting] = useState<number | null>(null)

  useEffect(() => {
    loadModels()
  }, [])

  const loadModels = async () => {
    setLoading(true)
    try {
      const list = await modelsConfigApi.list()
      setModels(list)
    } catch {
      // 静默处理
    } finally {
      setLoading(false)
    }
  }

  /** 打开新建 */
  const openCreate = () => {
    setEditingId(null)
    setForm({ provider_name: '', api_base: '', api_key: '', model_id: '', is_default: false })
    setDialogOpen(true)
  }

  /** 打开编辑 */
  const openEdit = (m: ModelConfigOut) => {
    setEditingId(m.id)
    setForm({
      provider_name: m.provider_name,
      api_base: m.api_base ?? '',
      api_key: m.api_key ?? '',
      model_id: m.model_id,
      is_default: m.is_default,
    })
    setDialogOpen(true)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      if (editingId) {
        const updated = await modelsConfigApi.update(editingId, form)
        setModels(prev => prev.map(m => (m.id === updated.id ? updated : m)))
      } else {
        const created = await modelsConfigApi.create(form)
        setModels(prev => [...prev, created])
      }
      setDialogOpen(false)
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '操作失败')
    }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('确定删除此模型配置？')) return
    try {
      await modelsConfigApi.delete(id)
      setModels(prev => prev.filter(m => m.id !== id))
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '删除失败')
    }
  }

  const handleTest = async (id: number) => {
    setTesting(id)
    try {
      const result = await modelsConfigApi.test(id)
      alert(result.success ? '✅ 连接成功' : `❌ ${result.message}`)
    } catch (e: unknown) {
      alert(`❌ ${e instanceof Error ? e.message : '测试失败'}`)
    } finally {
      setTesting(null)
    }
  }

  const handleSetDefault = async (id: number) => {
    try {
      const updated = await modelsConfigApi.setDefault(id)
      setModels(prev => prev.map(m => ({
        ...m,
        is_default: m.id === id,
      })))
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '设置默认失败')
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
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-serif text-gold">模型配置</h1>
          <p className="text-star-dim text-sm mt-1">管理 AI 模型提供商及 API 配置</p>
        </div>
        <Button onClick={openCreate} className="btn-gold text-white gap-1">
          <Plus className="h-4 w-4" />
          添加模型
        </Button>
      </div>

      {/* 模型表格 */}
      {models.length === 0 ? (
        <div className="glass-card p-12 text-center">
          <Brain className="h-12 w-12 text-star-dim mx-auto mb-3" />
          <p className="text-star-dim">暂无模型配置</p>
          <p className="text-star-dim/50 text-sm mt-1">点击「添加模型」配置第一个 AI 模型</p>
        </div>
      ) : (
        <div className="glass-card overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-gold/10">
                <th className="text-left px-4 py-3 text-xs font-medium text-star-dim">提供商</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-star-dim">模型 ID</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-star-dim hidden md:table-cell">API Base</th>
                <th className="text-center px-4 py-3 text-xs font-medium text-star-dim">默认</th>
                <th className="text-center px-4 py-3 text-xs font-medium text-star-dim hidden sm:table-cell">状态</th>
                <th className="text-right px-4 py-3 text-xs font-medium text-star-dim">操作</th>
              </tr>
            </thead>
            <tbody>
              {models.map((model) => (
                <tr key={model.id} className="border-b border-gold/5 last:border-0 hover:bg-accent/5 transition-colors">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <Brain className="h-4 w-4 text-accent-purple" />
                      <span className="text-sm font-medium text-star">{model.provider_name}</span>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <code className="text-xs bg-accent/10 text-accent-cyan px-1.5 py-0.5 rounded">
                      {model.model_id}
                    </code>
                  </td>
                  <td className="px-4 py-3 text-xs text-star-dim hidden md:table-cell max-w-[200px] truncate">
                    {model.api_base || '—'}
                  </td>
                  <td className="px-4 py-3 text-center">
                    {model.is_default ? (
                      <Star className="h-4 w-4 text-gold mx-auto fill-gold" />
                    ) : (
                      <button
                        onClick={() => handleSetDefault(model.id)}
                        className="text-star-dim hover:text-gold transition-colors"
                        title="设为默认"
                      >
                        <Star className="h-4 w-4 mx-auto" />
                      </button>
                    )}
                  </td>
                  <td className="px-4 py-3 text-center hidden sm:table-cell">
                    <span className="inline-flex items-center gap-1 text-xs text-accent-emerald">
                      <CheckCircle className="h-3 w-3" />
                      已配置
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex gap-1 justify-end">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-star-dim hover:text-accent-cyan h-8"
                        onClick={() => handleTest(model.id)}
                        disabled={testing === model.id}
                      >
                        {testing === model.id ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Play className="h-3.5 w-3.5" />
                        )}
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-star-dim hover:text-gold h-8"
                        onClick={() => openEdit(model)}
                      >
                        编辑
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-star-dim hover:text-red-400 h-8"
                        onClick={() => handleDelete(model.id)}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 新建/编辑对话框 */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="glass-card text-star max-w-lg">
          <DialogHeader>
            <DialogTitle className="text-gold font-serif">
              {editingId ? '编辑模型' : '添加模型'}
            </DialogTitle>
          </DialogHeader>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="model-provider">提供商</Label>
                <Input
                  id="model-provider"
                  value={form.provider_name}
                  onChange={e => setForm(f => ({ ...f, provider_name: e.target.value }))}
                  placeholder="如 openai"
                  required
                  className="glass-card border-gold/20 focus:border-gold/50 text-star"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="model-id">模型 ID</Label>
                <Input
                  id="model-id"
                  value={form.model_id}
                  onChange={e => setForm(f => ({ ...f, model_id: e.target.value }))}
                  placeholder="如 gpt-4o"
                  required
                  className="glass-card border-gold/20 focus:border-gold/50 text-star"
                />
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="model-base">API Base URL</Label>
              <Input
                id="model-base"
                value={form.api_base}
                onChange={e => setForm(f => ({ ...f, api_base: e.target.value }))}
                placeholder="https://api.openai.com/v1"
                className="glass-card border-gold/20 focus:border-gold/50 text-star"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="model-key">API Key</Label>
              <Input
                id="model-key"
                type="password"
                value={form.api_key}
                onChange={e => setForm(f => ({ ...f, api_key: e.target.value }))}
                placeholder="sk-..."
                className="glass-card border-gold/20 focus:border-gold/50 text-star"
              />
            </div>

            <div className="flex justify-end gap-3 pt-2">
              <Button
                type="button"
                variant="ghost"
                className="text-star-dim"
                onClick={() => setDialogOpen(false)}
              >
                取消
              </Button>
              <Button type="submit" className="btn-gold text-white">
                {editingId ? '保存' : '添加'}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
