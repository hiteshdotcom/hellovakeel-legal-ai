import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/store/auth";
import { useChat } from "@/store/chat";
import { useUI } from "@/store/ui";
import { Scales, Menu, X, SignOut, CaretDown } from "@/lib/icons";
import { initialsOf } from "@/lib/format";

export default function TopBar() {
  const { user, logout } = useAuth();
  const resetChat = useChat((s) => s.resetForLogout);
  const { mobileNavOpen, setMobileNav } = useUI();
  const [menuOpen, setMenuOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [menuOpen]);

  const name = user?.name || user?.email || "Account";
  const initials = initialsOf(user?.name || user?.email);

  async function doLogout() {
    setMenuOpen(false);
    await logout();
    resetChat();
  }

  return (
    <header className="sticky top-0 z-40 flex h-12 flex-none items-center gap-3 bg-navy px-3.5 text-white">
      <button
        aria-label={mobileNavOpen ? "Close menu" : "Open menu"}
        onClick={() => setMobileNav(!mobileNavOpen)}
        className="flex h-9 w-9 flex-none items-center justify-center rounded-lg hover:bg-white/10 active:scale-95 md:hidden"
      >
        {mobileNavOpen ? <X size={20} /> : <Menu size={20} />}
      </button>

      <div className="flex items-center gap-2.5">
        <div className="flex h-7 w-7 flex-none items-center justify-center rounded-lg bg-white/10">
          <Scales size={17} className="text-white" />
        </div>
        <div className="font-serif text-[17px] font-semibold tracking-tight">
          Nyaya<span className="text-gold">.AI</span>
        </div>
      </div>

      <div className="flex-1" />

      <div ref={wrapRef} className="relative">
        <button
          aria-label="Account menu"
          onClick={() => setMenuOpen((v) => !v)}
          className="flex items-center gap-2.5 rounded-full border border-white/15 bg-white/10 py-1 pl-1 pr-2.5 text-white hover:bg-white/[0.16]"
        >
          {user?.avatar_url ? (
            <img src={user.avatar_url} alt="" className="h-7 w-7 flex-none rounded-full object-cover" />
          ) : (
            <span className="flex h-7 w-7 flex-none items-center justify-center rounded-full bg-gold text-xs font-bold text-navy">
              {initials}
            </span>
          )}
          <span className="hidden max-w-[160px] truncate text-[13px] font-semibold md:block">{name}</span>
          <CaretDown size={14} className="text-white/70" />
        </button>

        {menuOpen && (
          <div className="absolute right-0 top-11 w-64 rounded-xl border border-divider bg-surface p-1.5 text-ink shadow-pop">
            <div className="mb-1.5 flex items-center gap-2.5 border-b border-divider p-2.5">
              <span className="flex h-8 w-8 flex-none items-center justify-center rounded-full bg-accent-soft text-xs font-bold text-accent-ink">
                {initials}
              </span>
              <div className="min-w-0">
                <div className="truncate text-[13px] font-bold">{name}</div>
                <div className="truncate text-[11px] text-ink-3">{user?.email}</div>
              </div>
            </div>
            <button
              onClick={doLogout}
              className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2.5 text-left text-[13px] font-semibold hover:bg-canvas"
            >
              <SignOut size={16} /> Sign out
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
