import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, Plus, Sparkles, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api, type Character, type CharacterRelation } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

/** 环形布局的画布尺寸（viewBox 坐标系，随容器缩放） */
const W = 720;
const H = 520;
const CX = W / 2;
const CY = H / 2;
const R = 190; // 圆环半径
const NODE_R = 26; // 节点圆半径

interface NodePos {
  id: number;
  name: string;
  role: string;
  x: number;
  y: number;
  center: boolean;
}

/**
 * 人物关系图（C 级）：纯 SVG 环形布局。
 * - 度数最高（或 role 含「主角」）的角色放中心，其余均匀分布在圆周上
 * - 边：manual 实线 / ai 虚线，中点显示关系标签，<title> 悬停显示说明
 * - 点击节点高亮与之相关的边，再次点击取消
 */
export default function RelationGraph({ novelId }: { novelId: number }) {
  const [characters, setCharacters] = useState<Character[]>([]);
  const [relations, setRelations] = useState<CharacterRelation[]>([]);
  const [extracting, setExtracting] = useState(false);
  const [selectedNode, setSelectedNode] = useState<number | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [form, setForm] = useState({ from: "", to: "", relation: "", description: "" });

  const load = useCallback(() => {
    api
      .get<Character[]>(`/api/novels/${novelId}/settings/characters`)
      .then(setCharacters)
      .catch((e) => toast.error(e.message));
    api
      .get<CharacterRelation[]>(`/api/novels/${novelId}/relations`)
      .then(setRelations)
      .catch((e) => toast.error(e.message));
  }, [novelId]);
  useEffect(load, [load]);

  /* ---------- 布局计算 ---------- */
  const nodes = useMemo<NodePos[]>(() => {
    if (characters.length === 0) return [];
    // 选中心节点：优先 role 含「主角」，否则度数（关系条数）最高
    const degree = new Map<number, number>();
    for (const r of relations) {
      degree.set(r.from_character_id, (degree.get(r.from_character_id) ?? 0) + 1);
      degree.set(r.to_character_id, (degree.get(r.to_character_id) ?? 0) + 1);
    }
    const protagonist = characters.find((c) => c.role.includes("主角"));
    const centerChar =
      protagonist ??
      [...characters].sort((a, b) => (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0))[0];
    const rest = characters.filter((c) => c.id !== centerChar.id);
    const pos: NodePos[] = [
      { id: centerChar.id, name: centerChar.name, role: centerChar.role, x: CX, y: CY, center: true },
    ];
    rest.forEach((c, i) => {
      // 从正上方开始顺时针均匀分布
      const angle = (2 * Math.PI * i) / rest.length - Math.PI / 2;
      pos.push({
        id: c.id,
        name: c.name,
        role: c.role,
        x: CX + R * Math.cos(angle),
        y: CY + R * Math.sin(angle),
        center: false,
      });
    });
    return pos;
  }, [characters, relations]);

  const nodeMap = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);

  /** 只画两端角色都还存在的边 */
  const edges = useMemo(
    () => relations.filter((r) => nodeMap.has(r.from_character_id) && nodeMap.has(r.to_character_id)),
    [relations, nodeMap]
  );

  /* ---------- 操作 ---------- */
  async function extract() {
    setExtracting(true);
    try {
      const res = await api.post<{ created: number; skipped: number }>(
        `/api/novels/${novelId}/relations/extract`,
        {}
      );
      toast.success(`AI 抽取完成：新增 ${res.created} 条，跳过 ${res.skipped} 条`);
      load();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setExtracting(false);
    }
  }

  async function addRelation() {
    if (!form.from || !form.to) return toast.error("请选择起点与终点角色");
    if (form.from === form.to) return toast.error("起点与终点不能是同一个角色");
    if (!form.relation.trim()) return toast.error("请填写关系名");
    try {
      await api.post(`/api/novels/${novelId}/relations`, {
        from_character_id: Number(form.from),
        to_character_id: Number(form.to),
        relation: form.relation.trim(),
        description: form.description.trim(),
      });
      setAddOpen(false);
      setForm({ from: "", to: "", relation: "", description: "" });
      load();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function removeRelation(rel: CharacterRelation) {
    if (!window.confirm(`删除关系「${rel.from_name} → ${rel.to_name}（${rel.relation}）」？`)) return;
    await api.delete(`/api/novels/${novelId}/relations/${rel.id}`);
    load();
  }

  /** 节点选中态下的边高亮：未选中节点时全部正常显示 */
  function edgeDimmed(r: CharacterRelation) {
    return selectedNode !== null && r.from_character_id !== selectedNode && r.to_character_id !== selectedNode;
  }

  /* ---------- 渲染 ---------- */
  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-muted-foreground tnum">{relations.length} 条关系</p>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" className="h-8" disabled={extracting} onClick={() => void extract()}>
            {extracting ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Sparkles className="mr-1 h-4 w-4" />}
            {extracting ? "抽取中…" : "AI 抽取关系"}
          </Button>
          <Button size="sm" variant="outline" className="h-8" onClick={() => setAddOpen(true)}>
            <Plus className="mr-1 h-4 w-4" />
            手动添加
          </Button>
        </div>
      </div>

      {characters.length < 2 ? (
        <div className="rounded-lg border border-dashed border-input py-16 text-center text-sm text-muted-foreground">
          先在「角色」页创建至少 2 个角色卡，再回来看关系图
        </div>
      ) : (
        <div className="rounded-lg border border-border bg-card">
          <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full select-none">
            {/* 边 */}
            {edges.map((r) => {
              const a = nodeMap.get(r.from_character_id)!;
              const b = nodeMap.get(r.to_character_id)!;
              const mx = (a.x + b.x) / 2;
              const my = (a.y + b.y) / 2;
              const dimmed = edgeDimmed(r);
              return (
                <g key={r.id} opacity={dimmed ? 0.15 : 1}>
                  <title>{`${a.name} → ${b.name}（${r.relation}）${r.description ? `\n${r.description}` : ""}`}</title>
                  <line
                    x1={a.x}
                    y1={a.y}
                    x2={b.x}
                    y2={b.y}
                    stroke={r.source === "ai" ? "hsl(var(--muted-foreground))" : "hsl(var(--primary))"}
                    strokeWidth={1.5}
                    strokeDasharray={r.source === "ai" ? "6 4" : undefined}
                  />
                  {/* 中点关系标签：衬底色块保证在连线上可读 */}
                  <rect
                    x={mx - r.relation.length * 6 - 4}
                    y={my - 9}
                    width={r.relation.length * 12 + 8}
                    height={18}
                    rx={4}
                    fill="hsl(var(--card))"
                    stroke="hsl(var(--border))"
                  />
                  <text
                    x={mx}
                    y={my + 4}
                    textAnchor="middle"
                    fontSize={11}
                    fill="hsl(var(--muted-foreground))"
                  >
                    {r.relation}
                  </text>
                </g>
              );
            })}
            {/* 节点 */}
            {nodes.map((n) => {
              const active = selectedNode === n.id;
              const dimmed = selectedNode !== null && !active &&
                !edges.some((r) => !edgeDimmed(r) && (r.from_character_id === n.id || r.to_character_id === n.id) && (r.from_character_id === selectedNode || r.to_character_id === selectedNode));
              return (
                <g
                  key={n.id}
                  opacity={dimmed ? 0.35 : 1}
                  className="cursor-pointer"
                  onClick={() => setSelectedNode(active ? null : n.id)}
                >
                  <title>{`${n.name}（${n.role}）`}</title>
                  <circle
                    cx={n.x}
                    cy={n.y}
                    r={n.center ? NODE_R + 6 : NODE_R}
                    fill={active ? "hsl(var(--primary))" : "hsl(var(--card))"}
                    stroke="hsl(var(--primary))"
                    strokeWidth={active || n.center ? 2.5 : 1.5}
                  />
                  <text
                    x={n.x}
                    y={n.y + 4}
                    textAnchor="middle"
                    fontSize={12}
                    fontWeight={n.center ? 600 : 400}
                    fill={active ? "hsl(var(--primary-foreground))" : "hsl(var(--card-foreground))"}
                  >
                    {n.name.length > 4 ? n.name.slice(0, 4) : n.name}
                  </text>
                </g>
              );
            })}
          </svg>
          <p className="border-t border-border px-4 py-2 text-xs text-muted-foreground">
            实线 = 手动添加，虚线 = AI 抽取；点击节点高亮相关关系
          </p>
        </div>
      )}

      {/* 关系列表（删除入口，比点边可靠） */}
      {relations.length > 0 && (
        <div className="mt-4 overflow-hidden rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/50 text-left text-xs text-muted-foreground">
                <th className="px-3 py-2 font-medium">起点</th>
                <th className="px-3 py-2 font-medium">关系</th>
                <th className="px-3 py-2 font-medium">终点</th>
                <th className="hidden px-3 py-2 font-medium md:table-cell">说明</th>
                <th className="px-3 py-2 font-medium">来源</th>
                <th className="w-10 px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {relations.map((r) => (
                <tr key={r.id} className="border-b border-border last:border-0">
                  <td className="px-3 py-2">{r.from_name}</td>
                  <td className="px-3 py-2 font-medium">{r.relation}</td>
                  <td className="px-3 py-2">{r.to_name}</td>
                  <td className="hidden max-w-48 truncate px-3 py-2 text-muted-foreground md:table-cell" title={r.description}>
                    {r.description || "—"}
                  </td>
                  <td className="px-3 py-2">
                    <Badge variant={r.source === "ai" ? "secondary" : "outline"}>
                      {r.source === "ai" ? "AI" : "手动"}
                    </Badge>
                  </td>
                  <td className="px-3 py-2">
                    <button
                      className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-destructive"
                      title="删除"
                      onClick={() => void removeRelation(r)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 手动添加对话框 */}
      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader><DialogTitle>添加人物关系</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>起点角色</Label>
                <Select value={form.from} onValueChange={(v) => setForm({ ...form, from: v })}>
                  <SelectTrigger><SelectValue placeholder="选择角色" /></SelectTrigger>
                  <SelectContent>
                    {characters.map((c) => (
                      <SelectItem key={c.id} value={String(c.id)}>{c.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>终点角色</Label>
                <Select value={form.to} onValueChange={(v) => setForm({ ...form, to: v })}>
                  <SelectTrigger><SelectValue placeholder="选择角色" /></SelectTrigger>
                  <SelectContent>
                    {characters.map((c) => (
                      <SelectItem key={c.id} value={String(c.id)}>{c.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-2">
              <Label>关系</Label>
              <Input
                value={form.relation}
                onChange={(e) => setForm({ ...form, relation: e.target.value })}
                placeholder="师徒 / 仇敌 / 兄妹…"
                maxLength={50}
              />
            </div>
            <div className="space-y-2">
              <Label>补充说明（可选）</Label>
              <Input
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                placeholder="如：少年时结仇，誓不两立"
                maxLength={300}
              />
            </div>
          </div>
          <DialogFooter><Button onClick={() => void addRelation()}>保存</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
