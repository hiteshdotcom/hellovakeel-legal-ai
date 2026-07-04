import { useMemo, useState } from "react";
import { motion } from "framer-motion";
import { useAuth } from "@/store/auth";
import { useChat } from "@/store/chat";
import type { ClarifyQuestion } from "@/lib/types";
import { Question, Send } from "@/lib/icons";
import { cn } from "@/lib/cn";

function stripQ(q: string): string {
  return q.replace(/\?+\s*$/, "").trim();
}

export default function ClarifyCard({
  preamble,
  questions,
}: {
  preamble: string;
  questions: ClarifyQuestion[];
}) {
  const user = useAuth((a) => a.user);
  const { send, streaming } = useChat();
  const [picked, setPicked] = useState<Record<number, string>>({});

  const composed = useMemo(
    () =>
      questions
        .map((q, i) => (picked[i] ? `${stripQ(q.q)}: ${picked[i]}` : null))
        .filter(Boolean)
        .join(". "),
    [picked, questions],
  );
  const canSend = composed.length > 0 && !streaming && !!user;

  function sendAnswers() {
    if (!canSend || !user) return;
    void send(user.id, composed + ".");
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className="flex flex-col gap-3.5"
    >
      <div className="inline-flex items-center gap-2 self-start rounded-full border border-[color:var(--gold)]/40 bg-gold-soft px-2.5 py-1">
        <Question size={14} weight="bold" className="text-[color:var(--gold)]" />
        <span className="text-[11.5px] font-bold text-[color:var(--gold)]">A bit more context</span>
      </div>

      <div className="text-[14.5px] leading-relaxed text-ink">{preamble}</div>

      <div className="flex flex-col gap-3">
        {questions.map((q, i) => (
          <div key={i} className="flex flex-col gap-1.5">
            <div className="text-[13.5px] font-semibold text-ink-2">{q.q}</div>
            <div className="flex flex-wrap gap-1.5">
              {q.chips.map((c) => {
                const active = picked[i] === c;
                return (
                  <button
                    key={c}
                    onClick={() => setPicked((p) => ({ ...p, [i]: active ? "" : c }))}
                    className={cn(
                      "rounded-full border px-3 py-1.5 text-[12.5px] font-semibold transition-colors",
                      active
                        ? "border-navy bg-navy text-white"
                        : "border-divider bg-canvas text-ink-2 hover:border-navy/40 hover:text-ink",
                    )}
                  >
                    {c}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      <button
        disabled={!canSend}
        onClick={sendAnswers}
        className={cn(
          "inline-flex h-10 items-center justify-center gap-2 self-start rounded-xl px-4 text-[13px] font-bold transition-colors",
          canSend
            ? "cursor-pointer bg-gold text-navy active:scale-95"
            : "cursor-not-allowed bg-divider text-ink-3 opacity-50",
        )}
      >
        <Send size={16} weight="bold" />
        Send answers
      </button>
      <div className="text-[11.5px] text-ink-3">Or just type your answer below.</div>
    </motion.div>
  );
}
