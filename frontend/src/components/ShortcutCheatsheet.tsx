import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { formatKey, groupedShortcuts, type ShortcutEntry } from "@/lib/shortcuts";

const SECTION_LABELS: Record<ShortcutEntry["section"], string> = {
  写作: "写作",
  保存: "保存",
  AI: "AI",
  导航: "导航",
  视图: "视图",
};

/** 快捷键速查面板：「?」 键或菜单触发，列出所有 SHORTCUTS。 */
export default function ShortcutCheatsheet({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
  const groups = groupedShortcuts();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>快捷键速查</DialogTitle>
          <DialogDescription>
            在编辑器外或快捷键白名单项可在编辑元素中触发。按 <kbd className="rounded border border-input bg-muted px-1.5 text-xs">?</kbd> 重新打开。
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-[60vh] space-y-5 overflow-y-auto pr-1">
          {Object.entries(groups).map(([section, items]) => (
            <div key={section}>
              <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                {SECTION_LABELS[section as ShortcutEntry["section"]] ?? section}
              </h3>
              <ul className="space-y-1.5">
                {items.map((s) => (
                  <li
                    key={s.key}
                    className="flex items-center justify-between gap-3 rounded px-2 py-1.5 text-sm hover:bg-muted/40"
                  >
                    <div className="min-w-0">
                      <div className="font-medium">{s.label}</div>
                      <div className="truncate text-xs text-muted-foreground">{s.desc}</div>
                    </div>
                    <kbd className="shrink-0 rounded border border-input bg-muted/60 px-2 py-0.5 text-xs font-mono tnum">
                      {formatKey(s.key, isMac)}
                    </kbd>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
