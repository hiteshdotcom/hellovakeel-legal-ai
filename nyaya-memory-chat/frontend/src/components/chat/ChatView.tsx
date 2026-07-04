import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useChat } from "@/store/chat";
import { useUI } from "@/store/ui";
import Message from "./Message";
import Composer from "./Composer";
import { cn } from "@/lib/cn";
import {
  Scales,
  Shield,
  Check,
  Brain,
  FileText,
  Warning,
  CaretRight,
  CaretDown,
  Sun,
  Moon,
} from "@/lib/icons";

const PILLARS: Array<[typeof Shield, string, string]> = [
  [Shield, "Grounded", "Every answer from real judgments"],
  [Check, "Verified", "Citations checked before they reach you"],
  [Brain, "Remembered", "Your matter across every session"],
];

function PipelineIndicator({ active }: { active: boolean }) {
  return (
    <div className="hidden items-center gap-2 md:flex" aria-hidden>
      <div className="flex items-center gap-0.5">
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex items-center gap-0.5">
            {i > 0 && (
              <span className={cn("h-0.5 w-3.5 rounded", active ? "bg-gold" : "bg-divider")} />
            )}
            <span
              className={cn("h-2 w-2 rounded-full", active ? "bg-gold" : "bg-divider")}
              style={active ? { animation: `1s ${i * 0.2}s ease-in-out infinite pulse` } : undefined}
            />
          </div>
        ))}
      </div>
      <span className="text-[11px] text-ink-3">
        {active ? "Composing…" : "Recall · Retrieve · Compose"}
      </span>
    </div>
  );
}

function currentTitle(): string {
  const { sessions, sessionId, messages } = useChat.getState();
  const s = sessions.find((x) => x.id === sessionId);
  if (s?.title) return s.title;
  return messages.length ? messages[0].text.slice(0, 46) : "New Session";
}

export default function ChatView() {
  const { messages, sources, streaming, warnings } = useChat();
  const { theme, toggleTheme, setSourcesDrawer } = useUI();
  const threadRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true); // is the user stuck to the bottom?
  const prevLenRef = useRef(0);
  const [showJump, setShowJump] = useState(false);
  const dirty = warnings.length > 0;

  const NEAR_BOTTOM = 96; // px tolerance for "at the bottom"

  const onThreadScroll = () => {
    const t = threadRef.current;
    if (!t) return;
    const dist = t.scrollHeight - t.scrollTop - t.clientHeight;
    pinnedRef.current = dist < NEAR_BOTTOM;
    setShowJump(dist >= NEAR_BOTTOM && messages.length > 0);
  };

  const scrollToBottom = (smooth = false) => {
    const t = threadRef.current;
    if (!t) return;
    t.scrollTo({ top: t.scrollHeight, behavior: smooth ? "smooth" : "auto" });
    pinnedRef.current = true;
    setShowJump(false);
  };

  // ChatGPT-style anchoring: follow new tokens ONLY while pinned to the bottom.
  // A newly-added message (the user just sent) always re-pins; if the user has
  // scrolled up to read, streaming never yanks them down.
  useEffect(() => {
    const t = threadRef.current;
    if (!t) return;
    const grew = messages.length > prevLenRef.current;
    prevLenRef.current = messages.length;
    if (grew) {
      pinnedRef.current = true;
      requestAnimationFrame(() => scrollToBottom(false));
    } else if (pinnedRef.current) {
      requestAnimationFrame(() => {
        t.scrollTop = t.scrollHeight;
      });
    }
  }, [messages]);

  return (
    <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-canvas">
      {/* header */}
      <header className="flex h-14 flex-none items-center gap-3.5 border-b border-divider bg-surface px-[18px]">
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <div className="truncate font-serif text-[17px] font-semibold">{currentTitle()}</div>
          <PipelineIndicator active={streaming} />
        </div>
        <button
          aria-label="Toggle sources panel"
          onClick={() => setSourcesDrawer(true)}
          className="flex flex-none items-center gap-1.5 rounded-full border border-divider px-2.5 py-1.5 text-xs font-bold text-ink hover:bg-canvas"
        >
          <FileText size={14} />
          Sources ({sources.length})
          <CaretRight size={13} weight="bold" />
        </button>
        <span
          className={cn(
            "hidden flex-none items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11.5px] font-bold md:inline-flex",
            dirty ? "border-warn-bd bg-warn-bg text-warn" : "border-good-bd bg-good-bg text-good",
          )}
        >
          {dirty ? <Warning size={13} weight="bold" /> : <Check size={13} weight="bold" />}
          {dirty ? `${warnings.length} to review` : "Verified"}
        </span>
        <button
          aria-label="Toggle theme"
          onClick={toggleTheme}
          className="flex h-9 w-9 flex-none items-center justify-center rounded-lg text-ink-2 hover:bg-canvas"
        >
          {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
        </button>
      </header>

      {/* thread */}
      <div className="relative flex min-h-0 flex-1 flex-col">
        <div
          ref={threadRef}
          onScroll={onThreadScroll}
          role="log"
          aria-live="polite"
          aria-label="Chat messages"
          className="ny-sc flex-1 overflow-y-auto overflow-x-hidden px-6 pb-3.5 pt-7"
        >
        <div className={cn("mx-auto flex w-full max-w-[760px] flex-col gap-5")}>
          {messages.length === 0 ? (
            <div className="flex flex-col items-center gap-4 px-2 py-6 text-center">
              <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-accent-soft">
                <Scales size={30} className="text-accent-ink" />
              </div>
              <div className="font-serif text-[26px] font-medium tracking-tight">
                Ask about Indian case law
              </div>
              <div className="max-w-[440px] text-sm leading-relaxed text-ink-3">
                Every answer is grounded in retrieved judgments and verified before it reaches you.
                Your matter is remembered across sessions.
              </div>
              <div className="mt-1.5 grid w-full max-w-[560px] grid-cols-3 gap-3.5">
                {PILLARS.map(([Icon, t, d]) => (
                  <div key={t} className="flex flex-col items-center gap-1.5 p-1.5">
                    <span className="flex h-9 w-9 items-center justify-center rounded-[10px] border border-divider bg-canvas">
                      <Icon size={16} className="text-accent-ink" />
                    </span>
                    <div className="text-[12.5px] font-bold text-ink">{t}</div>
                    <div className="hidden max-w-[160px] text-[11.5px] leading-snug text-ink-3 md:block">
                      {d}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            messages.map((m, i) => <Message key={i} m={m} />)
          )}
        </div>
        </div>

        <AnimatePresence>
          {showJump && (
            <motion.button
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 6 }}
              transition={{ duration: 0.15 }}
              onClick={() => scrollToBottom(true)}
              aria-label="Scroll to latest"
              className="absolute bottom-4 left-1/2 flex h-9 w-9 -translate-x-1/2 items-center justify-center rounded-full border border-divider bg-surface text-ink-2 shadow-pop transition-colors hover:text-ink active:scale-95"
            >
              <CaretDown size={18} />
            </motion.button>
          )}
        </AnimatePresence>
      </div>

      <Composer />
    </main>
  );
}
