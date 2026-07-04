import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/store/auth";
import { useChat } from "@/store/chat";
import { useUI } from "@/store/ui";
import type { SessionRow } from "@/lib/types";
import { cn } from "@/lib/cn";
import { fmtAgo } from "@/lib/format";
import { Plus, Search, X, CaretLeft, CaretRight, CaretDown, DotsThree } from "@/lib/icons";

const GROUP_ORDER = ["Today", "Yesterday", "This week", "Older"] as const;
type GroupKey = (typeof GROUP_ORDER)[number];

function groupSessions(sessions: SessionRow[]): Record<GroupKey, SessionRow[]> {
  const out: Record<GroupKey, SessionRow[]> = { Today: [], Yesterday: [], "This week": [], Older: [] };
  const now = new Date();
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const day = 86400000;
  for (const s of sessions) {
    const t = s.last_active_at ? new Date(s.last_active_at).getTime() : 0;
    if (!t) out.Older.push(s);
    else if (t >= startToday) out.Today.push(s);
    else if (t >= startToday - day) out.Yesterday.push(s);
    else if (t >= startToday - 6 * day) out["This week"].push(s);
    else out.Older.push(s);
  }
  return out;
}

function SessionRowItem({ s }: { s: SessionRow }) {
  const user = useAuth((a) => a.user);
  const { sessionId, openSession, renameSession, deleteSession, newSession } = useChat();
  const setMobileNav = useUI((u) => u.setMobileNav);
  const [menuOpen, setMenuOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(s.title || "");
  const active = s.id === sessionId;
  const preview = s.topic || s.preview || s.last_message || fmtAgo(s.last_active_at);

  function commit() {
    const v = draft.trim();
    if (v) renameSession(s.id, v);
    setEditing(false);
  }
  function onDelete() {
    setMenuOpen(false);
    const wasActive = active;
    deleteSession(s.id);
    if (wasActive) newSession();
  }

  return (
    <div
      className={cn(
        "group relative flex cursor-pointer gap-2 rounded-lg py-2 pl-2.5 pr-2 text-ink",
        "border-l-[3px]",
        active ? "border-navy bg-accent-soft" : "border-transparent hover:bg-canvas",
      )}
      onClick={() => {
        if (!editing && user) {
          void openSession(user.id, s.id);
          setMobileNav(false);
        }
      }}
    >
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        {editing ? (
          <input
            autoFocus
            value={draft}
            aria-label="Rename session"
            onClick={(e) => e.stopPropagation()}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") commit();
              else if (e.key === "Escape") setEditing(false);
            }}
            onBlur={commit}
            className="w-full rounded-md border border-gold bg-surface px-1.5 py-0.5 text-[13px] font-semibold outline-none"
          />
        ) : (
          <div className="truncate text-[13px] font-semibold">{s.title || "Session"}</div>
        )}
        <div className="truncate text-[11px] text-ink-2">{preview}</div>
      </div>

      <button
        aria-label="Session options"
        onClick={(e) => {
          e.stopPropagation();
          setMenuOpen((v) => !v);
        }}
        className={cn(
          "absolute right-1.5 top-1/2 flex h-[26px] w-[26px] -translate-y-1/2 items-center justify-center rounded-md border border-divider bg-surface text-ink-2 opacity-0 transition-opacity",
          "group-hover:opacity-100 group-focus-within:opacity-100",
        )}
      >
        <DotsThree size={15} weight="bold" />
      </button>
      {menuOpen && (
        <div
          className="absolute right-1.5 top-[calc(50%+16px)] z-20 w-36 rounded-lg border border-divider bg-surface p-1.5 shadow-pop"
          onClick={(e) => e.stopPropagation()}
        >
          <button
            className="w-full rounded-md px-2.5 py-2 text-left text-[13px] hover:bg-canvas"
            onClick={() => {
              setEditing(true);
              setMenuOpen(false);
            }}
          >
            Rename
          </button>
          <button
            className="w-full rounded-md px-2.5 py-2 text-left text-[13px] text-bad hover:bg-canvas"
            onClick={onDelete}
          >
            Delete
          </button>
        </div>
      )}
    </div>
  );
}

export default function Sidebar() {
  const user = useAuth((a) => a.user);
  const { sessions, newSession } = useChat();
  const { sidebarCollapsed, toggleSidebar, mobileNavOpen, setMobileNav } = useUI();
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({
    Today: true,
    Yesterday: true,
    "This week": true,
    Older: false,
  });
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (searchOpen) searchRef.current?.focus();
  }, [searchOpen]);

  const collapsed = sidebarCollapsed; // desktop-only visual collapse
  const filtered = sessions.filter(
    (s) =>
      !query ||
      (s.title || "").toLowerCase().includes(query.toLowerCase()) ||
      (s.topic || "").toLowerCase().includes(query.toLowerCase()),
  );
  const groups = groupSessions(filtered);

  return (
    <aside
      className={cn(
        "ny-sc flex flex-none flex-col border-r border-divider bg-surface transition-[width] duration-200",
        collapsed ? "md:w-12" : "md:w-60",
        // mobile: off-canvas drawer
        "fixed left-0 top-12 z-30 h-[calc(100dvh-48px)] w-[min(84vw,300px)] shadow-modal md:static md:h-auto md:w-60 md:shadow-none",
        mobileNavOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0",
        "transition-transform",
      )}
    >
      {/* top: collapse + search */}
      <div className="flex items-center gap-1.5 border-b border-divider p-2.5">
        <button
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          onClick={toggleSidebar}
          className="hidden h-9 w-9 flex-none items-center justify-center rounded-lg text-ink-2 hover:bg-canvas md:flex"
        >
          {collapsed ? <CaretRight size={17} /> : <CaretLeft size={17} />}
        </button>
        {!collapsed &&
          (searchOpen ? (
            <>
              <input
                ref={searchRef}
                value={query}
                placeholder="Search sessions"
                aria-label="Search sessions"
                onChange={(e) => setQuery(e.target.value)}
                className="min-w-0 flex-1 rounded-lg border border-divider bg-surface px-2.5 py-1.5 text-[13px] outline-none focus:border-gold"
              />
              <button
                aria-label="Close search"
                onClick={() => {
                  setSearchOpen(false);
                  setQuery("");
                }}
                className="flex h-8 w-8 flex-none items-center justify-center rounded-lg text-ink-2 hover:bg-canvas"
              >
                <X size={15} />
              </button>
            </>
          ) : (
            <>
              <div className="flex-1" />
              <button
                aria-label="Search sessions"
                onClick={() => setSearchOpen(true)}
                className="flex h-9 w-9 items-center justify-center rounded-lg text-ink-2 hover:bg-canvas"
              >
                <Search size={17} />
              </button>
            </>
          ))}
      </div>

      {/* session list */}
      <div className="ny-sc flex flex-1 flex-col gap-1 overflow-y-auto p-2">
        {!collapsed &&
          (filtered.length === 0 ? (
            <div className="p-2 text-[12.5px] text-ink-3">
              {sessions.length ? "No matches." : "No sessions yet."}
            </div>
          ) : (
            GROUP_ORDER.map((label) => {
              const items = groups[label];
              if (!items.length) return null;
              const open = openGroups[label] !== false;
              return (
                <div key={label}>
                  <button
                    onClick={() => setOpenGroups((g) => ({ ...g, [label]: !open }))}
                    className="flex w-full items-center gap-1.5 px-2 pb-1 pt-2 text-[10.5px] font-bold uppercase tracking-[0.06em] text-ink-3"
                  >
                    <CaretDown
                      size={12}
                      weight="bold"
                      className={cn("transition-transform", open ? "" : "-rotate-90")}
                    />
                    {label}
                    <span className="ml-auto font-semibold">{items.length}</span>
                  </button>
                  {open && items.map((s) => <SessionRowItem key={s.id} s={s} />)}
                </div>
              );
            })
          ))}
        {collapsed &&
          sessions.slice(0, 14).map((s) => (
            <button
              key={s.id}
              aria-label={s.title || "Session"}
              title={s.title || "Session"}
              onClick={() => user && void useChat.getState().openSession(user.id, s.id)}
              className="flex h-9 items-center justify-center rounded-lg text-ink-2 hover:bg-canvas"
            >
              <span className="text-xs font-bold">{(s.title || "S").slice(0, 1).toUpperCase()}</span>
            </button>
          ))}
      </div>

      {/* footer */}
      <div className="border-t border-divider p-2">
        <button
          onClick={() => {
            newSession();
            setMobileNav(false);
          }}
          className={cn(
            "flex h-11 w-full items-center justify-center gap-2 rounded-xl border border-divider text-[13.5px] font-semibold text-ink hover:bg-canvas active:scale-[0.98]",
          )}
          aria-label="New session"
        >
          <Plus size={16} weight="bold" />
          {!collapsed && "New Session"}
        </button>
      </div>
    </aside>
  );
}
