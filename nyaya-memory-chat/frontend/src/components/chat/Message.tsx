import { memo, useState } from "react";
import { motion } from "framer-motion";
import type { UIMessage } from "@/store/chat";
import { useChat } from "@/store/chat";
import { useUI } from "@/store/ui";
import Answer from "./Answer";
import ThinkingIndicator from "./ThinkingIndicator";
import ClarifyCard from "./ClarifyCard";
import { cn } from "@/lib/cn";
import { Brain, Check, Warning, Copy, ThumbsUp, ThumbsDown, CaretRight } from "@/lib/icons";

function Message({ m }: { m: UIMessage }) {
  if (m.role === "user") {
    return (
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.2 }}
        className="flex justify-end"
      >
        <div className="max-w-[74%] rounded-2xl rounded-br-sm bg-navy px-4 py-3 text-[15px] leading-relaxed text-white">
          {m.text}
        </div>
      </motion.div>
    );
  }
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className="flex flex-col gap-3 rounded-xl border border-divider border-l-2 border-l-gold bg-surface px-[18px] py-4"
    >
      {m.recalled.length > 0 && (
        <div
          title={m.recalled.join(" · ")}
          className="inline-flex items-center gap-1.5 self-start rounded-full border border-divider bg-accent-soft px-2.5 py-1"
        >
          <Brain size={14} className="text-accent-ink" />
          <span className="text-[11.5px] font-bold text-accent-ink">
            Recalled {m.recalled.length} fact{m.recalled.length > 1 ? "s" : ""} from memory
          </span>
        </div>
      )}

      {m.thinking && <ThinkingIndicator phase={m.phase} />}

      {m.clarify ? (
        <ClarifyCard preamble={m.clarify.preamble} questions={m.clarify.questions} />
      ) : (
        m.text && (
          <Answer
            text={m.text}
            sources={m.sources}
            warnings={m.warnings}
            streaming={!m.done}
            done={m.done}
          />
        )
      )}

      {m.done && !m.clarify && <MessageFooter m={m} />}
    </motion.div>
  );
}

function MessageFooter({ m }: { m: UIMessage }) {
  const setSourcesDrawer = useUI((u) => u.setSourcesDrawer);
  const setSources = useChat.setState;
  const [copied, setCopied] = useState(false);
  const [feedback, setFeedback] = useState<"up" | "down" | null>(null);
  const verified = m.warnings.length === 0;
  const usable = m.sources.filter((s) => s && (s.judgment_id || s.case_title));

  function copy() {
    void navigator.clipboard.writeText(m.text || "");
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  }

  const iconBtn =
    "inline-flex h-7 w-[30px] items-center justify-center rounded-lg border border-divider text-ink-2 hover:bg-canvas";

  return (
    <div className="flex flex-wrap items-center gap-1.5 border-t border-divider pt-2.5">
      <span
        className={cn(
          "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11.5px] font-bold",
          verified ? "bg-good-bg text-good" : "bg-warn-bg text-warn",
        )}
      >
        {verified ? <Check size={12} weight="bold" /> : <Warning size={12} weight="bold" />}
        {verified ? "All citations verified" : `${m.warnings.length} to review`}
      </span>

      <button className={iconBtn} aria-label={copied ? "Copied" : "Copy answer"} onClick={copy}>
        {copied ? <Check size={14} weight="bold" /> : <Copy size={14} />}
      </button>
      <button
        className={cn(iconBtn, feedback === "up" && "bg-accent-soft text-navy")}
        aria-label="Good response"
        onClick={() => setFeedback((f) => (f === "up" ? null : "up"))}
      >
        <ThumbsUp size={14} />
      </button>
      <button
        className={cn(iconBtn, feedback === "down" && "bg-accent-soft text-navy")}
        aria-label="Poor response"
        onClick={() => setFeedback((f) => (f === "down" ? null : "down"))}
      >
        <ThumbsDown size={14} />
      </button>

      {usable.length > 0 && (
        <button
          aria-label="View sources"
          onClick={() => {
            setSources({ sources: usable });
            setSourcesDrawer(true);
          }}
          className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-divider px-2.5 py-1.5 text-[11.5px] font-bold text-ink-2 hover:bg-canvas"
        >
          Sources
          <CaretRight size={12} weight="bold" />
        </button>
      )}
    </div>
  );
}

// Memoized so streaming (only the last message changes identity) doesn't
// re-render the whole thread on every token.
export default memo(Message);
