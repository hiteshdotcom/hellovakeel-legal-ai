// ============================================================
// API client. Same-origin (`/api`) so the HttpOnly session cookie
// is sent automatically — in dev, Vite proxies /api to the FastAPI
// backend (see vite.config.ts).
// ============================================================
import type {
  ChatEvent,
  CorpusGraph,
  JudgmentDetail,
  Providers,
  SessionMessagesResponse,
  SessionsResponse,
  Source,
  User,
} from "./types";

const API = "/api";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function parseError(r: Response): Promise<string> {
  try {
    const body = await r.json();
    if (typeof body.error === "string") return body.error;
    if (Array.isArray(body.detail))
      return body.detail.map((d: { msg?: string }) => d?.msg).filter(Boolean).join(" ");
    if (typeof body.detail === "string") return body.detail;
  } catch {
    /* not json */
  }
  return `HTTP ${r.status}`;
}

async function jget<T>(path: string): Promise<T> {
  const r = await fetch(API + path);
  if (!r.ok) throw new ApiError(await parseError(r), r.status);
  return r.json() as Promise<T>;
}

async function jpost<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(API + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) throw new ApiError(await parseError(r), r.status);
  return r.json() as Promise<T>;
}

/**
 * POST a request and consume the NDJSON stream, invoking `onEvent` for each
 * decoded line. Resolves when the stream ends.
 */
async function streamNDJSON<E>(
  path: string,
  body: unknown,
  onEvent: (event: E) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(API + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok) throw new ApiError(await parseError(resp), resp.status);
  if (!resp.body) throw new ApiError("No response stream", 500);

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nl: number;
    while ((nl = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (!line) continue;
      try {
        onEvent(JSON.parse(line) as E);
      } catch {
        /* ignore malformed line */
      }
    }
  }
}

// -------------------- Auth --------------------
export const api = {
  getProviders: () => jget<Providers>("/auth/providers"),
  getMe: () => jget<{ user: User }>("/auth/me"),
  login: (email: string, password: string) =>
    jpost<{ user: User }>("/auth/login", { email, password }),
  signup: (email: string, password: string, name: string) =>
    jpost<{ user: User }>("/auth/signup", { email, password, name }),
  logout: () => jpost<{ ok: boolean }>("/auth/logout"),
  exchangeClerk: (token: string) => jpost<{ user: User }>("/auth/clerk", { token }),

  // -------------------- Sessions / history --------------------
  getSessions: (userId: string) => jget<SessionsResponse>(`/sessions/${userId}`),
  getSessionMessages: (userId: string, sessionId: string) =>
    jget<SessionMessagesResponse>(`/sessions/${userId}/${sessionId}`),

  // -------------------- Chat (streaming) --------------------
  streamChat: (
    payload: { user_id: string; session_id: string | null; message: string },
    onEvent: (e: ChatEvent) => void,
    signal?: AbortSignal,
  ) => streamNDJSON<ChatEvent>("/chat", payload, onEvent, signal),

  // -------------------- Judgments --------------------
  getJudgment: (id: string) =>
    jget<JudgmentDetail>(`/judgments/${encodeURIComponent(id)}`),
  summariseJudgment: (id: string) =>
    jpost<Record<string, string>>(`/judgments/${encodeURIComponent(id)}/summarise`),
  streamJudgmentAsk: (
    id: string,
    payload: { user_id: string; message: string },
    onEvent: (e: ChatEvent) => void,
    signal?: AbortSignal,
  ) =>
    streamNDJSON<ChatEvent>(
      `/judgments/${encodeURIComponent(id)}/ask`,
      payload,
      onEvent,
      signal,
    ),

  // -------------------- Graph --------------------
  getCorpusGraph: (judgmentId: string) =>
    jget<CorpusGraph>(`/graph-corpus/${encodeURIComponent(judgmentId)}`),

  // Google (server-side redirect flow)
  googleLoginUrl: () => `${API}/auth/google/login`,
};

export type { Source };
