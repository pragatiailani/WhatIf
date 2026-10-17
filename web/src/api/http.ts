// HTTP implementation of `Api`, the contract the FastAPI routes should satisfy.
// Chat and ingestion progress stream as Server-Sent Events. `VITE_API_URL` is the server.

import type { Api, ChatEvent } from "./client";
import type { ChatTurn, IngestionProgress } from "./types";

const BASE = (import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const TOKEN_KEY = "whatif.token";

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem(TOKEN_KEY);
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const res = await fetch(BASE + path, { ...init, headers });
  if (!res.ok) throw await errorFrom(res);
  return res.json();
}

/** The server's reason for a failed request. */
async function errorFrom(res: Response): Promise<Error> {
  const body = await res.json().catch(() => ({}));
  // FastAPI sends a string, or a list of field errors for invalid input.
  const detail = Array.isArray(body.detail) ? body.detail.map((d: { msg: string }) => d.msg).join("; ") : body.detail;
  return new Error(detail ?? `${res.status} ${res.statusText}`);
}

const json = (method: string, body?: unknown): RequestInit => ({ method, body: body === undefined ? undefined : JSON.stringify(body) });

/** Reads an SSE response body, calling `onEvent(event, data)` for each message. */
async function readSse(res: Response, onEvent: (event: string, data: string) => void) {
  const reader = res.body!.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value;
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const raw = buf.slice(0, i);
      buf = buf.slice(i + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of raw.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      onEvent(event, data.join("\n"));
    }
  }
}

export const httpApi: Api = {
  signUp: (name, email, password) => req("/auth/signup", json("POST", { name, email, password })),
  signIn: (email, password) => req("/auth/signin", json("POST", { email, password })),
  me: () => req("/auth/me"),
  setInkittToken: (token) => req("/auth/me/inkitt", json("PUT", { token })),
  clearInkittToken: () => req("/auth/me/inkitt", json("DELETE")),

  listPublicBooks: () => req("/books?scope=public"),
  listMyBooks: () => req("/books?scope=mine"),
  getBook: (id) => req(`/books/${id}`),
  uploadBook: (file, meta) => {
    const form = new FormData();
    form.append("file", file);
    Object.entries(meta).forEach(([k, v]) => form.append(k, v));
    return req("/books", { method: "POST", body: form });
  },
  watchIngestion: (bookId, onProgress) => {
    const source = new EventSource(`${BASE}/books/${bookId}/ingestion/events`);
    source.onmessage = (e) => {
      const p: IngestionProgress = JSON.parse(e.data);
      onProgress(p);
      if (p.status === "ready" || p.status === "failed") source.close();
    };
    return () => source.close();
  },

  getGraph: (bookId, sid) => req(`/books/${bookId}/graph${sid ? `?storyline_id=${sid}` : ""}`),

  listStorylines: (bookId) => req(`/books/${bookId}/storylines`),
  listPublicStorylines: () => req("/storylines?scope=public"),
  listMyStorylines: () => req("/storylines?scope=mine"),
  getStoryline: (id) => req(`/storylines/${id}`),
  createStoryline: (bookId, title, premise) => req(`/books/${bookId}/storylines`, json("POST", { title, premise })),
  forkStoryline: (id) => req(`/storylines/${id}/fork`, json("POST")),
  updateStoryline: (id, patch) => req(`/storylines/${id}`, json("PATCH", patch)),
  starStoryline: (id) => req(`/storylines/${id}/star`, json("POST")),

  listCheckpoints: (sid) => req(`/storylines/${sid}/checkpoints`),
  saveCheckpoint: (sid, name) => req(`/storylines/${sid}/checkpoints`, json("POST", { name })),
  restoreCheckpoint: (sid, cid) => req(`/storylines/${sid}/checkpoints/${cid}/restore`, json("POST")),

  history: (bookId, sid) => req(`/books/${bookId}/messages${sid ? `?storyline_id=${sid}` : ""}`),
  send: async (bookId, sid, text, onEvent) => {
    const token = localStorage.getItem(TOKEN_KEY);
    const res = await fetch(`${BASE}/books/${bookId}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify({ storyline_id: sid, text }),
    });
    if (!res.ok) throw await errorFrom(res);
    let turn: ChatTurn | null = null;
    await readSse(res, (event, data) => {
      if (event === "phase" || event === "token") onEvent({ type: event, ...JSON.parse(data) } as ChatEvent);
      else if (event === "done") turn = JSON.parse(data);
      else if (event === "error") throw new Error(JSON.parse(data).detail);
    });
    if (!turn) throw new Error("chat stream ended without a result");
    return turn;
  },
};
