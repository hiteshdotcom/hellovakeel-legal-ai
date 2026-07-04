import { create } from "zustand";
import { api } from "@/lib/api";
import type { SessionRow, Source, Statute, Warning } from "@/lib/types";

export interface UIMessage {
  role: "user" | "assistant";
  text: string;
  recalled: string[];
  thinking: boolean;
  done: boolean;
  sources: Source[];
  warnings: Warning[];
  verified: string[];
}

const rid = () => "s_" + Math.random().toString(36).slice(2, 12);

interface ChatState {
  sessions: SessionRow[];
  sessionId: string | null;
  messages: UIMessage[];
  sources: Source[];
  statutes: Statute[];
  warnings: Warning[];
  streaming: boolean;
  recallPulse: boolean;

  loadSessions: (userId: string) => Promise<void>;
  newSession: () => void;
  openSession: (userId: string, id: string) => Promise<void>;
  send: (userId: string, text: string) => Promise<void>;
  renameSession: (id: string, title: string) => void;
  deleteSession: (id: string) => void;
  resetForLogout: () => void;
}

export const useChat = create<ChatState>((set, get) => ({
  sessions: [],
  sessionId: null,
  messages: [],
  sources: [],
  statutes: [],
  warnings: [],
  streaming: false,
  recallPulse: false,

  loadSessions: async (userId) => {
    try {
      const res = await api.getSessions(userId);
      set({ sessions: res.sessions || [] });
    } catch {
      set({ sessions: [] });
    }
  },

  newSession: () =>
    set({ sessionId: null, messages: [], sources: [], statutes: [], warnings: [] }),

  openSession: async (userId, id) => {
    set({ sessionId: id, sources: [], statutes: [], warnings: [] });
    try {
      const data = await api.getSessionMessages(userId, id);
      const messages: UIMessage[] = (data.messages || [])
        .filter((m) => m.role !== "recall")
        .map((m) => ({
          role: m.role as "user" | "assistant",
          text: m.content,
          recalled: [],
          thinking: false,
          done: true,
          sources: m.sources || [],
          warnings: m.warnings || [],
          verified: [],
        }));
      const lastWarn = [...messages].reverse().find((m) => m.warnings.length);
      set({
        messages,
        sources: data.sources || [],
        warnings: lastWarn ? lastWarn.warnings : [],
      });
    } catch {
      set({ messages: [] });
    }
  },

  send: async (userId, raw) => {
    const text = raw.trim();
    if (!text || get().streaming) return;
    let sessionId = get().sessionId;
    if (!sessionId) {
      sessionId = rid();
      set({ sessionId });
    }

    const user: UIMessage = {
      role: "user",
      text,
      recalled: [],
      thinking: false,
      done: true,
      sources: [],
      warnings: [],
      verified: [],
    };
    const asst: UIMessage = {
      role: "assistant",
      text: "",
      recalled: [],
      thinking: true,
      done: false,
      sources: [],
      warnings: [],
      verified: [],
    };
    set({ messages: [...get().messages, user, asst], streaming: true });

    // Mutate the trailing assistant message in place, then publish a fresh array.
    const patchAsst = (fn: (m: UIMessage) => void) => {
      const messages = get().messages.slice();
      const last = messages[messages.length - 1];
      if (last && last.role === "assistant") {
        const copy = { ...last };
        fn(copy);
        messages[messages.length - 1] = copy;
        set({ messages });
      }
    };

    try {
      await api.streamChat({ user_id: userId, session_id: sessionId, message: text }, (ev) => {
        if (ev.type === "meta") {
          set({ sessionId: ev.session_id });
          patchAsst((m) => {
            m.recalled = ev.recalled || [];
            m.thinking = true;
          });
        } else if (ev.type === "sources") {
          set({ sources: ev.sources || [], statutes: ev.statutes || [] });
        } else if (ev.type === "token") {
          patchAsst((m) => {
            m.thinking = false;
            m.text += ev.text;
          });
        } else if (ev.type === "final") {
          patchAsst((m) => {
            m.text = ev.answer;
            m.sources = get().sources;
            m.warnings = ev.warnings || [];
            m.verified = ev.verified || [];
          });
          set({ warnings: ev.warnings || [] });
          const msgs = get().messages;
          if (msgs[msgs.length - 1]?.recalled.length) {
            set({ recallPulse: true });
            setTimeout(() => set({ recallPulse: false }), 1800);
          }
        }
      });
    } catch (e) {
      patchAsst((m) => {
        m.text += `\n\n[connection error: ${e instanceof Error ? e.message : String(e)}]`;
      });
    }

    patchAsst((m) => {
      m.thinking = false;
      m.done = true;
    });
    set({ streaming: false });
    void get().loadSessions(userId);
  },

  renameSession: (id, title) =>
    set((s) => ({
      sessions: s.sessions.map((x) => (x.id === id ? { ...x, title } : x)),
    })),

  deleteSession: (id) =>
    set((s) => {
      const sessions = s.sessions.filter((x) => x.id !== id);
      if (s.sessionId === id)
        return { sessions, sessionId: null, messages: [], sources: [], warnings: [] };
      return { sessions };
    }),

  resetForLogout: () =>
    set({
      sessions: [],
      sessionId: null,
      messages: [],
      sources: [],
      statutes: [],
      warnings: [],
      streaming: false,
    }),
}));
