import { motion } from "framer-motion";
import { Feather, RotateCcw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, isMock } from "../api/client";
import { resetMockData } from "../api/mock/server";
import type { Book, Storyline } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { BookCover } from "../components/BookCover";
import { useIngestion } from "../lib/ingestion";
import { Skeleton } from "../components/Loaders";
import { StorylineCard, useFork } from "../components/StorylineCard";
import { Badge, Button, Empty, Page } from "../components/ui";

export default function Shelf() {
  const { user } = useAuth();
  const { fork, overlay } = useFork();
  const [lines, setLines] = useState<Storyline[] | null>(null);
  const [uploads, setUploads] = useState<Book[]>([]);
  const [books, setBooks] = useState<Book[]>([]);
  const { progressOf } = useIngestion();

  useEffect(() => {
    api.listMyStorylines().then(setLines);
    api.listMyBooks().then(setUploads);
    api.listPublicBooks().then(setBooks);
  }, []);
  const titles = useMemo(() => Object.fromEntries([...books, ...uploads].map((b) => [b.id, b.title])), [books, uploads]);
  const published = lines?.filter((s) => s.visibility === "public").length ?? 0;
  const stars = lines?.reduce((n, s) => n + s.stars, 0) ?? 0;

  return (
    <Page>
      {overlay}
      <header className="mb-10 flex flex-wrap items-end gap-6">
        <div className="grid size-16 place-items-center rounded-2xl border border-gold/40 bg-gold-soft font-display text-3xl text-gold">{user!.name.charAt(0)}</div>
        <div className="flex-1">
          <h1 className="font-display text-4xl font-semibold">{user!.name}'s shelf</h1>
          <p className="text-sm text-ink-mute">{user!.email}</p>
        </div>
        <dl className="flex gap-8 text-sm">
          {[["storylines", lines?.length ?? 0], ["published", published], ["stars", stars], ["uploads", uploads.length]].map(([k, v]) => (
            <div key={k}>
              <dt className="text-xs uppercase tracking-wider text-ink-mute">{k}</dt>
              <dd className="font-display text-3xl text-gold">{v}</dd>
            </div>
          ))}
        </dl>
      </header>

      <div className="flourish mb-6 font-display text-2xl">Your storylines</div>
      {!lines ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{Array.from({ length: 3 }, (_, i) => <Skeleton key={i} className="h-44" />)}</div>
      ) : lines.length === 0 ? (
        <Empty icon={<Feather className="size-12" />} title="Your quill is dry">
          Open any book and type <i className="text-gold">“What if…”</i>. <Link to="/library" className="text-gold hover:underline">Browse the library →</Link>
        </Empty>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {lines.map((s, i) => <StorylineCard key={s.id} s={s} index={i} bookTitle={titles[s.book_id]} onFork={fork} />)}
        </div>
      )}

      {uploads.length > 0 && (
        <>
          <div className="flourish mb-6 mt-16 font-display text-2xl">Your uploads</div>
          <div className="flex flex-wrap gap-6">
            {uploads.map((b) => {
              const live = progressOf(b.id);
              const status = live?.status ?? b.status;
              const working = status !== "ready" && status !== "failed";
              const hint = status === "failed" ? (live?.detail ?? b.error ?? undefined) : live?.detail;
              return (
                <motion.div key={b.id} whileHover={{ y: -4 }}>
                  <Link to={`/books/${b.id}`} className="block">
                    <BookCover book={b} size="sm" />
                    <div className="mt-2 flex gap-1" title={hint}>
                      <Badge tone={status === "ready" ? "sage" : status === "failed" ? "rose" : "amber"}>
                        {status}
                        {working && live && live.progress > 0 && ` ${Math.round(live.progress * 100)}%`}
                      </Badge>
                      <Badge tone="mute">{b.visibility}</Badge>
                    </div>
                  </Link>
                </motion.div>
              );
            })}
          </div>
        </>
      )}

      {isMock && (
        <div className="mt-20 flex items-center justify-between gap-4 rounded-xl border border-line p-4 text-sm text-ink-mute">
          <span>Running on in-browser demo data.</span>
          <Button variant="danger" onClick={() => { if (confirm("Erase all demo accounts, uploads and storylines?")) { resetMockData(); location.href = "/"; } }}>
            <RotateCcw className="size-4" /> Reset demo data
          </Button>
        </div>
      )}
    </Page>
  );
}
