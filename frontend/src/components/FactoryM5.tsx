/** AI 工厂 M5 · 简介弹窗 + 封面 prompt 弹窗
 *
 * SynopsisDialog：4 版本简介卡片（精简/标准/推广/抖音），一键复制/重新生成。
 * CoverDialog：封面中文画面描述 + 英文绘图 prompt（可复制去天宫发任务），
 * 配了天宫通道时显示任务已提交。
 */

import { useState } from "react";
import { toast } from "sonner";
import { Copy, Image as ImageIcon, Loader2, RefreshCw, Send, Type } from "lucide-react";
import { aiFactoryM5, type AiProject, type AiSynopsis } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

function copyText(t: string) {
  navigator.clipboard
    .writeText(t)
    .then(() => toast.success("已复制"))
    .catch(() => toast.error("复制失败"));
}

const VERSIONS: { key: keyof AiSynopsis; label: string; hint: string }[] = [
  { key: "short", label: "精简版", hint: "50-100 字 · 平台投稿" },
  { key: "standard", label: "标准版", hint: "200-300 字 · 详情页" },
  { key: "promotion", label: "推广版", hint: "500 字内 · 社区推文" },
  { key: "douyin", label: "抖音版", hint: "30 字内 · 短视频钩子" },
];

export function SynopsisDialog({
  project,
  open,
  onOpenChange,
  onChange,
}: {
  project: AiProject;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onChange: (p: AiProject) => void;
}) {
  const [busy, setBusy] = useState(false);
  const synopsis = project.synopsis;

  async function generate() {
    setBusy(true);
    try {
      const r = await aiFactoryM5.synopsis(project.id);
      onChange({ ...project, synopsis: r });
      toast.success("简介已生成");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "生成失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] flex-col sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Type className="h-4 w-4 text-primary" />
            多版本简介
          </DialogTitle>
          <DialogDescription>四种投放场景各一版，都有钩子、不剧透结局。</DialogDescription>
        </DialogHeader>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
          {synopsis ? (
            VERSIONS.map((v) => (
              <div key={v.key} className="rounded-lg border border-border bg-card p-3">
                <div className="mb-1.5 flex items-center justify-between">
                  <p className="text-sm font-medium">
                    {v.label} <span className="text-xs font-normal text-muted-foreground">{v.hint}</span>
                  </p>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 px-2 text-xs"
                    onClick={() => copyText(synopsis[v.key] ?? "")}
                  >
                    <Copy className="mr-1 h-3 w-3" />
                    复制
                  </Button>
                </div>
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground/90">
                  {synopsis[v.key] || "（未生成）"}
                </p>
              </div>
            ))
          ) : (
            <p className="py-8 text-center text-sm text-muted-foreground">
              还没有简介——点击下方生成（需要先确认立项草案）
            </p>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-border pt-3">
          <Button size="sm" disabled={busy} onClick={() => void generate()}>
            {busy ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="mr-1 h-3.5 w-3.5" />}
            {synopsis ? "重新生成" : "生成简介"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function CoverDialog({
  project,
  open,
  onOpenChange,
  onChange,
}: {
  project: AiProject;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onChange: (p: AiProject) => void;
}) {
  const [style, setStyle] = useState("玄幻风插画");
  const [busy, setBusy] = useState(false);
  const cover = project.cover_prompt;

  async function generate() {
    setBusy(true);
    try {
      const r = await aiFactoryM5.coverPrompt(project.id, style);
      onChange({ ...project, cover_prompt: r });
      if (r.tiangong_task) toast.success("封面 prompt 已生成，天宫任务已提交");
      else toast.success("封面 prompt 已生成");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "生成失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ImageIcon className="h-4 w-4 text-primary" />
            封面方案
          </DialogTitle>
          <DialogDescription>
            生成封面绘图 prompt——复制发给碧霄/婉儿在天宫生成图像；配置天宫通道后此处自动提交任务。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div>
            <Label className="text-xs">画风</Label>
            <Input
              className="mt-1 h-8 text-sm"
              value={style}
              onChange={(e) => setStyle(e.target.value)}
              placeholder="如：玄幻风插画 / 国风工笔 / 赛博霓虹"
            />
          </div>

          {cover?.concept && (
            <div className="space-y-2.5">
              <div className="rounded-lg border border-border bg-card p-3">
                <p className="mb-1 text-xs font-medium text-muted-foreground">画面构思</p>
                <p className="text-sm leading-relaxed">{cover.concept}</p>
              </div>
              <div className="rounded-lg border border-border bg-card p-3">
                <div className="mb-1 flex items-center justify-between">
                  <p className="text-xs font-medium text-muted-foreground">绘图 prompt（英文）</p>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 px-2 text-xs"
                    onClick={() => copyText(cover.prompt_en ?? "")}
                  >
                    <Copy className="mr-1 h-3 w-3" />
                    复制
                  </Button>
                </div>
                <p className="break-all font-mono text-xs leading-relaxed text-foreground/80">{cover.prompt_en}</p>
              </div>
              {cover.negative && (
                <div className="rounded-lg border border-border bg-card p-3">
                  <div className="mb-1 flex items-center justify-between">
                    <p className="text-xs font-medium text-muted-foreground">负面 prompt</p>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2 text-xs"
                      onClick={() => copyText(cover.negative ?? "")}
                    >
                      <Copy className="mr-1 h-3 w-3" />
                      复制
                    </Button>
                  </div>
                  <p className="break-all font-mono text-xs text-foreground/70">{cover.negative}</p>
                </div>
              )}
              {cover.tiangong_task ? (
                <p className="rounded-md bg-green-500/10 px-3 py-2 text-xs text-green-600 dark:text-green-400">
                  ✅ 天宫任务已提交（{cover.tiangong_task.ref}）——生成完成后取图
                </p>
              ) : (
                <p className="rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
                  💡 复制 prompt 发给碧霄/婉儿即可在天宫生成封面；或部署时配置 TIANGONG_BASE_URL +
                  TIANGONG_SERVICE_KEY 后自动提交任务
                </p>
              )}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-border pt-3">
          <Button size="sm" disabled={busy} onClick={() => void generate()}>
            {busy ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : <Send className="mr-1 h-3.5 w-3.5" />}
            {cover?.prompt_en ? "重新生成" : "生成封面方案"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
