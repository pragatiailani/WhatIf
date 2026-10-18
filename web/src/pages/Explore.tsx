import { motion } from "framer-motion";
import { Compass } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { Book, Storyline } from "../api/types";
import { Skeleton } from "../components/Loaders";
import { StorylineCard } from "../components/StorylineCard";
import { Empty, Page } from "../components/ui";

export default function Explore() {
  const [lines, setLines] = useState<Storyline[] | null>(null);
  const [books, setBooks] = useState<Book[]>([]);
  const [sort, setSort] = useState<"popular" | "new">("popular");
  const [book, setBook] = useState("all");

  useEffect(() => {
    api.listPublicStorylines().then(setLines);
    api.listPublicBooks().then(setBooks);
  }, []);

  const titles = useMemo(() => Object.fromEntries(books.map((b) => [b.id, b.title])), [books]);
  const shown = (lines ?? [])
    .filter((s) => book === "all" || s.book_id === book)
    .sort((a, b) => (sort === "popular" ? b.stars - a.stars : b.created_at.localeCompare(a.created_at)));

  return (
    <Page>
      <header className="mb-8">
        <h1 className="font-display text-5xl font-semibold">Storylines</h1>
        <p className="mt-2 text-ink-soft">Worlds other readers have bent. Read them, star them, and ask them anything.</p>
      </header>
      <div className="mb-6 flex flex-wrap items-center gap-2">
        <div className="flex rounded-xl border border-line bg-surface p-1">
          {(["popular", "new"] as const).map((s) => (
            <button key={s} onClick={() => setSort(s)} className={`relative rounded-lg px-3.5 py-1.5 text-sm capitalize ${sort === s ? "text-[#1b1508]" : "text-ink-soft hover:text-ink"}`}>
              {sort === s && <motion.span layoutId="sort" className="absolute inset-0 rounded-lg bg-gold" />}
              <span className="relative">{s}</span>
            </button>
          ))}
        </div>
        <select value={book} onChange={(e) => setBook(e.target.value)} className="input w-auto py-2 text-sm">
          <option value="all">All books</option>
          {books.map((b) => <option key={b.id} value={b.id}>{b.title}</option>)}
        </select>
      </div>

      {!lines ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-48" />)}</div>
      ) : shown.length === 0 ? (
        <Empty icon={<Compass className="size-12" />} title="Uncharted territory">No public storylines here yet. Publish yours from the workspace.</Empty>
      ) : (
        <motion.div layout className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {shown.map((s, i) => (
            <StorylineCard key={s.id} s={s} index={i} bookTitle={titles[s.book_id]} onChange={(n) => setLines((ls) => ls!.map((x) => (x.id === n.id ? n : x)))} />
          ))}
        </motion.div>
      )}
    </Page>
  );
}
