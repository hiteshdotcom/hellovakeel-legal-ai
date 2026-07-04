import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/store/auth";
import { useChat } from "@/store/chat";
import { Send, Shield, Lightning } from "@/lib/icons";
import { cn } from "@/lib/cn";

export default function Composer() {
  const user = useAuth((a) => a.user);
  const { streaming, send } = useChat();
  const [draft, setDraft] = useState("");
  const taRef = useRef<HTMLTextAreaElement>(null);
  const canSend = draft.trim().length > 0 && !streaming;

  useEffect(() => {
    if (!streaming) taRef.current?.focus();
  }, [streaming]);

  function autoGrow() {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(160, ta.scrollHeight) + "px";
  }

  function submit() {
    if (!canSend || !user) return;
    const text = draft;
    setDraft("");
    if (taRef.current) taRef.current.style.height = "auto";
    void send(user.id, text);
  }

  return (
    <div className="flex-none border-t border-divider bg-surface px-6 pb-4 pt-3">
      <div className="mx-auto flex max-w-[760px] flex-col gap-2.5">
        <div className="flex flex-col gap-2 rounded-2xl border-[1.5px] border-divider bg-surface p-3 shadow-card">
          <textarea
            ref={taRef}
            rows={1}
            value={draft}
            aria-label="Message"
            placeholder="Ask about a judgment, statute, or your matter…"
            onChange={(e) => {
              setDraft(e.target.value);
              autoGrow();
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            className="max-h-40 w-full resize-none bg-transparent text-[15px] leading-relaxed text-ink outline-none"
          />
          <div className="flex items-center justify-between gap-2">
            <span
              title="Cognee retrieves relevant judgments from its corpus"
              className="inline-flex h-10 items-center gap-1.5 rounded-xl border border-divider px-2.5 text-[12.5px] font-bold text-accent-ink"
            >
              <Lightning size={15} weight="fill" className="text-gold" />
              <span className="hidden md:inline">Cognee</span>
            </span>
            <button
              aria-label="Send message"
              disabled={!canSend}
              onClick={submit}
              className={cn(
                "flex h-10 w-10 flex-none items-center justify-center rounded-xl transition-colors",
                canSend
                  ? "cursor-pointer bg-gold text-navy active:scale-95"
                  : "cursor-not-allowed bg-divider text-ink-3 opacity-40",
              )}
            >
              <Send size={18} weight="bold" />
            </button>
          </div>
        </div>
        <div className="flex items-center justify-between text-[11px] text-ink-3">
          <span className="inline-flex items-center gap-1.5">
            <Shield size={12} weight="bold" className="text-good" />
            Grounded · Citations verified
          </span>
          <span className="hidden font-mono md:inline">↵ Enter · Shift+↵ newline</span>
        </div>
      </div>
    </div>
  );
}
