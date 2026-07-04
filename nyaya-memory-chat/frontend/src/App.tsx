import { useEffect } from "react";
import { useAuth } from "@/store/auth";
import { useChat } from "@/store/chat";
import AuthScreen from "@/components/auth/AuthScreen";
import AppShell from "@/components/layout/AppShell";
import { Scales } from "@/lib/icons";

export default function App() {
  const { user, checked, init } = useAuth();
  const loadSessions = useChat((s) => s.loadSessions);

  useEffect(() => {
    void init();
  }, [init]);

  useEffect(() => {
    if (user) void loadSessions(user.id);
  }, [user, loadSessions]);

  if (!checked) {
    return (
      <div className="flex min-h-[100dvh] items-center justify-center">
        <div className="flex flex-col items-center gap-3 text-ink-3">
          <div className="flex h-12 w-12 animate-pulse items-center justify-center rounded-[13px] bg-navy">
            <Scales size={26} className="text-white" />
          </div>
          <span className="text-sm">Loading Nyaya.AI…</span>
        </div>
      </div>
    );
  }

  return user ? <AppShell /> : <AuthScreen />;
}
