/**
 * Agent 管理页面 — 卡片网格展示，支持 CRUD + 测试
 * 管理 AI Agent 的配置、系统提示词和关联模型
 */

import { useEffect, useState } from 'react'
import {
  Plus, Edit, Trash2, Play, Loader2, Bot, Cpu,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import { agentsApi, modelsConfigApi, type AgentOut, type ModelConfigOut } from '@/lib/api'

export function AdminAgentPage() {
  const [agents, setAgents] = useState<AgentOut[]>([])
  const [models, setModels] = useState<ModelConfigOut[]>([])
  const [loading, setLoading] = useState(true)

  // 对话框状态
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingAgent, setEditingAgent] = useState<AgentOut | null>(null)
  const [form, setForm] = useState({
    name: '',
    description: '',
    system_prompt: '',
    model_config_id: 0,
    status: 'active',
  })

  useEffect(() => {
    loadData()
  }, [])

  const loadData = async () => {
    setLoading(true)
    try {
      const [agentList, modelList] = await Promise.all([
        agentsApi.list(),
        modelsConfigApi.list(),
      ])
      setAgents(agentList)
      setModels(modelList)
    } catch {
      // 静默处理
    } finally {
      setLoading(false)
    }
  }

  /** 打开新建对话框 */
  const openCreate = () => {
    setEditingAgent(null)
    setForm({ name: '', description: '', system_prompt: '', model_config_id: models[0]?.id ?? 0, status: 'active' })
    setDialogOpen(true)
  }

  /** 打开编辑对话框 */
  const openEdit = (agent: AgentOut) => {
    setEditingAgent(agent)
    setForm({
      name: agent.name,
      description: agent.description ?? '',
      system_prompt: agent.system_prompt ?? '',
      model_config_id: agent.model_config_id ?? 0,
      status: agent.status,
    })
    setDialogOpen(true)
  }

  /** 提交表单（新建或编辑） */
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      if (editingAgent) {
        const updated = await agentsApi.update(editingAgent.id, form)
        setAgents(prev => prev.map(a => (a.id === updated.id ? updated : a)))
      } else {
        const created = await agentsApi.create(form)
        setAgents(prev => [...prev, created])
      }
      setDialogOpen(false)
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '操作失败')
    }
  }

  /** 删除 Agent */
  const handleDelete = async (id: number) => {
    if (!confirm('确定删除此 Agent？')) return
    try {
      await agentsApi.delete(id)
      setAgents(prev => prev.filter(a => a.id !== id))
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '删除失败')
    }
  }

  /** 测试 Agent */
  const handleTest = async (id: number) => {
    try {
      const result = await agentsApi.test(id)
      alert(result.message)
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : '测试失败')
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
          <h1 className="text-2xl font-serif text-gold">Agent 管理</h1>
          <p className="text-star-dim text-sm mt-1">管理 AI 写作助手的配置与行为</p>
        </div>
        <Button onClick={openCreate} className="btn-gold text-white gap-1">
          <Plus className="h-4 w-4" />
          新建 Agent
        </Button>
      </div>

      {/* Agent 卡片网格 */}
      {agents.length === 0 ? (
        <div className="glass-card p-12 text-center">
          <Bot className="h-12 w-12 text-star-dim mx-auto mb-3" />
          <p className="text-star-dim">暂无 Agent 配置</p>
          <p className="text-star-dim/50 text-sm mt-1">点击「新建 Agent」创建第一个 AI 写作助手</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {agents.map((agent) => (
            <div key={agent.id} className="glass-card p-5 flex flex-col hover:border-gold/30 transition-colors">
              {/* 头部 */}
              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-2">
                  <div className={cn(
                    'w-8 h-8 rounded-lg flex items-center justify-center',
                    agent.status === 'active' ? 'bg-accent-emerald/15 text-accent-emerald' : 'bg-star-dim/10 text-star-dim',
                  )}>
                    <Bot className="h-4 w-4" />
                  </div>
                  <div>
                    <h3 className="font-medium text-star">{agent.name}</h3>
                    <span className={cn(
                      'text-xs',
                      agent.status === 'active' ? 'text-accent-emerald' : 'text-star-dim',
                    )}>
                      {agent.status === 'active' ? '● 运行中' : '○ 已停用'}
                    </span>
                  </div>
                </div>
              </div>

              {/* 描述 */}
              {agent.description && (
                <p className="text-star-dim text-sm mb-3 line-clamp-2">{agent.description}</p>
              )}

              {/* 关联模型 */}
              <div className="flex items-center gap-2 text-xs text-star-dim mb-4">
                <Cpu className="h-3 w-3" />
                {models.find(m => m.id === agent.model_config_id)?.model_id ?? '未关联模型'}
              </div>

              {/* 操作按钮 */}
              <div className="flex gap-2 mt-auto pt-3 border-t border-gold/10">
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-star-dim hover:text-accent-cyan"
                  onClick={() => handleTest(agent.id)}
                >
                  <Play className="h-3.5 w-3.5 mr-1" />
                  测试
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-star-dim hover:text-gold"
                  onClick={() => openEdit(agent)}
                >
                  <Edit className="h-3.5 w-3.5 mr-1" />
                  编辑
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-star-dim hover:text-red-400 ml-auto"
                  onClick={() => handleDelete(agent.id)}
                >
                  <Trash2 className="h-3.5 w-3.5 mr-1" />
                  删除
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 新建/编辑对话框 */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="glass-card text-star max-w-lg">
          <DialogHeader>
            <DialogTitle className="text-gold font-serif">
              {editingAgent ? '编辑 Agent' : '新建 Agent'}
            </DialogTitle>
          </DialogHeader>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="agent-name">名称</Label>
              <Input
                id="agent-name"
                value={form.name}
                onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                placeholder="Agent 名称"
                required
                className="glass-card border-gold/20 focus:border-gold/50 text-star"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="agent-desc">描述</Label>
              <Input
                id="agent-desc"
                value={form.description}
                onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                placeholder="简短描述 Agent 的用途"
                className="glass-card border-gold/20 focus:border-gold/50 text-star"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="agent-prompt">系统提示词</Label>
              <Textarea
                id="agent-prompt"
                value={form.system_prompt}
                onChange={e => setForm(f => ({ ...f, system_prompt: e.target.value }))}
                placeholder="定义 Agent 的行为和写作风格..."
                rows={4}
                className="glass-card border-gold/20 focus:border-gold/50 text-star"
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="agent-model">关联模型</Label>
                <select
                  id="agent-model"
                  value={form.model_config_id}
                  onChange={e => setForm(f => ({ ...f, model_config_id: Number(e.target.value) }))}
                  className="w-full rounded-md border border-gold/20 bg-primary/80 text-star px-3 py-2 text-sm focus:outline-none focus:border-gold/50"
                >
                  <option value={0}>未选择</option>
                  {models.map(m => (
                    <option key={m.id} value={m.id}>
                      {m.provider_name} / {m.model_id}
                    </option>
                  ))}
                </select>
              </div>

              <div className="space-y-2">
                <Label htmlFor="agent-status">状态</Label>
                <select
                  id="agent-status"
                  value={form.status}
                  onChange={e => setForm(f => ({ ...f, status: e.target.value }))}
                  className="w-full rounded-md border border-gold/20 bg-primary/80 text-star px-3 py-2 text-sm focus:outline-none focus:border-gold/50"
                >
                  <option value="active">运行中</option>
                  <option value="inactive">已停用</option>
                </select>
              </div>
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
                {editingAgent ? '保存' : '创建'}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}

/** clsx-like conditional class helper (local, avoid extra import) */
function cn(...classes: (string | boolean | undefined | null)[]): string {
  return classes.filter(Boolean).join(' ')
}
