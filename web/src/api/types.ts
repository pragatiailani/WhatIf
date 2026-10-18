// Wire types for the WhatIf? API. Field names mirror app/repo/models.py so the FastAPI
// routes can return repo records as-is.

export type BookStatus =
  | "queued"
  | "parsing"
  | "extracting"
  | "merging"
  | "indexing"
  | "ready"
  | "failed";

export type Visibility = "public" | "private";

export interface User {
  id: string;
  name: string;
  email: string;
  created_at: string;
  /** an Inkitt access token is saved for this user */
  inkitt_connected?: boolean;
}

export interface Session {
  token: string;
  user: User;
}

export interface Book {
  id: string;
  title: string;
  author: string | null;
  owner_id: string | null; // null for catalog books
  visibility: Visibility;
  status: BookStatus;
  error: string | null;
  blurb?: string;
  genre?: string;
  cover_hue: number; // drives the generated cover art
  stats: { chapters?: number; pages?: number; chunks?: number; entities?: number; events?: number; relations?: number; storylines?: number };
  created_at: string;
}

export interface IngestionProgress {
  book_id: string;
  status: BookStatus;
  /** 0..1 within the current stage */
  progress: number;
  detail: string;
}

// --- Graph ---------------------------------------------------------------

export interface GraphEntity {
  key: string;
  name: string;
  type: string; // person, place, object, group
  aliases: string[];
  description: string;
  first_page: number | null;
  last_page: number | null;
  mention_count: number;
  community_id: number | null;
  sid: string | null;
}

export interface GraphEvent {
  key: string;
  summary: string;
  story_pos: number;
  chapter: string | null;
  first_page: number | null;
  sid: string | null;
  involves: string[]; // entity keys (INVOLVES edges)
}

export interface GraphRelation {
  id: string;
  src: string;
  dst: string;
  rel: string;
  description: string;
  weight: number;
  sid: string | null;
}

export interface Community {
  cid: number;
  title: string;
  summary: string;
}

export interface StoryGraph {
  entities: GraphEntity[];
  events: GraphEvent[];
  relations: GraphRelation[];
  communities: Community[];
  /** node keys hidden in this storyline (stale or removed) */
  hidden: string[];
}

// --- Storylines ----------------------------------------------------------

export interface Storyline {
  id: string;
  book_id: string;
  owner_id: string;
  owner_name: string;
  title: string;
  premise: string; // the first "what if"
  visibility: Visibility;
  parent_storyline_id: string | null;
  fork_op_seq: number | null;
  head_op_seq: number;
  forks: number;
  stars: number;
  created_at: string;
  updated_at: string;
}

// --- Chat ----------------------------------------------------------------

export type Route = "local" | "global" | "canon" | "edit";

export interface Citation {
  chapter: string | null;
  page: number | null;
  quote: string;
  /** set for generated storyline passages, which cite a storyline chunk not a page */
  storyline_chunk?: boolean;
}

/** Why the bot answered what it answered: the retrieval path through the graph. */
export interface RetrievalTrace {
  rewritten_question: string;
  seeds: string[]; // matched by vector search
  expanded: string[]; // reached by k-hop expansion
  edges: string[]; // relation ids walked
  hops: number;
  communities: number[]; // global search
  steps: TraceStep[];
  /** why the answer holds: the graph facts it rests on, in order; null when nothing backs it */
  reasoning?: Reasoning | null;
}

/** One fact in an answer's reasoning, as stored in the graph. */
export type ReasoningLink =
  | { kind: "relation"; id: string; src_key: string; dst_key: string; src: string; dst: string; rel: string; description: string; why: string }
  | { kind: "event"; key: string; summary: string; chapter: string | null; page: number | null; involves: string[]; names: string[]; why: string };

export interface Reasoning {
  chain: ReasoningLink[];
  conclusion: string;
}

/** One thing the agent did on the way to an answer. */
export interface TraceStep {
  label: string;
  detail: string; // the tool's arguments
  tool?: string;
  why?: string; // what the agent was looking for, in its words
  found?: string; // what came back
}

/** What a storyline edit changed. */
export interface StoryDiff {
  divergence_pos: number;
  added: string[];
  hidden: string[];
  modified: string[];
  added_edges: string[];
  passage: string;
  op_seq: number;
}

/** A verified storyline edit waiting for the reader: nothing changes until they accept it. */
export interface Proposal {
  storyline_id: string;
  premise: string;
  /** what accepting would change */
  diff: StoryDiff;
  /** the storyline's graph if accepted */
  graph: StoryGraph;
}

/** What the Accept button sends; the agent commits the proposal when it reads it. */
export const ACCEPT_PROPOSAL = "Accept these changes.";

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  route?: Route;
  citations?: Citation[];
  trace?: RetrievalTrace;
  /** what this turn changed in the storyline (set on the turn that applied an edit) */
  diff?: StoryDiff;
  /** on the latest answer while an edit waits for the reader to accept or discuss it */
  proposal?: Proposal;
  created_at: string;
}

/** One of the user's conversations on a book (canon) or in a storyline. */
export interface ChatSummary {
  id: string;
  storyline_id: string | null;
  /** the first question, once one is asked */
  title: string | null;
  created_at: string;
  last_active: string;
}

export interface ChatTurn {
  user: Message;
  assistant: Message;
  /** the chat the turn was saved in; a new one when none was given */
  chat: ChatSummary;
  /** graph after the turn; sent only when the turn changed it (an accepted storyline edit) */
  graph?: StoryGraph;
  /** null when chatting with canon; a new storyline when an edit is made from canon */
  storyline: Storyline | null;
  /** things the UI should do after the turn, e.g. ask for the user's Inkitt token */
  actions?: ChatAction[];
}

export type ChatAction = "inkitt_token_required";
