import { Link, useNavigate } from "react-router";
import { ChevronLeft, Library, LibraryBig, LogOut, Settings2 } from "lucide-react";
import BeidouMark from "@/components/BeidouMark";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuth } from "@/store/auth";

/** 紧凑 48px 顶栏 + 全屏主舞台 */
export default function AppShell({
  children,
  back,
  title,
  actions,
  focus,
}: {
  children: React.ReactNode;
  back?: string;
  title?: React.ReactNode;
  actions?: React.ReactNode;
  focus?: boolean; // 专注模式：隐藏顶栏
}) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  return (
    <div className="flex h-screen flex-col bg-background">
      <header className={`flex h-12 shrink-0 items-center gap-2 border-b border-border bg-card px-3 ${focus ? "hidden" : ""}`}>
        {back ? (
          <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => navigate(back)}>
            <ChevronLeft className="h-4 w-4" />
          </Button>
        ) : (
          <Link to="/" className="flex items-center gap-2 px-1">
            <BeidouMark className="h-5 w-8 text-primary" />
            <span className="font-content text-base font-semibold tracking-wide">北斗</span>
          </Link>
        )}
        {title && (
          <>
            <span className="h-4 w-px bg-border" />
            <div className="min-w-0 flex-1 truncate text-sm text-foreground">{title}</div>
          </>
        )}
        {!title && <div className="flex-1" />}
        {actions}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className="ml-1 flex h-7 w-7 items-center justify-center rounded-full bg-primary/10 text-xs font-medium text-primary outline-none transition-colors hover:bg-primary/20">
              {user?.username.slice(0, 1).toUpperCase()}
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-40">
            <DropdownMenuItem onClick={() => navigate("/")}>
              <Library className="mr-2 h-4 w-4" />
              书架
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => navigate("/library")}>
              <LibraryBig className="mr-2 h-4 w-4" />
              资料库
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => navigate("/account")}>
              <Settings2 className="mr-2 h-4 w-4" />
              账号设置
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onClick={() => {
                logout();
                navigate("/login");
              }}
            >
              <LogOut className="mr-2 h-4 w-4" />
              退出登录
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </header>
      <main className="min-h-0 flex-1">{children}</main>
    </div>
  );
}
