// In-browser fake backend. State lives in localStorage so it survives reloads.
// Mirrors the README's storage model: canon is immutable, storylines are an op log
// materialized over canon, checkpoints are (name, op_seq), forks copy ops.

import type { Api, ChatEvent } from "../client";
import type {
  Book,
  BookStatus,
  ChatTurn,
  Checkpoint,
  IngestionProgress,
  Message,
  StoryGraph,
  Storyline,
  User,
} from "../types";
import { CATALOG, buildGraph, quotesOf } from "./catalog";
import { answerGlobal, answerLocal, applyEdit, classify, extractFromText, materialize, type Op } from "./engine";

interface Db {
  users: (User & { password: string })[];
  sessions: Record<string, string>; // token -> user id
  books: (Book & { ingest_started: number })[];
  uploadGraphs: Record<string, StoryGraph>;
  storylines: Storyline[];
  ops: Record<string, Op[]>; // storyline id -> op log
  checkpoints: Checkpoint[];
  messages: Record<string, Message[]>; // `${userId}|${bookId}|${sid ?? "canon"}`
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
    if (raw) return JSON.parse(raw);
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
  const fresh: Db = { users: [], sessions: {}, books: [], uploadGraphs: {}, storylines: [], ops: {}, checkpoints: [], messages: {}, stars: {} };
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
const msgKey = (uid: string, bookId: string, sid: string | null) => `${uid}|${bookId}|${sid ?? "canon"}`;

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

  async history(bookId, sid) {
    await latency();
    const u = currentUser();
    if (!u) return [];
    return db.messages[msgKey(u.id, bookId, sid)] ?? [];
  },

  async send(bookId, sid, text, onEvent) {
    const u = requireUser();
    findBook(bookId);
    let storyline = sid ? findStoryline(sid) : null;
    const route = classify(text, !!storyline);
    const userMsg: Message = { id: "m-" + id(), role: "user", content: text, created_at: now() };

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
      const turn = persist(u.id, bookId, storyline?.id ?? null, userMsg, assistant, graphOf(bookId, storyline?.id ?? null), storyline);
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
        return persist(u.id, bookId, storyline?.id ?? null, userMsg, assistant, graphOf(bookId, storyline?.id ?? null), storyline);
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
      onEvent({ type: "phase", label: "Writing the new passage…" });
      await wait(800);
      // Applying a new op discards any reverted tail (it can no longer be redone).
      db.ops[storyline.id] = (db.ops[storyline.id] ?? []).filter((o) => !o.reverted);
      db.ops[storyline.id].push({ seq: opSeq, reverted: false, diff: res.diff!, passage: res.passage! });
      storyline.head_op_seq = opSeq;
      storyline.updated_at = now();
      const d = res.diff!;
      const assistant: Message = {
        id: "m-" + id(), role: "assistant", content: "", route, trace: res.trace, created_at: now(),
        diff: {
          divergence_pos: res.divergence_pos!, op_seq: opSeq, passage: res.passage!,
          added: [...d.add_entities.map((e) => e.key), ...d.add_events.map((e) => e.key)],
          hidden: d.hide, modified: d.modify.map((m) => m.key), added_edges: d.add_relations.map((r) => r.id),
        },
        citations: [{ chapter: d.add_events[0].chapter, page: null, quote: res.passage!.split(". ")[0] + ".", storyline_chunk: true }],
      };
      await streamText(res.content, onEvent);
      assistant.content = res.content;
      return persist(u.id, bookId, storyline.id, userMsg, assistant, graphOf(bookId, storyline.id), storyline, sid === null);
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
    return persist(u.id, bookId, storyline?.id ?? null, userMsg, assistant, graphOf(bookId, storyline?.id ?? null), storyline);
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

function persist(uid: string, bookId: string, sid: string | null, user: Message, assistant: Message, graph: StoryGraph, storyline: Storyline | null, branched = false): ChatTurn {
  const k = msgKey(uid, bookId, sid);
  (db.messages[k] ??= []).push(user, assistant);
  if (branched) {
    // Keep the canon conversation too, so the turn shows up where it was asked.
    (db.messages[msgKey(uid, bookId, null)] ??= []).push(user, { ...assistant, content: assistant.content + `\n\nThis branched a new storyline: **${storyline!.title}**.` });
  }
  save();
  return { user, assistant, graph, storyline };
}

/** Wipes demo data (exposed for the profile page). */
export function resetMockData() {
  localStorage.removeItem(KEY);
  localStorage.removeItem(TOKEN_KEY);
  db = seed();
  save();
}
