// ============================================================
// API shapes — mirror the FastAPI backend (app/auth.py, app/api/chat.py).
// ============================================================

export interface User {
  id: string;
  email: string;
  name: string;
  avatar_url?: string | null;
  provider: string;
}

export interface Providers {
  password: boolean;
  google: boolean;
  clerk: boolean;
  clerk_publishable_key: string;
  clerk_frontend_api: string;
}

/** A retrieved judgment, shaped by `_source_from_meta` on the backend. */
export interface Source {
  judgment_id: string;
  case_title: string;
  citation?: string | null;
  neutral_citation?: string | null;
  court?: string;
  courtdate?: string;
  judgment_date?: string | null;
  ratio_decidendi?: string;
  ratio?: string;
  headnotes?: string;
  still_good_law?: boolean;
  good?: boolean;
}

export interface Statute {
  [key: string]: unknown;
  title?: string;
  section?: string;
  act?: string;
  citation?: string;
  text?: string;
}

export type WarningKind = "unverified_citation" | "overruled" | string;

export interface Warning {
  kind: WarningKind;
  text?: string;
  detail?: string;
  title?: string;
}

export interface MemoryView {
  sub?: string;
  tokens?: number;
  empty: boolean;
  groups: unknown[];
}

// ---- streaming chat events (NDJSON) ----
export interface MetaEvent {
  type: "meta";
  session_id: string;
  recalled: string[];
}
export interface SourcesEvent {
  type: "sources";
  sources: Source[];
  statutes?: Statute[];
  retrieval_ms?: number;
}
export interface TokenEvent {
  type: "token";
  text: string;
}
export interface FinalEvent {
  type: "final";
  answer: string;
  sources: string[];
  warnings: Warning[];
  verified?: string[];
  memory?: MemoryView;
  total_ms?: number;
}
export interface DoneEvent {
  type: "done";
}
/** Judgment-scoped Q&A emits a `source` event first. */
export interface SourceEvent {
  type: "source";
  source: Source;
}
export type ChatEvent =
  | MetaEvent
  | SourcesEvent
  | TokenEvent
  | FinalEvent
  | DoneEvent
  | SourceEvent;

// ---- sessions ----
export interface SessionRow {
  id: string;
  title?: string;
  topic?: string;
  preview?: string;
  last_message?: string;
  last_active_at?: string | null;
}
export interface SessionsResponse {
  user_id: string;
  sessions: SessionRow[];
}
export interface StoredMessage {
  role: "user" | "assistant" | "recall";
  content: string;
  sources?: Source[];
  warnings?: Warning[];
}
export interface SessionMessagesResponse {
  session_id: string;
  messages: StoredMessage[];
  sources: Source[];
}

// ---- judgment detail ----
export interface JudgmentPage {
  page_number: number;
  text: string;
}
export interface Citation {
  id?: string | number;
  citing_id?: string;
  cited_id?: string | null;
  cited_citation?: string;
  citation_text?: string;
  cited_id_title?: string;
  case_title?: string;
  context?: string;
  citation_type?: string;
  good_law?: boolean | null;
  applicability_score?: number;
  treatment_chain?: Array<{ code?: string; label?: string; ref?: string }>;
  ratio_decidendi?: string;
  [key: string]: unknown;
}
export interface TopTerm {
  term: string;
  count: number;
}
export interface JudgmentAnalytics {
  pages: number;
  characters: number;
  words: number;
  citations_total: number;
  citations_outgoing: number;
  citations_incoming: number;
  citations_external: number;
  has_ratio: boolean;
  has_headnotes: boolean;
  ratio_still_good_law: boolean;
  overruled_by?: string | null;
  top_terms: TopTerm[];
}
/** The backend `metadata` blob is rich and loosely-structured. */
export type JudgmentMeta = Record<string, unknown>;

export interface JudgmentDetail {
  judgment_id: string;
  source: Source;
  metadata: JudgmentMeta;
  pages: JudgmentPage[];
  citations: Citation[];
  analytics: JudgmentAnalytics;
  page_count: number;
  text_preview: string;
  full_text: string;
  text_chars: number;
  error?: string;
}

// ---- citation graph ----
export type GraphNodeKind = "act" | "good" | "over" | "fact" | "you";
export interface GraphNode {
  id: string;
  label: string;
  sub?: string;
  kind: GraphNodeKind;
}
export interface GraphEdge {
  src: string;
  dst: string;
}
export interface CorpusGraph {
  judgment_id?: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}
