import { motion } from "framer-motion";
import { BookPlus, Search, SearchX } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Book, IngestionProgress } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { TiltCover } from "../components/BookCover";
import { Skeleton } from "../components/Loaders";
import { Badge, Empty, Page } from "../components/ui";
import { errorText, useToast } from "../lib/ui";

export default function Library() {
  const { user } = useAuth();
  const toast = useToast();
  const [tab, setTab] = useState<"public" | "mine">("public");
  const [books, setBooks] = useState<Book[] | null>(null);
  const [q, setQ] = useState("");
  const [genre, setGenre] = useState("All");

  useEffect(() => {
    setBooks(null);
    (tab === "public" ? api.listPublicBooks() : api.listMyBooks()).then(setBooks).catch((e) => (toast(errorText(e), "error"), setBooks([])));
  }, [tab, toast]);

  const genres = useMemo(() => ["All", ...new Set((books ?? []).flatMap((b) => (b.genre ? [b.genre] : [])))], [books]);
  const shown = (books ?? []).filter(
    (b) => (genre === "All" || b.genre === genre) && (b.title + " " + (b.author ?? "")).toLowerCase().includes(q.toLowerCase()),
  );

  return (
    <Page>
      <header className="mb-8 flex flex-col gap-6 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="font-display text-5xl font-semibold">The Library</h1>
          <p className="mt-2 text-ink-soft">Pick a book. Ask it anything. Then change it.</p>
        </div>
        <div className="relative w-full md:w-80">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ink-mute" />
          <input className="input pl-10" placeholder="Search titles or authors" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </header>

      <div className="mb-6 flex flex-wrap items-center gap-2">
        {user && (
          <div className="mr-3 flex rounded-xl border border-line bg-surface p-1">
            {(["public", "mine"] as const).map((t) => (
              <button key={t} onClick={() => setTab(t)} className={`relative rounded-lg px-4 py-1.5 text-sm transition ${tab === t ? "text-[#1b1508]" : "text-ink-soft hover:text-ink"}`}>
                {tab === t && <motion.span layoutId="lib-tab" className="absolute inset-0 rounded-lg bg-gold" transition={{ type: "spring", stiffness: 400, damping: 32 }} />}
                <span className="relative">{t === "public" ? "Public library" : "My uploads"}</span>
              </button>
            ))}
          </div>
        )}
        {genres.length > 2 &&
          genres.map((g) => (
            <button key={g} onClick={() => setGenre(g)} className={`rounded-full border px-3 py-1 text-xs transition ${genre === g ? "border-gold/50 bg-gold-soft text-gold" : "border-line text-ink-soft hover:border-gold/30"}`}>
              {g}
            </button>
          ))}
      </div>

      {!books ? (
        <div className="grid grid-cols-2 gap-6 sm:grid-cols-3 lg:grid-cols-5">
          {Array.from({ length: 5 }, (_, i) => (
            <div key={i} className="space-y-3">
              <Skeleton className="aspect-[2/3]" />
              <Skeleton className="h-4 w-3/4" />
            </div>
          ))}
        </div>
      ) : shown.length === 0 ? (
        !q ? (
          <Empty icon={<BookPlus className="size-12" />} title={tab === "public" ? "The shelves are empty" : "No uploads yet"}>
            Bring your own book and we'll map it into a story graph. <Link to="/upload" className="text-gold hover:underline">Upload one →</Link>
          </Empty>
        ) : (
          <Empty icon={<SearchX className="size-12" />} title="Nothing on these shelves">No books match “{q}”.</Empty>
        )
      ) : (
        <div className="grid grid-cols-2 gap-x-6 gap-y-10 sm:grid-cols-3 lg:grid-cols-5">
          {shown.map((b, i) => (
            <motion.div key={b.id} initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }}>
              <Link to={`/books/${b.id}`} className="group block">
                <TiltCover book={b} />
                <div className="mt-3">
                  <h3 className="font-display text-lg font-semibold leading-tight group-hover:text-gold">{b.title}</h3>
                  <p className="text-sm text-ink-mute">{b.author ?? "Unknown author"}</p>
                  {b.status !== "ready" ? <IngestBadge book={b} /> : <BookStats book={b} />}
                </div>
              </Link>
            </motion.div>
          ))}
        </div>
      )}
    </Page>
  );
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function BookStats({ book: { stats } }: { book: Book }) {
  const parts = [
    stats.pages && plural(stats.pages, "page"),
    stats.entities && plural(stats.entities, "entity", "entities"),
    stats.events && plural(stats.events, "event"),
    stats.storylines && plural(stats.storylines, "storyline"),
  ].filter(Boolean);
  return parts.length ? <p className="mt-1 text-xs text-sage">{parts.join(" · ")}</p> : null;
}

function IngestBadge({ book }: { book: Book }) {
  const [p, setP] = useState<IngestionProgress | null>(null);
  useEffect(() => api.watchIngestion(book.id, setP), [book.id]);
  if (p?.status === "ready") return <div className="mt-1"><Badge tone="sage">ready</Badge></div>;
  if (p?.status === "failed" || (!p && book.status === "failed"))
    return <div className="mt-1" title={p?.detail ?? book.error ?? undefined}><Badge tone="rose">failed</Badge></div>;
  return (
    <div className="mt-2 space-y-1">
      <Badge tone="amber">{p?.status ?? book.status}…</Badge>
      <div className="h-1 overflow-hidden rounded-full bg-surface-2">
        <motion.div className="h-full bg-amber" animate={{ width: `${Math.round((p?.progress ?? 0) * 100)}%` }} />
      </div>
    </div>
  );
}
