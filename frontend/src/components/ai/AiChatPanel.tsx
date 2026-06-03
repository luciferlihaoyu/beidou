import { useState, useRef, useEffect, useCallback } from 'react'
import { Send, Bot, Loader2, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

interface AgentOption {
  id: number
  name: string
}

interface Props {
  /** External content to display as an assistant message (from toolbar buttons). */
  externalContent?: string
  /** Called after external content has been consumed. */
  onExternalContentConsumed?: () => void
}

export function AiChatPanel({ externalContent, onExternalContentConsumed }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [agents, setAgents] = useState<AgentOption[]>([])
  const [selectedAgentId, setSelectedAgentId] = useState<number | null>(null)
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const streamingRef = useRef<string>('')

  // ── Load agents ──
  useEffect(() => {
    const token = localStorage.getItem('token')
    if (!token) return
    fetch('/api/agents', {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : []))
      .then((data: AgentOption[]) => setAgents(data))
      .catch(() => {})
  }, [])

  // ── Handle external content ──
  useEffect(() => {
    if (externalContent) {
      setMessages((prev) => [...prev, { role: 'assistant', content: externalContent }])
      onExternalContentConsumed?.()
    }
  }, [externalContent, onExternalContentConsumed])

  // ── WebSocket ──
  const connect = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${protocol}//${window.location.host}/api/ai/chat`
    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onopen = () => setConnected(true)
    ws.onclose = () => setConnected(false)
    ws.onerror = () => setConnected(false)

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.type === 'chat_response') {
          if (data.content) {
            streamingRef.current += data.content
            // Update last assistant message in place (streaming effect)
            setMessages((prev) => {
              const copy = [...prev]
              const last = copy[copy.length - 1]
              if (last && last.role === 'assistant' && !data.done) {
                copy[copy.length - 1] = { ...last, content: streamingRef.current }
              } else {
                copy.push({ role: 'assistant', content: streamingRef.current })
              }
              return copy
            })
          }
          if (data.done) {
            streamingRef.current = ''
          }
        } else if (data.type === 'pong') {
          // heartbeat
        } else if (data.type === 'error') {
          setMessages((prev) => [
            ...prev,
            { role: 'assistant', content: `⚠️ ${data.content}` },
          ])
          streamingRef.current = ''
        }
      } catch {
        // ignore parse errors
      }
    }

    // Heartbeat every 30s
    const heartbeat = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'ping' }))
      }
    }, 30000)

    return () => {
      clearInterval(heartbeat)
      ws.close()
    }
  }, [])

  useEffect(() => {
    const cleanup = connect()
    return () => cleanup?.()
  }, [connect])

  // Auto-scroll
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const sendMessage = () => {
    if (!input.trim() || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return

    const msg = input.trim()
    const historyMessages = messages.slice(-20).map((m) => ({
      role: m.role,
      content: m.content,
    }))

    setMessages((prev) => [...prev, { role: 'user', content: msg }])
    setInput('')
    streamingRef.current = ''

    wsRef.current.send(
      JSON.stringify({
        type: 'chat',
        agent_id: selectedAgentId,
        content: msg,
        messages: historyMessages,
      }),
    )
  }

  const clearMessages = () => {
    setMessages([])
    streamingRef.current = ''
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  const isStreaming = streamingRef.current !== ''

  return (
    <div className="flex flex-col h-full bg-gray-950 text-gray-100">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-white/10">
        <div className="flex items-center gap-2">
          <Bot className="h-4 w-4 text-blue-400" />
          <span className="text-sm font-medium">AI 助手</span>
          <span
            className={cn(
              'inline-block w-2 h-2 rounded-full',
              connected ? 'bg-green-400' : 'bg-red-400',
            )}
          />
        </div>
        <div className="flex items-center gap-2">
          {/* Agent selector */}
          <select
            value={selectedAgentId ?? ''}
            onChange={(e) =>
              setSelectedAgentId(e.target.value ? Number(e.target.value) : null)
            }
            className="text-xs bg-white/10 border border-white/20 rounded px-2 py-1 text-gray-200"
          >
            <option value="">默认助手</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
          {/* Clear button */}
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-gray-400 hover:text-gray-200"
            onClick={clearMessages}
            title="清空对话"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.length === 0 && (
          <p className="text-sm text-gray-500 text-center mt-8">
            AI 助手就绪，输入消息开始对话
          </p>
        )}
        {messages.map((msg, i) => {
          const isLastAssistant =
            msg.role === 'assistant' && i === messages.length - 1
          return (
            <div
              key={i}
              className={cn(
                'flex',
                msg.role === 'user' ? 'justify-end' : 'justify-start',
              )}
            >
              <div
                className={cn(
                  'max-w-[85%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap',
                  msg.role === 'user'
                    ? 'bg-blue-600 text-white rounded-br-md'
                    : 'backdrop-blur-md bg-white/10 text-gray-100 rounded-bl-md',
                )}
              >
                {msg.role === 'assistant' && isLastAssistant && isStreaming ? (
                  <>
                    {msg.content}
                    <span className="inline-block w-1.5 h-4 bg-blue-400 ml-0.5 align-text-bottom animate-pulse" />
                  </>
                ) : (
                  msg.content
                )}
              </div>
            </div>
          )
        })}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="p-3 border-t border-white/10">
        <div className="flex gap-2">
          <Input
            className="h-9 text-sm bg-white/10 border-white/20 text-gray-100 placeholder:text-gray-500"
            placeholder="输入消息… (Enter 发送)"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={!connected}
          />
          <Button
            size="icon"
            className="h-9 w-9 shrink-0 bg-blue-600 hover:bg-blue-500"
            onClick={sendMessage}
            disabled={!connected || !input.trim()}
          >
            <Send className="h-4 w-4" />
          </Button>
        </div>
        {!connected && (
          <p className="text-xs text-red-400 mt-1">连接已断开，正在重连…</p>
        )}
      </div>
    </div>
  )
}
