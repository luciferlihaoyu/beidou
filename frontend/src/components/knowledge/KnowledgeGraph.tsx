/**
 * 知识图谱脑图组件 — Canvas 自力导向图
 * 不依赖外部图库，使用 requestAnimationFrame + 力导向算法
 *
 * 特性：
 * - 力导向布局（排斥力 + 吸引力 + 阻尼）
 * - 拖拽节点
 * - 滚轮缩放
 * - 点击选中
 * - 按类型/关键词搜索筛选
 * - 不同类型不同颜色
 */

import { useEffect, useRef, useState, useCallback } from 'react'
import { Search } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { knowledgeApi, type GraphData, type KnowledgeEntryOut } from '@/lib/api'

/** 节点颜色映射 */
const NODE_COLORS: Record<string, string> = {
  character: '#d4a574',
  worldview: '#3b82f6',
  plot: '#10b981',
  foreshadow: '#a855f7',
  custom: '#e8e8e8',
}

/** 节点颜色（发光版本） */
const NODE_GLOW: Record<string, string> = {
  character: 'rgba(212, 165, 116, 0.4)',
  worldview: 'rgba(59, 130, 246, 0.4)',
  plot: 'rgba(16, 185, 129, 0.4)',
  foreshadow: 'rgba(168, 85, 247, 0.4)',
  custom: 'rgba(232, 232, 232, 0.2)',
}

/** 类型中文名 */
const TYPE_LABELS: Record<string, string> = {
  character: '角色',
  worldview: '世界观',
  plot: '剧情',
  foreshadow: '伏笔',
  custom: '自定义',
}

interface GraphNode {
  id: string
  label: string
  type: string
  val: number
  x: number
  y: number
  vx: number
  vy: number
  // 固定（拖拽中）
  fx: number | null
  fy: number | null
}

interface GraphEdge {
  source: string
  target: string
  label: string
}

interface Props {
  baseId: number
  /** 已知条目列表（用于从图中映射到 entry 对象） */
  entries: KnowledgeEntryOut[]
  /** 点击节点时回调 */
  onEntryClick: (entry: KnowledgeEntryOut | null) => void
  /** 条目变更后回调 */
  onEntriesChange: () => void
}

export function KnowledgeGraph({ baseId, entries, onEntryClick, onEntriesChange }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  // 图数据
  const nodesRef = useRef<GraphNode[]>([])
  const edgesRef = useRef<GraphEdge[]>([])
  const [loading, setLoading] = useState(true)

  // 交互状态
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [searchText, setSearchText] = useState('')
  const [filterType, setFilterType] = useState<string>('')

  // 视口状态
  const offsetRef = useRef({ x: 0, y: 0 })
  const scaleRef = useRef(1)
  const draggingNodeRef = useRef<string | null>(null)
  const panningRef = useRef(false)
  const lastMouseRef = useRef({ x: 0, y: 0 })
  const [scale, setScale] = useState(1)

  // 加载图数据
  useEffect(() => {
    loadGraph()
  }, [baseId])

  const loadGraph = async () => {
    setLoading(true)
    try {
      const data = await knowledgeApi.getGraph(baseId)
      if (data?.nodes) {
        const centerX = 400
        const centerY = 300
        const gNodes: GraphNode[] = data.nodes.map((n, i) => ({
          id: n.id,
          label: n.label,
          type: n.type || 'custom',
          val: n.val || 5,
          x: centerX + (Math.random() - 0.5) * 200,
          y: centerY + (Math.random() - 0.5) * 200,
          vx: 0,
          vy: 0,
          fx: null,
          fy: null,
        }))
        nodesRef.current = gNodes
        edgesRef.current = data.edges || []
      } else {
        nodesRef.current = []
        edgesRef.current = []
      }
    } catch {
      nodesRef.current = []
      edgesRef.current = []
    } finally {
      setLoading(false)
    }
  }

  // ── 力导向模拟 ────────────────────────────
  useEffect(() => {
    if (!canvasRef.current) return

    const canvas = canvasRef.current
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let animId = 0
    const REPULSION = 800
    const ATTRACTION = 0.005
    const DAMPING = 0.85

    const simulate = () => {
      const nodes = nodesRef.current
      const edges = edgesRef.current

      // 力导向步进
      for (const node of nodes) {
        if (node.fx !== null) {
          node.x = node.fx
          node.y = node.fy!
          node.vx = 0
          node.vy = 0
          continue
        }

        // 排斥力（节点之间）
        for (const other of nodes) {
          if (node === other) continue
          let dx = node.x - other.x
          let dy = node.y - other.y
          const dist = Math.sqrt(dx * dx + dy * dy) || 1
          const force = REPULSION / (dist * dist)
          node.vx += (dx / dist) * force * 0.02
          node.vy += (dy / dist) * force * 0.02
        }

        // 中心引力
        const cx = canvas.width / 2
        const cy = canvas.height / 2
        node.vx += (cx - node.x) * ATTRACTION
        node.vy += (cy - node.y) * ATTRACTION
      }

      // 边吸引力
      for (const edge of edges) {
        const src = nodes.find(n => n.id === edge.source)
        const tgt = nodes.find(n => n.id === edge.target)
        if (!src || !tgt) continue
        const dx = tgt.x - src.x
        const dy = tgt.y - src.y
        const dist = Math.sqrt(dx * dx + dy * dy) || 1
        const force = (dist - 120) * 0.005
        src.vx += (dx / dist) * force
        src.vy += (dy / dist) * force
        tgt.vx -= (dx / dist) * force
        tgt.vy -= (dy / dist) * force
      }

      // 应用速度 + 阻尼
      for (const node of nodes) {
        node.vx *= DAMPING
        node.vy *= DAMPING
        node.x += node.vx
        node.y += node.vy
        // 边界
        node.x = Math.max(20, Math.min(canvas.width - 20, node.x))
        node.y = Math.max(20, Math.min(canvas.height - 20, node.y))
      }

      // 绘制
      draw(ctx, canvas)
      animId = requestAnimationFrame(simulate)
    }

    animId = requestAnimationFrame(simulate)

    return () => cancelAnimationFrame(animId)
  }, [selectedNodeId])

  // ── 绘制函数 ──────────────────────────────
  const draw = (ctx: CanvasRenderingContext2D, canvas: HTMLCanvasElement) => {
    const scale = scaleRef.current
    const offset = offsetRef.current
    const nodes = nodesRef.current
    const edges = edgesRef.current

    ctx.clearRect(0, 0, canvas.width, canvas.height)

    // 筛选
    const visibleNodeIds = new Set(
      nodes
        .filter(n => {
          if (filterType && n.type !== filterType) return false
          if (searchText && !n.label.toLowerCase().includes(searchText.toLowerCase())) return false
          return true
        })
        .map(n => n.id),
    )

    ctx.save()
    ctx.translate(offset.x, offset.y)
    ctx.scale(scale, scale)

    // 绘制边
    for (const edge of edges) {
      if (!visibleNodeIds.has(edge.source) || !visibleNodeIds.has(edge.target)) continue
      const src = nodes.find(n => n.id === edge.source)
      const tgt = nodes.find(n => n.id === edge.target)
      if (!src || !tgt) continue

      ctx.beginPath()
      ctx.moveTo(src.x, src.y)
      ctx.lineTo(tgt.x, tgt.y)
      ctx.strokeStyle = 'rgba(212, 165, 116, 0.2)'
      ctx.lineWidth = 1.5
      ctx.stroke()

      // 关系标签
      if (edge.label && scale > 0.6) {
        const mx = (src.x + tgt.x) / 2
        const my = (src.y + tgt.y) / 2
        ctx.font = `${10 / scale}px sans-serif`
        ctx.fillStyle = 'rgba(160, 160, 160, 0.7)'
        ctx.textAlign = 'center'
        ctx.fillText(edge.label, mx, my - 4)
      }
    }

    // 绘制节点
    for (const node of nodes) {
      if (!visibleNodeIds.has(node.id)) continue
      const color = NODE_COLORS[node.type] || NODE_COLORS.custom
      const glow = NODE_GLOW[node.type] || NODE_GLOW.custom
      const radius = Math.max(12, Math.min(30, node.val * 4))

      // 发光效果
      ctx.beginPath()
      ctx.arc(node.x, node.y, radius + 6, 0, Math.PI * 2)
      ctx.fillStyle = glow
      ctx.fill()

      // 圆形节点
      ctx.beginPath()
      ctx.arc(node.x, node.y, radius, 0, Math.PI * 2)
      ctx.fillStyle = selectedNodeId === node.id ? color : `${color}cc`
      ctx.fill()

      // 选中高亮边框
      if (selectedNodeId === node.id) {
        ctx.beginPath()
        ctx.arc(node.x, node.y, radius + 3, 0, Math.PI * 2)
        ctx.strokeStyle = '#d4a574'
        ctx.lineWidth = 2
        ctx.stroke()
      }

      // 标签
      if (scale > 0.4) {
        ctx.font = `${Math.max(9, 11 / scale)}px sans-serif`
        ctx.fillStyle = scale > 0.7 ? '#e8e8e8' : '#c0c0c0'
        ctx.textAlign = 'center'
        ctx.fillText(node.label, node.x, node.y + radius + 14)
      }
    }

    ctx.restore()
  }

  // ── 鼠标交互 ──────────────────────────────
  const getCanvasPos = (e: React.MouseEvent): { x: number; y: number } => {
    const canvas = canvasRef.current!
    const rect = canvas.getBoundingClientRect()
    return {
      x: e.clientX - rect.left,
      y: e.clientY - rect.top,
    }
  }

  /** 从屏幕坐标反算图坐标 */
  const screenToWorld = (sx: number, sy: number) => {
    const offset = offsetRef.current
    const scale = scaleRef.current
    return {
      x: (sx - offset.x) / scale,
      y: (sy - offset.y) / scale,
    }
  }

  const handleMouseDown = (e: React.MouseEvent) => {
    const pos = getCanvasPos(e)
    const world = screenToWorld(pos.x, pos.y)
    const nodes = nodesRef.current

    // 检查是否点中节点
    for (const node of nodes) {
      const radius = Math.max(12, Math.min(30, node.val * 4))
      const dx = world.x - node.x
      const dy = world.y - node.y
      if (Math.sqrt(dx * dx + dy * dy) < radius + 6) {
        draggingNodeRef.current = node.id
        node.fx = node.x
        node.fy = node.y
        setSelectedNodeId(node.id)
        const entry = entries.find(e => String(e.id) === node.id)
        if (entry) onEntryClick(entry)
        canvasRef.current!.style.cursor = 'grabbing'
        return
      }
    }

    // 否则开始平移
    draggingNodeRef.current = null
    panningRef.current = true
    lastMouseRef.current = { x: e.clientX, y: e.clientY }
    canvasRef.current!.style.cursor = 'grabbing'
  }

  const handleMouseMove = (e: React.MouseEvent) => {
    if (draggingNodeRef.current) {
      const pos = getCanvasPos(e)
      const world = screenToWorld(pos.x, pos.y)
      const node = nodesRef.current.find(n => n.id === draggingNodeRef.current)
      if (node) {
        node.fx = world.x
        node.fy = world.y
      }
    } else if (panningRef.current) {
      const dx = e.clientX - lastMouseRef.current.x
      const dy = e.clientY - lastMouseRef.current.y
      offsetRef.current.x += dx
      offsetRef.current.y += dy
      lastMouseRef.current = { x: e.clientX, y: e.clientY }
    }
  }

  const handleMouseUp = () => {
    if (draggingNodeRef.current) {
      const node = nodesRef.current.find(n => n.id === draggingNodeRef.current)
      if (node) {
        node.fx = null
        node.fy = null
      }
      draggingNodeRef.current = null
    }
    panningRef.current = false
    if (canvasRef.current) canvasRef.current.style.cursor = 'default'
  }

  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault()
    const delta = e.deltaY > 0 ? 0.9 : 1.1
    const newScale = Math.max(0.2, Math.min(3, scaleRef.current * delta))
    scaleRef.current = newScale
    setScale(newScale)
  }

  // ── Canvas resize ──────────────────────────
  useEffect(() => {
    const resize = () => {
      const canvas = canvasRef.current
      const container = containerRef.current
      if (!canvas || !container) return
      const rect = container.getBoundingClientRect()
      canvas.width = rect.width
      canvas.height = rect.height
    }
    resize()
    window.addEventListener('resize', resize)
    return () => window.removeEventListener('resize', resize)
  }, [])

  return (
    <div ref={containerRef} className="w-full h-full relative bg-bg">
      {/* 工具栏 */}
      <div className="absolute top-2 left-2 right-2 z-10 flex gap-2 items-center">
        {/* 搜索框 */}
        <div className="relative flex-1 max-w-[200px]">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-star-dim" />
          <Input
            value={searchText}
            onChange={e => setSearchText(e.target.value)}
            placeholder="搜索节点..."
            className="pl-7 h-8 text-xs glass-card border-gold/20 focus:border-gold/40 text-star"
          />
        </div>

        {/* 类型筛选 */}
        <select
          value={filterType}
          onChange={e => setFilterType(e.target.value)}
          className="h-8 text-xs bg-primary/80 text-star border border-gold/20 rounded-md px-2 focus:outline-none focus:border-gold/40"
        >
          <option value="">全部类型</option>
          {Object.entries(TYPE_LABELS).map(([key, label]) => (
            <option key={key} value={key}>{label}</option>
          ))}
        </select>
      </div>

      {/* 图例 */}
      <div className="absolute bottom-2 left-2 z-10 flex gap-3 text-xs text-star-dim">
        {Object.entries(TYPE_LABELS).map(([key, label]) => (
          <div key={key} className="flex items-center gap-1.5">
            <span
              className="w-2.5 h-2.5 rounded-full"
              style={{ backgroundColor: NODE_COLORS[key] || NODE_COLORS.custom }}
            />
            {label}
          </div>
        ))}
      </div>

      {/* Canvas */}
      <canvas
        ref={canvasRef}
        className="w-full h-full"
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        onWheel={handleWheel}
      />

      {/* 加载中 */}
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-bg/50">
          <div className="text-star-dim text-sm animate-pulse">加载图谱数据中...</div>
        </div>
      )}
    </div>
  )
}
