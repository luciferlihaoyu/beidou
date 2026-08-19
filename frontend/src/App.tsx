import { useEffect } from "react";
import { Navigate, Route, Routes, useParams } from "react-router";
import { Toaster } from "@/components/ui/sonner";
import { useAuth } from "@/store/auth";
import Login from "@/pages/Login";
import Bookshelf from "@/pages/Bookshelf";
import Editor from "@/pages/Editor";
import NovelSettings from "@/pages/NovelSettings";
import Account from "@/pages/Account";
import Library from "@/pages/Library";

function NovelLibrary() {
  const { id } = useParams();
  return <Library novelId={Number(id)} />;
}

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, ready } = useAuth();
  if (!ready) {
    return (
      <div className="flex h-screen items-center justify-center bg-background">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  const bootstrap = useAuth((s) => s.bootstrap);

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  return (
    <>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/"
          element={
            <RequireAuth>
              <Bookshelf />
            </RequireAuth>
          }
        />
        <Route
          path="/novel/:id"
          element={
            <RequireAuth>
              <Editor />
            </RequireAuth>
          }
        />
        <Route
          path="/novel/:id/settings"
          element={
            <RequireAuth>
              <NovelSettings />
            </RequireAuth>
          }
        />
        <Route
          path="/library"
          element={
            <RequireAuth>
              <Library />
            </RequireAuth>
          }
        />
        <Route
          path="/novel/:id/library"
          element={
            <RequireAuth>
              <NovelLibrary />
            </RequireAuth>
          }
        />
        <Route
          path="/account"
          element={
            <RequireAuth>
              <Account />
            </RequireAuth>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <Toaster position="top-center" richColors />
    </>
  );
}
