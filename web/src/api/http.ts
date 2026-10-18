// HTTP implementation of `Api`, the contract the FastAPI routes should satisfy.
// Chat and ingestion progress stream as Server-Sent Events. `VITE_API_URL` is the server.

import { getAccessToken, notifySessionLost, setAccessToken } from "../auth/token";
import type { Api, ChatEvent } from "./client";
import type { ChatTurn, IngestionProgress, Session } from "./types";

// Default to the page's own hostname, so the refresh cookie (SameSite=Lax) is sent whether the app
// is opened as localhost or 127.0.0.1.
const BASE = (import.meta.env.VITE_API_URL ?? `http://${window.location.hostname}:8000`).replace(/\/$/, "");

const send = (path: string, init: RequestInit = {}) => {
  const headers = new Headers(init.headers);
  const token = getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  // `include` sends and accepts the httpOnly refresh cookie on this cross-origin API.
  return fetch(BASE + path, { ...init, headers, credentials: "include" });
};

let refreshing: Promise<Session> | null = null;

/** Trades the refresh cookie for a new session and keeps its access token. Concurrent callers
 *  (React StrictMode, parallel requests) share one request: refresh tokens are single use, so a
 *  second request with the same cookie would fail and clear the new one. */
function refreshSession(): Promise<Session> {
  refreshing ??= (async () => {
    try {
      const res = await fetch(BASE + "/auth/refresh", { method: "POST", credentials: "include" });
      if (!res.ok) throw await errorFrom(res);
      const session: Session = await res.json();
      setAccessToken(session.token);
      return session;
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

/** `fetch` with the access token. On a 401 the session is renewed and the request retried once;
 *  if that fails the user is signed out. */
async function authFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const res = await send(path, init);
  if (res.status !== 401) return res;
  if (!(await refreshSession().then(() => true, () => false))) {
    setAccessToken(null);
    notifySessionLost();
    return res;
  }
  return send(path, init);
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const res = await authFetch(path, { ...init, headers });
  if (!res.ok) throw await errorFrom(res);
  return res.json();
}

/** A call made without an access token (sign-in, sign-up): the response sets the refresh cookie. */
async function open<T>(path: string, init: RequestInit): Promise<T> {
  const headers = new Headers({ "Content-Type": "application/json" });
  const res = await fetch(BASE + path, { ...init, headers, credentials: "include" });
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
  signUp: (name, email, password) => open("/auth/signup", json("POST", { name, email, password })),
  signIn: (email, password) => open("/auth/signin", json("POST", { email, password })),
  refresh: refreshSession,
  signOut: async () => {
    await fetch(BASE + "/auth/logout", { method: "POST", credentials: "include" }).catch(() => {});
  },
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
  // `EventSource` can't send an Authorization header, so the stream is read with fetch instead.
  watchIngestion: (bookId, onProgress) => {
    const stop = new AbortController();
    (async () => {
      const res = await authFetch(`/books/${bookId}/ingestion/events`, { signal: stop.signal });
      if (!res.ok) return;
      await readSse(res, (_event, data) => {
        const p: IngestionProgress = JSON.parse(data);
        onProgress(p);
        if (p.status === "ready" || p.status === "failed") stop.abort();
      });
    })().catch(() => {}); // aborted, or the connection dropped
    return () => stop.abort();
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

  listChats: (bookId, sid) => req(`/books/${bookId}/chats${sid ? `?storyline_id=${sid}` : ""}`),
  history: (bookId, sid, chatId) => req(`/books/${bookId}/messages?chat_id=${chatId}${sid ? `&storyline_id=${sid}` : ""}`),
  send: async (bookId, sid, chatId, text, onEvent) => {
    const res = await authFetch(`/books/${bookId}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ storyline_id: sid, chat_id: chatId, new_chat: chatId === null, text }),
    });
    if (!res.ok) throw await errorFrom(res);
    let turn: ChatTurn | null = null;
    await readSse(res, (event, data) => {
      if (event === "phase" || event === "token") onEvent({ type: event, ...JSON.parse(data) } as ChatEvent);
      else if (event === "step") onEvent({ type: "step", step: JSON.parse(data) });
      else if (event === "reasoning") onEvent({ type: "reasoning", reasoning: JSON.parse(data) });
      else if (event === "done") turn = JSON.parse(data);
      else if (event === "error") throw new Error(JSON.parse(data).detail);
    });
    if (!turn) throw new Error("chat stream ended without a result");
    return turn;
  },
};
