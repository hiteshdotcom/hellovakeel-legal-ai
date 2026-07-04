import { create } from "zustand";
import { api } from "@/lib/api";
import type { CorpusGraph, JudgmentDetail, Source, Warning } from "@/lib/types";

export type JudgmentTab = "overview" | "analysis" | "text" | "citations" | "ask";

interface JudgmentState {
  open: boolean;
  loading: boolean;
  selectedId: string | null;
  pendingSource: Source | null;
  detail: JudgmentDetail | null;
  error: string;
  tab: JudgmentTab;
  graph: CorpusGraph;
  textQuery: string;

  // Ask-this-judgment
  askDraft: string;
  askAnswer: string;
  askWarnings: Warning[];
  asking: boolean;

  openJudgment: (src: Source | string) => Promise<void>;
  close: () => void;
  setTab: (t: JudgmentTab) => void;
  setTextQuery: (q: string) => void;
  setAskDraft: (v: string) => void;
  ask: (userId: string) => Promise<void>;
}

export const useJudgment = create<JudgmentState>((set, get) => ({
  open: false,
  loading: false,
  selectedId: null,
  pendingSource: null,
  detail: null,
  error: "",
  tab: "overview",
  graph: { nodes: [], edges: [] },
  textQuery: "",
  askDraft: "",
  askAnswer: "",
  askWarnings: [],
  asking: false,

  openJudgment: async (srcOrId) => {
    const source = typeof srcOrId === "object" ? srcOrId : null;
    const id = typeof srcOrId === "object" ? srcOrId.judgment_id : srcOrId;
    if (!id) return;
    set({
      open: true,
      loading: true,
      selectedId: id,
      pendingSource: source,
      detail: null,
      error: "",
      tab: "overview",
      textQuery: "",
      askAnswer: "",
      askDraft: "",
      askWarnings: [],
      graph: { nodes: [], edges: [] },
    });
    try {
      const detail = await api.getJudgment(id);
      set({ detail, loading: false, pendingSource: null });
    } catch (e) {
      set({
        loading: false,
        error: `Could not load this judgment. ${e instanceof Error ? e.message : ""}`,
      });
    }
    try {
      const graph = await api.getCorpusGraph(id);
      set({ graph });
    } catch {
      set({ graph: { nodes: [], edges: [] } });
    }
  },

  close: () =>
    set({
      open: false,
      loading: false,
      selectedId: null,
      pendingSource: null,
      detail: null,
      error: "",
      askAnswer: "",
      askWarnings: [],
      graph: { nodes: [], edges: [] },
    }),

  setTab: (tab) => set({ tab }),
  setTextQuery: (textQuery) => set({ textQuery }),
  setAskDraft: (askDraft) => set({ askDraft }),

  ask: async (userId) => {
    const q = get().askDraft.trim();
    const id = get().selectedId;
    if (!q || !id || get().asking) return;
    set({ asking: true, askAnswer: "", askWarnings: [] });
    try {
      await api.streamJudgmentAsk(id, { user_id: userId, message: q }, (ev) => {
        if (ev.type === "token") set({ askAnswer: get().askAnswer + ev.text });
        else if (ev.type === "final") set({ askWarnings: ev.warnings || [] });
      });
    } catch (e) {
      set({
        askAnswer:
          get().askAnswer + `\n\n[connection error: ${e instanceof Error ? e.message : String(e)}]`,
      });
    }
    set({ asking: false });
  },
}));
