import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Brain, Search, Shield, type IconType } from "@/lib/icons";
import { SkeletonLines } from "@/components/ui";

type Phase = "recall" | "retrieve" | "compose";

const HEAD: Record<Phase, { icon: IconType; text: string }> = {
  recall: { icon: Brain, text: "Hang tight — recalling your matter…" },
  retrieve: { icon: Search, text: "Searching 505 judgments & Central Acts…" },
  compose: { icon: Shield, text: "Grounding every citation, then composing…" },
};

const REASSURANCE = [
  "I verify every citation before it reaches you.",
  "Answers come only from real judgments — nothing invented.",
  "This usually takes a few seconds.",
];

export default function ThinkingIndicator({ phase }: { phase: Phase }) {
  const head = HEAD[phase] ?? HEAD.recall;
  const Icon = head.icon;
  const [tip, setTip] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setTip((t) => (t + 1) % REASSURANCE.length), 2600);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="flex flex-col gap-3">
      <AnimatePresence mode="wait">
        <motion.div
          key={phase}
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          transition={{ duration: 0.2 }}
          className="flex items-center gap-2.5 text-[13.5px] font-medium text-ink-2"
        >
          <Icon size={15} className="text-accent-ink" />
          {head.text}
          <span className="inline-flex gap-1">
            {[0, 0.2, 0.4].map((d) => (
              <span
                key={d}
                className="h-1 w-1 rounded-full bg-ink-3"
                style={{ animation: `1.2s ${d}s infinite dot` }}
              />
            ))}
          </span>
        </motion.div>
      </AnimatePresence>

      <AnimatePresence mode="wait">
        <motion.div
          key={tip}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.3 }}
          className="text-[12px] text-ink-3"
        >
          {REASSURANCE[tip]}
        </motion.div>
      </AnimatePresence>

      <div className="mt-0.5">
        <SkeletonLines />
      </div>
    </div>
  );
}
