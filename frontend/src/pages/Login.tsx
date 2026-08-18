import { useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import BeidouMark from "@/components/BeidouMark";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/store/auth";

export default function Login() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const login = useAuth((s) => s.login);
  const navigate = useNavigate();

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!username || !password) return;
    setLoading(true);
    try {
      await login(username, password);
      navigate("/", { replace: true });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "登录失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="rise-in w-full max-w-sm">
        <div className="mb-10 flex flex-col items-center">
          <BeidouMark className="h-10 w-16 text-primary" />
          <h1 className="font-content mt-5 text-3xl tracking-wide text-foreground">
            北斗
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            AI 网文创作工作台
          </p>
        </div>

        <form
          onSubmit={onSubmit}
          className="rounded-lg border border-border bg-card p-8 shadow-[0_1px_20px_rgba(0,0,0,0.03)]"
        >
          <div className="space-y-5">
            <div className="space-y-2">
              <Label htmlFor="username">用户名</Label>
              <Input
                id="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                autoFocus
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">密码</Label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
              />
            </div>
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? "登录中…" : "登录"}
            </Button>
          </div>
        </form>

        <p className="mt-6 text-center text-xs text-muted-foreground">
          前世抟土造人，今生敲码创世
        </p>
      </div>
    </div>
  );
}
