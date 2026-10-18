// In-browser fake backend. State lives in localStorage so it survives reloads.
// Mirrors the README's storage model: canon is immutable, storylines are an op log
// materialized over canon, checkpoints are (name, op_seq), forks copy ops.

import type { Api, ChatEvent } from "../client";
import {
  ACCEPT_PROPOSAL,
  type Book,
  type BookStatus,
  type ChatSummary,
  type ChatTurn,
  type Checkpoint,
  type IngestionProgress,
  type Message,
  type Proposal,
  type StoryDiff,
  type StoryGraph,
  type Storyline,
  type User,
} from "../types";
import { CATALOG, buildGraph, quotesOf } from "./catalog";
import { answerGlobal, answerLocal, applyEdit, classify, extractFromText, materialize, type Op } from "./engine";

type MockChat = ChatSummary & { user_id: string; book_id: string; messages: Message[] };
/** An edit drafted but not yet accepted: its op is applied only on accept. */
type Draft = { premise: string; op: Op; diff: StoryDiff; citations: Message["citations"] };

interface Db {
  users: (User & { password: string })[];
  sessions: Record<string, string>; // token -> user id
  books: (Book & { ingest_started: number })[];
  uploadGraphs: Record<string, StoryGraph>;
  storylines: Storyline[];
  ops: Record<string, Op[]>; // storyline id -> op log
  checkpoints: Checkpoint[];
  chats: MockChat[];
  drafts: Record<string, Draft>; // storyline id -> the edit waiting for approval
  stars: Record<string, string[]>; // user id -> storyline ids
}

const KEY = "whatif.mock.v1";
const TOKEN_KEY = "whatif.token";
const now = () => new Date().toISOString();
const id = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
const latency = () => wait(250 + Math.random() * 350);

let db: Db = load();

function load(): Db {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return { chats: [], drafts: {}, ...JSON.parse(raw) }; // stores saved before these had none
  } catch {
    /* fall through to a fresh seed */
  }
  return seed();
}

function save() {
  try {
    localStorage.setItem(KEY, JSON.stringify(db));
  } catch {
    /* storage full or blocked: keep running in memory */
  }
}

function seed(): Db {
  const fresh: Db = { users: [], sessions: {}, books: [], uploadGraphs: {}, storylines: [], ops: {}, checkpoints: [], chats: [], drafts: {}, stars: {} };
  const created = "2026-09-01T10:00:00.000Z";
  for (const [key, entry] of Object.entries(CATALOG)) {
    const g = buildGraph(entry);
    fresh.books.push({
      ...entry.book,
      id: key,
      owner_id: null,
      visibility: "public",
      status: "ready",
      error: null,
      stats: { ...entry.book.stats, entities: g.entities.length, events: g.events.length },
      created_at: created,
      ingest_started: 0,
    });
  }
  // Community storylines so Explore isn't empty on first run.
  const authors = [
    { id: "u-moira", name: "Moira Quill" },
    { id: "u-tobias", name: "Tobias Inkwell" },
    { id: "u-sena", name: "Sena Lantern" },
  ];
  const seeds: [string, number, string, string, number][] = [
    ["oz", 0, "The Lion Who Was Never Afraid", "What if the Lion never needed courage and befriended Glinda early?", 41],
    ["oz", 1, "Toto Takes the Throne", "What if Toto refused to expose the Wizard and ruled Emerald City instead?", 27],
    ["oz", 2, "A Dragon Named Ember", "What if Dorothy met a dragon named Ember on the yellow brick road?", 63],
    ["pride", 0, "First Impressions, Second Chances", "What if Darcy never slighted Elizabeth at the assembly?", 52],
    ["alice", 1, "The Cat Who Stayed", "What if the Cheshire Cat befriended Alice and never vanished?", 19],
    ["frankenstein", 2, "The Kind Creator", "What if Victor never abandoned the Creature?", 34],
    ["treasure", 0, "Silver's Last Honest Voyage", "What if Silver never betrayed Jim and helped him find Ben Gunn?", 22],
  ];
  seeds.forEach(([bookId, a, title, premise, stars], i) => {
    const sid = `seed${i}`;
    const s: Storyline = {
      id: sid, book_id: bookId, owner_id: authors[a].id, owner_name: authors[a].name, title, premise,
      visibility: "public", parent_storyline_id: null, fork_op_seq: null, head_op_seq: 0,
      forks: Math.floor(stars / 4), stars, created_at: created, updated_at: created,
    };
    const res = applyEdit(buildGraph(CATALOG[bookId]), premise, sid, 1);
    if (res.ok && res.diff) {
      fresh.ops[sid] = [{ seq: 1, reverted: false, diff: res.diff, passage: res.passage! }];
      s.head_op_seq = 1;
    }
    fresh.storylines.push(s);
  });
  return fresh;
}

// --- helpers -------------------------------------------------------------

function currentUser(): User | null {
  const token = localStorage.getItem(TOKEN_KEY);
  const uid = token ? db.sessions[token] : undefined;
  const u = db.users.find((x) => x.id === uid);
  if (!u) return null;
  const { password: _pw, ...user } = u;
  return user;
}

function requireUser(): User {
  const u = currentUser();
  if (!u) throw new Error("Please sign in first.");
  return u;
}

async function hash(s: string) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function session(u: Db["users"][number]) {
  const token = id() + id();
  db.sessions[token] = u.id;
  localStorage.setItem(TOKEN_KEY, token); // the mock has no cookies: it keeps its fake session here
  save();
  const { password: _pw, ...user } = u;
  return { token, user };
}

// Simulated ingestion: status is derived from time since upload, so reloads resume.
const STAGES: [BookStatus, number, (p: number, b: Book) => string][] = [
  ["queued", 1200, () => "Waiting for a free scribe…"],
  ["parsing", 2200, (p) => `Detecting chapters and page spans (${Math.round(p * 100)}%)`],
  ["extracting", 4500, (p) => `Extracting entities from chunk ${Math.max(1, Math.ceil(p * 12))} of 12`],
  ["merging", 3200, (p) => `Merging graphs, level ${Math.max(1, Math.ceil(p * 4))} of 4`],
  ["indexing", 1800, () => "Building vector indexes and community summaries"],
];

function progressOf(b: Db["books"][number]): IngestionProgress {
  if (b.status === "ready" || b.status === "failed") return { book_id: b.id, status: b.status, progress: 1, detail: b.status === "ready" ? "Ready to explore" : b.error ?? "Failed" };
  let t = Date.now() - b.ingest_started;
  for (const [status, ms, detail] of STAGES) {
    if (t < ms) return { book_id: b.id, status, progress: t / ms, detail: detail(t / ms, b) };
    t -= ms;
  }
  b.status = "ready";
  save();
  return { book_id: b.id, status: "ready", progress: 1, detail: "Ready to explore" };
}

function publicBook(b: Db["books"][number]): Book {
  const { ingest_started: _s, ...book } = b;
  return { ...book, status: progressOf(b).status, stats: { ...book.stats, storylines: db.storylines.filter((s) => s.book_id === b.id && s.visibility === "public").length } };
}

function findBook(bookId: string) {
  const b = db.books.find((x) => x.id === bookId);
  if (!b) throw new Error("Book not found.");
  const u = currentUser();
  if (b.visibility !== "public" && b.owner_id !== u?.id) throw new Error("This book is private.");
  return b;
}

function findStoryline(sid: string) {
  const s = db.storylines.find((x) => x.id === sid);
  if (!s) throw new Error("Storyline not found.");
  const u = currentUser();
  if (s.visibility !== "public" && s.owner_id !== u?.id) throw new Error("This storyline is private.");
  return s;
}

function canonGraph(bookId: string): StoryGraph {
  return CATALOG[bookId] ? buildGraph(CATALOG[bookId]) : structuredClone(db.uploadGraphs[bookId]);
}

function graphOf(bookId: string, sid: string | null): StoryGraph {
  const canon = canonGraph(bookId);
  if (!sid) return canon;
  const s = findStoryline(sid);
  return materialize(canon, db.ops[sid] ?? [], s.head_op_seq);
}

const quotes = (bookId: string) => (CATALOG[bookId] ? quotesOf(CATALOG[bookId]) : {});

/** Chat `chatId`, or a new one (stored once its first turn is) when it is null. */
function openChat(uid: string, bookId: string, sid: string | null, chatId: string | null): MockChat {
  if (chatId === null) return { id: "c-" + id(), user_id: uid, book_id: bookId, storyline_id: sid, title: null, created_at: now(), last_active: now(), messages: [] };
  const c = db.chats.find((x) => x.id === chatId && x.user_id === uid && x.book_id === bookId && x.storyline_id === sid);
  if (!c) throw new Error("Chat not found.");
  return c;
}

function summary({ user_id: _u, book_id: _b, messages: _m, ...chat }: MockChat): ChatSummary {
  return chat;
}

async function streamText(text: string, onEvent: (e: ChatEvent) => void) {
  const parts = text.match(/\S+\s*/g) ?? [];
  for (const p of parts) {
    onEvent({ type: "token", text: p });
    await wait(12 + Math.random() * 22);
  }
}

// --- API -----------------------------------------------------------------

const impl: Api = {
  async signUp(name, email, password) {
    await latency();
    email = email.trim().toLowerCase();
    if (!name.trim()) throw new Error("Tell us what to call you.");
    if (!/^\S+@\S+\.\S+$/.test(email)) throw new Error("That email doesn't look right.");
    if (password.length < 8) throw new Error("Use at least 8 characters for your password.");
    if (db.users.some((u) => u.email === email)) throw new Error("An account with that email already exists.");
    const u = { id: "u-" + id(), name: name.trim(), email, created_at: now(), password: await hash(password) };
    db.users.push(u);
    return session(u);
  },

  async signIn(email, password) {
    await latency();
    const u = db.users.find((x) => x.email === email.trim().toLowerCase());
    if (!u || u.password !== (await hash(password))) throw new Error("Email or password is incorrect.");
    return session(u);
  },

  async refresh() {
    const u = db.users.find((x) => x.id === db.sessions[localStorage.getItem(TOKEN_KEY) ?? ""]);
    if (!u) throw new Error("sign in required");
    return session(u);
  },

  async signOut() {
    localStorage.removeItem(TOKEN_KEY);
  },

  async me() {
    const u = currentUser();
    if (!u) throw new Error("Session expired.");
    return u;
  },

  async setInkittToken(token) {
    await latency();
    const u = db.users.find((x) => x.id === requireUser().id)!;
    if (!/^ink_\w{16,}$/.test(token)) throw new Error("Inkitt did not accept that token. Check it and try again.");
    u.inkitt_connected = true;
    save();
    return currentUser()!;
  },

  async clearInkittToken() {
    const u = db.users.find((x) => x.id === requireUser().id)!;
    u.inkitt_connected = false;
    save();
    return currentUser()!;
  },

  async listPublicBooks() {
    await latency();
    return db.books.filter((b) => b.visibility === "public" && b.status !== "failed").map(publicBook);
  },

  async listMyBooks() {
    await latency();
    const u = requireUser();
    return db.books.filter((b) => b.owner_id === u.id).map(publicBook);
  },

  async getBook(bookId) {
    await latency();
    return publicBook(findBook(bookId));
  },

  async uploadBook(file, meta) {
    const u = requireUser();
    if (file.size > 20 * 1024 * 1024) throw new Error("Books up to 20 MB, please.");
    const isText = /\.(txt|md)$/i.test(file.name) || file.type.startsWith("text/");
    // Only plain text is extracted in the mock; other formats get the Oz demo graph.
    const g = isText ? extractFromText(await file.text()) : buildGraph(CATALOG.oz);
    if (g.entities.length < 2) throw new Error("Couldn't find enough named characters in that text to build a graph.");
    await latency();
    const book: Db["books"][number] = {
      id: "b-" + id(),
      title: meta.title.trim() || file.name.replace(/\.[^.]+$/, ""),
      author: meta.author.trim() || null,
      owner_id: u.id,
      visibility: meta.visibility,
      status: "queued",
      error: null,
      blurb: isText ? `Uploaded by ${u.name}. ${g.entities.length} characters found.` : `Uploaded by ${u.name}. (Demo mode: non-text files use a sample graph.)`,
      genre: "Upload",
      cover_hue: Math.floor(Math.random() * 360),
      stats: { pages: Math.max(1, Math.round(file.size / 2200)), entities: g.entities.length, events: g.events.length },
      created_at: now(),
      ingest_started: Date.now(),
    };
    db.books.push(book);
    db.uploadGraphs[book.id] = g;
    save();
    return publicBook(book);
  },

  watchIngestion(bookId, onProgress) {
    let stop = false;
    const tick = () => {
      if (stop) return;
      const b = db.books.find((x) => x.id === bookId);
      if (!b) return;
      const p = progressOf(b);
      onProgress(p);
      if (p.status !== "ready" && p.status !== "failed") setTimeout(tick, 250);
    };
    tick();
    return () => (stop = true);
  },

  async getGraph(bookId, sid) {
    await latency();
    findBook(bookId);
    return graphOf(bookId, sid);
  },

  async listStorylines(bookId) {
    await latency();
    const u = currentUser();
    return db.storylines.filter((s) => s.book_id === bookId && (s.visibility === "public" || s.owner_id === u?.id)).sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  },

  async listPublicStorylines() {
    await latency();
    return db.storylines.filter((s) => s.visibility === "public").sort((a, b) => b.stars - a.stars);
  },

  async listMyStorylines() {
    await latency();
    const u = requireUser();
    return db.storylines.filter((s) => s.owner_id === u.id).sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  },

  async getStoryline(sid) {
    await latency();
    return findStoryline(sid);
  },

  async createStoryline(bookId, title, premise) {
    await latency();
    const u = requireUser();
    findBook(bookId);
    const s: Storyline = {
      id: "s-" + id(), book_id: bookId, owner_id: u.id, owner_name: u.name, title: title.trim() || "Untitled storyline", premise,
      visibility: "private", parent_storyline_id: null, fork_op_seq: null, head_op_seq: 0, forks: 0, stars: 0, created_at: now(), updated_at: now(),
    };
    db.storylines.push(s);
    db.ops[s.id] = [];
    save();
    return s;
  },

  async forkStoryline(sid) {
    await wait(900); // long enough for the fork animation to play
    const u = requireUser();
    const parent = findStoryline(sid);
    const s: Storyline = {
      ...parent, id: "s-" + id(), owner_id: u.id, owner_name: u.name, title: `${parent.title} (fork)`, visibility: "private",
      parent_storyline_id: parent.id, fork_op_seq: parent.head_op_seq, forks: 0, stars: 0, created_at: now(), updated_at: now(),
    };
    // Copy applied ops up to the fork point under the new sid.
    db.ops[s.id] = structuredClone((db.ops[sid] ?? []).filter((o) => !o.reverted && o.seq <= parent.head_op_seq));
    parent.forks += 1;
    db.storylines.push(s);
    save();
    return s;
  },

  async updateStoryline(sid, patch) {
    await latency();
    const u = requireUser();
    const s = findStoryline(sid);
    if (s.owner_id !== u.id) throw new Error("Only the owner can change this storyline.");
    Object.assign(s, patch, { updated_at: now() });
    save();
    return s;
  },

  async starStoryline(sid) {
    await wait(120);
    const u = requireUser();
    const s = findStoryline(sid);
    const mine = (db.stars[u.id] ??= []);
    const i = mine.indexOf(sid);
    if (i >= 0) (mine.splice(i, 1), (s.stars -= 1));
    else (mine.push(sid), (s.stars += 1));
    save();
    return s;
  },

  async listCheckpoints(sid) {
    await latency();
    return db.checkpoints.filter((c) => c.storyline_id === sid).sort((a, b) => b.created_at.localeCompare(a.created_at));
  },

  async saveCheckpoint(sid, name) {
    await latency();
    const s = findStoryline(sid);
    const c: Checkpoint = { id: "c-" + id(), storyline_id: sid, name: name.trim() || `Checkpoint at op ${s.head_op_seq}`, op_seq: s.head_op_seq, auto: false, created_at: now() };
    db.checkpoints.push(c);
    save();
    return c;
  },

  async restoreCheckpoint(sid, cid) {
    await wait(700);
    const u = requireUser();
    const s = findStoryline(sid);
    if (s.owner_id !== u.id) throw new Error("Fork this storyline to change it.");
    const c = db.checkpoints.find((x) => x.id === cid);
    if (!c) throw new Error("Checkpoint not found.");
    // Auto-save the head so the restore itself can be undone.
    db.checkpoints.push({ id: "c-" + id(), storyline_id: sid, name: `Before restoring “${c.name}”`, op_seq: s.head_op_seq, auto: true, created_at: now() });
    for (const op of db.ops[sid] ?? []) op.reverted = op.seq > c.op_seq;
    s.head_op_seq = c.op_seq;
    s.updated_at = now();
    save();
    return { storyline: s, graph: graphOf(s.book_id, sid) };
  },

  async listChats(bookId, sid) {
    await latency();
    const u = currentUser();
    if (!u) return [];
    return db.chats
      .filter((c) => c.user_id === u.id && c.book_id === bookId && c.storyline_id === sid)
      .sort((a, b) => b.last_active.localeCompare(a.last_active))
      .map(summary);
  },

  async history(bookId, sid, chatId) {
    await latency();
    return withProposal(bookId, sid, openChat(requireUser().id, bookId, sid, chatId).messages);
  },

  async send(bookId, sid, chatId, text, onEvent) {
    const u = requireUser();
    findBook(bookId);
    const chat = openChat(u.id, bookId, sid, chatId);
    let storyline = sid ? findStoryline(sid) : null;
    const route = classify(text, !!storyline);
    const userMsg: Message = { id: "m-" + id(), role: "user", content: text, created_at: now() };
    const waiting = storyline ? db.drafts[storyline.id] : undefined;

    if (storyline && waiting && text === ACCEPT_PROPOSAL) {
      onEvent({ type: "phase", label: "Updating the story…" });
      await wait(700);
      onEvent({ type: "phase", label: "Writing the changed chapters…" });
      await wait(800);
      // Applying a new op discards any reverted tail (it can no longer be redone).
      db.ops[storyline.id] = [...(db.ops[storyline.id] ?? []).filter((o) => !o.reverted), waiting.op];
      storyline.head_op_seq = waiting.op.seq;
      storyline.updated_at = now();
      delete db.drafts[storyline.id];
      const content = `Done: your storyline now follows “${waiting.premise}”. The story map shows what changed.`;
      await streamText(content, onEvent);
      const assistant: Message = { id: "m-" + id(), role: "assistant", content, route: "edit", diff: waiting.diff, citations: waiting.citations, created_at: now() };
      return persist(chat, userMsg, assistant, graphOf(bookId, storyline.id), storyline);
    }

    if (storyline && waiting && route !== "edit") {
      // Discussing the proposal: the demo can't redraft, but the proposal stays open.
      onEvent({ type: "phase", label: "Reconsidering the draft…" });
      await wait(700);
      const content = `(Demo) Noted: “${text}”. The real assistant would revise the draft with that and show you the new version. Press Accept when you're happy with it.`;
      await streamText(content, onEvent);
      const assistant: Message = { id: "m-" + id(), role: "assistant", content, route: "edit", created_at: now() };
      return persist(chat, userMsg, assistant, graphOf(bookId, storyline.id), storyline);
    }

    if (/\binkitt\b/i.test(text)) {
      onEvent({ type: "phase", label: "Checking your Inkitt connection…" });
      await wait(500);
      const content = u.inkitt_connected
        ? storyline
          ? `(Demo) I would send “${storyline.title}” to Inkitt now: the original chapters up to where it diverges, then your new ones, all as drafts. Nothing goes live until you say so.`
          : "(Demo) I would send this book to Inkitt now, one draft chapter per book chapter. Nothing goes live until you say so."
        : "To publish on Inkitt I need your Inkitt access token. Paste it in the window that just opened, and I'll carry on.";
      await streamText(content, onEvent);
      const assistant: Message = { id: "m-" + id(), role: "assistant", content, created_at: now() };
      const turn = persist(chat, userMsg, assistant, graphOf(bookId, storyline?.id ?? null), storyline);
      return u.inkitt_connected ? turn : { ...turn, actions: ["inkitt_token_required"] };
    }

    if (route === "edit") {
      onEvent({ type: "phase", label: "Parsing your what-if into ops…" });
      await wait(700);
      if (storyline && storyline.owner_id !== u.id) throw new Error("This storyline isn't yours. Fork it to make changes.");
      onEvent({ type: "phase", label: "Finding the divergence point…" });
      await wait(650);
      const opSeq = (storyline?.head_op_seq ?? 0) + 1;
      const tmpSid = storyline?.id ?? "s-" + id();
      const res = applyEdit(graphOf(bookId, storyline?.id ?? null), text, tmpSid, opSeq);
      if (!res.ok) {
        onEvent({ type: "phase", label: "Checking consistency…" });
        await wait(400);
        const assistant: Message = { id: "m-" + id(), role: "assistant", content: "", route, trace: res.trace, created_at: now() };
        await streamText(res.content, onEvent);
        assistant.content = res.content;
        return persist(chat, userMsg, assistant, graphOf(bookId, storyline?.id ?? null), storyline);
      }
      if (!storyline) {
        // An edit from canon branches a new storyline.
        const title = text.replace(/^(what if|suppose|imagine)\s+/i, "").replace(/[?.!]+$/, "");
        storyline = {
          id: tmpSid, book_id: bookId, owner_id: u.id, owner_name: u.name, title: title.charAt(0).toUpperCase() + title.slice(1, 60), premise: text,
          visibility: "private", parent_storyline_id: null, fork_op_seq: null, head_op_seq: 0, forks: 0, stars: 0, created_at: now(), updated_at: now(),
        };
        db.storylines.push(storyline);
        db.ops[tmpSid] = [];
      }
      onEvent({ type: "phase", label: "Checking the story still holds together…" });
      await wait(800);
      // The edit waits as a draft (replacing any earlier one) until the reader accepts it.
      const d = res.diff!;
      db.drafts[storyline.id] = {
        premise: text,
        op: { seq: opSeq, reverted: false, diff: d, passage: res.passage! },
        diff: {
          divergence_pos: res.divergence_pos!, op_seq: opSeq, passage: res.passage!,
          added: [...d.add_entities.map((e) => e.key), ...d.add_events.map((e) => e.key)],
          hidden: d.hide, modified: d.modify.map((m) => m.key), added_edges: d.add_relations.map((r) => r.id),
        },
        citations: [{ chapter: d.add_events[0].chapter, page: null, quote: res.passage!.split(". ")[0] + ".", storyline_chunk: true }],
      };
      const content = `${res.content}\n\nHere's the change on the story map. Accept it to update the storyline, or tell me what to change.`;
      const assistant: Message = { id: "m-" + id(), role: "assistant", content: "", route, trace: res.trace, created_at: now() };
      await streamText(content, onEvent);
      assistant.content = content;
      return persist(chat, userMsg, assistant, graphOf(bookId, storyline.id), storyline, sid === null);
    }

    onEvent({ type: "phase", label: route === "global" ? "Reading community summaries…" : "Searching the index…" });
    await wait(600);
    onEvent({ type: "phase", label: route === "global" ? "Weighing the big picture…" : "Walking the graph…" });
    await wait(650);
    const g = route === "canon" ? canonGraph(bookId) : graphOf(bookId, storyline?.id ?? null);
    const ans = route === "global" ? answerGlobal(g, text) : answerLocal(g, text, quotes(bookId), route === "canon");
    onEvent({ type: "phase", label: "Checking every claim against the text…" });
    await wait(400);
    await streamText(ans.content, onEvent);
    const assistant: Message = { id: "m-" + id(), role: "assistant", content: ans.content, route, citations: ans.citations, trace: ans.trace, created_at: now() };
    return persist(chat, userMsg, assistant, graphOf(bookId, storyline?.id ?? null), storyline);
  },
};

/** Clone every resolved value, as a network hop would, so callers never share the store's objects. */
export const mockApi: Api = Object.fromEntries(
  Object.entries(impl).map(([name, fn]) => [
    name,
    (...args: unknown[]) => {
      const out = (fn as (...a: unknown[]) => unknown)(...args);
      return out instanceof Promise ? out.then((v) => structuredClone(v)) : out;
    },
  ]),
) as unknown as Api;

function persist(chat: MockChat, user: Message, assistant: Message, graph: StoryGraph, storyline: Storyline | null, branched = false): ChatTurn {
  // As on the server, a canon chat's first edit moves the chat into the storyline it branched.
  if (branched) chat.storyline_id = storyline!.id;
  chat.title ??= user.content.slice(0, 80);
  chat.last_active = now();
  chat.messages.push(user, assistant);
  if (!db.chats.includes(chat)) db.chats.push(chat);
  save();
  const [answer] = withProposal(chat.book_id, chat.storyline_id, [assistant]);
  return { user, assistant: answer, graph, storyline, chat: summary(chat) };
}

/** As on the server, the waiting draft rides on the latest answer (it is not stored there). */
function withProposal(bookId: string, sid: string | null, messages: Message[]): Message[] {
  const draft = sid ? db.drafts[sid] : undefined;
  const last = messages.map((m) => m.role).lastIndexOf("assistant");
  if (!sid || !draft || last < 0) return messages;
  const canon = canonGraph(bookId);
  const s = findStoryline(sid);
  const ops = [...(db.ops[sid] ?? []).filter((o) => o.seq <= s.head_op_seq), draft.op];
  const proposal: Proposal = { storyline_id: sid, premise: draft.premise, diff: draft.diff, graph: materialize(canon, ops, draft.op.seq) };
  return messages.map((m, i) => (i === last ? { ...m, proposal } : m));
}

/** Wipes demo data (exposed for the profile page). */
export function resetMockData() {
  localStorage.removeItem(KEY);
  localStorage.removeItem(TOKEN_KEY);
  db = seed();
  save();
}
