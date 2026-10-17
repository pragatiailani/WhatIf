// App-wide watch on the signed-in user's books while they are ingested, so the user can browse
// elsewhere and still hear when a book is ready (or failed).

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { Book, BookStatus, IngestionProgress } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { useToast } from "./ui";

const finished = (s: BookStatus) => s === "ready" || s === "failed";

interface Watched {
  book: Book;
  progress: IngestionProgress | null;
}

interface IngestionState {
  /** Books still being ingested. */
  active: Watched[];
  /** Latest progress seen this session for a watched book. */
  progressOf(bookId: string): IngestionProgress | undefined;
  /** Watch a book until it is ready or failed; a no-op if it is finished or already watched. */
  track(book: Book): void;
}

const Ctx = createContext<IngestionState | null>(null);

export function IngestionProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const toast = useToast();
  const [watched, setWatched] = useState<Record<string, Watched>>({});
  const stops = useRef(new Map<string, () => void>());

  const track = useCallback(
    (book: Book) => {
      if (finished(book.status) || stops.current.has(book.id)) return;
      setWatched((w) => ({ ...w, [book.id]: { book, progress: null } }));
      let done = false;
      const stop = api.watchIngestion(book.id, (p) => {
        setWatched((w) => (w[book.id] ? { ...w, [book.id]: { ...w[book.id], progress: p } } : w));
        if (!finished(p.status) || done) return;
        done = true;
        stops.current.delete(book.id); // the watch closes itself on a final status
        if (p.status === "ready") toast(`“${book.title}” is ready to explore.`, "success");
        else toast(`“${book.title}” failed: ${p.detail}`, "error");
      });
      if (!done) stops.current.set(book.id, stop); // the first update can arrive synchronously
    },
    [toast],
  );

  // Pick up the user's unfinished uploads, e.g. after a reload; drop everything on sign-out.
  useEffect(() => {
    let cancelled = false;
    if (user) api.listMyBooks().then((bs) => !cancelled && bs.forEach(track), () => {});
    const watches = stops.current;
    return () => {
      cancelled = true;
      watches.forEach((stop) => stop());
      watches.clear();
      setWatched({});
    };
  }, [user?.id, track]);

  const value: IngestionState = {
    active: Object.values(watched).filter((w) => !finished(w.progress?.status ?? w.book.status)),
    progressOf: (id) => watched[id]?.progress ?? undefined,
    track,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useIngestion() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useIngestion outside IngestionProvider");
  return v;
}
