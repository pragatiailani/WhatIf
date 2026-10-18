import { motion } from "framer-motion";
import { ArrowLeft, MessageCircleQuestion, Sparkles, Wand2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Book, StoryGraph, Storyline } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { BookCover } from "../components/BookCover";
import { GraphView } from "../components/GraphView";
import { PageLoader } from "../components/Loaders";
import { StorylineCard } from "../components/StorylineCard";
import { Badge, Button, Empty, Page } from "../components/ui";
import { errorText } from "../lib/ui";
import { IngestionStages } from "./Upload";

export default function BookPage() {
  const { bookId = "" } = useParams();
  const { user } = useAuth();
  const nav = useNavigate();
  const [book, setBook] = useState<Book | null>(null);
  const [graph, setGraph] = useState<StoryGraph | null>(null);
  const [lines, setLines] = useState<Storyline[]>([]);
  const [error, setError] = useState("");

  const load = () => {
    api.getBook(bookId).then((b) => {
      setBook(b);
      if (b.status === "ready") api.getGraph(bookId, null).then(setGraph);
    }).catch((e) => setError(errorText(e)));
    api.listStorylines(bookId).then(setLines).catch(() => {});
  };
  useEffect(load, [bookId]);

  if (error) return <Page><Empty icon={<Sparkles className="size-12" />} title="This book slipped off the shelf">{error}</Empty></Page>;
  if (!book) return <PageLoader label="Fetching the book…" />;

  const begin = (path: string) => (user ? nav(path) : nav(`/signin?next=${encodeURIComponent(path)}`));

  return (
    <Page>
      <Link to="/library" className="mb-6 inline-flex items-center gap-1.5 text-sm text-ink-mute hover:text-gold">
        <ArrowLeft className="size-4" /> Library
      </Link>

      <section className="grid gap-10 md:grid-cols-[auto_1fr]">
        <motion.div initial={{ opacity: 0, rotateY: -25 }} animate={{ opacity: 1, rotateY: 0 }} transition={{ type: "spring", stiffness: 80 }} style={{ transformPerspective: 900 }} className="mx-auto md:mx-0">
          <BookCover book={book} size="lg" />
        </motion.div>
        <div>
          <div className="mb-3 flex flex-wrap gap-1.5">
            {book.genre && <Badge tone="mute">{book.genre}</Badge>}
            {book.visibility === "private" && <Badge tone="mute">private upload</Badge>}
            {book.status !== "ready" && <Badge tone="amber">{book.status}</Badge>}
          </div>
          <h1 className="font-display text-5xl font-semibold leading-tight">{book.title}</h1>
          {book.author && <p className="mt-1 font-display text-xl italic text-ink-soft">{book.author}</p>}
          {book.blurb && <p className="mt-5 max-w-2xl font-serif text-lg leading-relaxed text-ink-soft">{book.blurb}</p>}
          <dl className="mt-6 flex flex-wrap gap-x-8 gap-y-3 text-sm">
            {[["chapters", book.stats.chapters], ["pages", book.stats.pages], ["characters & places", book.stats.entities], ["events", book.stats.events]].filter(([, v]) => v).map(([k, v]) => (
              <div key={k as string}>
                <dt className="text-xs uppercase tracking-wider text-ink-mute">{k}</dt>
                <dd className="font-display text-2xl text-gold">{v}</dd>
              </div>
            ))}
          </dl>
          {book.status === "ready" ? (
            <div className="mt-8 flex flex-wrap gap-3">
              <Button onClick={() => begin(`/read/${book.id}`)} className="px-5 py-3">
                <MessageCircleQuestion className="size-4" /> Ask or rewrite with “What if…”
              </Button>
            </div>
          ) : (
            <div className="card mt-8 p-5">
              <IngestionStages bookId={book.id} onReady={load} />
            </div>
          )}
        </div>
      </section>

      {graph && (
        <section className="mt-16">
          <div className="flourish mb-6 font-display text-2xl">Story map</div>
          <div className="card h-[420px] overflow-hidden">
            <GraphView graph={graph} overlay={null} compact />
          </div>
        </section>
      )}

      <section className="mt-16">
        <div className="flourish mb-6 font-display text-2xl">Storylines</div>
        {lines.length === 0 ? (
          <Empty icon={<Wand2 className="size-12" />} title="No what-ifs yet">Be the first to bend this story.</Empty>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {lines.map((s, i) => (
              <StorylineCard key={s.id} s={s} index={i} onChange={(n) => setLines((ls) => ls.map((x) => (x.id === n.id ? n : x)))} />
            ))}
          </div>
        )}
      </section>

    </Page>
  );
}
