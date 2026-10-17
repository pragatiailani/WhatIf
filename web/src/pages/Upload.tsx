import { AnimatePresence, motion } from "framer-motion";
import { Check, FileText, Globe, Lock, UploadCloud, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, isMock } from "../api/client";
import type { Book, BookStatus, IngestionProgress, Visibility } from "../api/types";
import { BookCover } from "../components/BookCover";
import { Button, Page } from "../components/ui";
import { useIngestion } from "../lib/ingestion";
import { errorText, useToast } from "../lib/ui";

const ACCEPT = ".pdf";

export default function Upload() {
  const toast = useToast();
  const nav = useNavigate();
  const { track } = useIngestion();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [over, setOver] = useState(false);
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [visibility, setVisibility] = useState<Visibility>("public");
  const [busy, setBusy] = useState(false);
  const [book, setBook] = useState<Book | null>(null);

  const pick = (f: File | undefined) => {
    if (!f) return;
    if (!f.name.toLowerCase().endsWith(".pdf")) return toast("Only PDF files are supported for now.", "error");
    setFile(f);
    if (!title) setTitle(f.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " "));
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    pick(e.dataTransfer.files[0]);
  };

  const submit = async () => {
    if (!file) return;
    setBusy(true);
    try {
      const uploaded = await api.uploadBook(file, { title, author, visibility });
      track(uploaded); // the app-wide watch toasts when it is ready, wherever the user is
      setBook(uploaded);
    } catch (e) {
      toast(errorText(e), "error");
    } finally {
      setBusy(false);
    }
  };

  if (book) {
    return (
      <Page className="max-w-3xl">
        <div className="card p-8">
          <div className="flex flex-col items-center gap-6 sm:flex-row sm:items-start">
            <BookCover book={book} size="sm" />
            <div className="flex-1">
              <h1 className="font-display text-3xl font-semibold">{book.title}</h1>
              <p className="mt-1 text-sm text-ink-soft">The scribes are mapping your book into a story graph. You can leave this page; we'll keep working.</p>
            </div>
          </div>
          <div className="mt-8">
            <IngestionStages bookId={book.id} onOpen={() => nav(`/books/${book.id}`)} />
          </div>
        </div>
      </Page>
    );
  }

  return (
    <Page className="max-w-3xl">
      <h1 className="font-display text-5xl font-semibold">Bring a book</h1>
      <p className="mt-2 text-ink-soft">We'll split it into chunks, extract every character, place and event, and weave them into a graph you can question and rewrite.</p>

      <motion.div
        onDragOver={(e) => (e.preventDefault(), setOver(true))}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
        onClick={() => input.current?.click()}
        animate={{ scale: over ? 1.02 : 1, borderColor: over ? "var(--gold)" : "var(--line)" }}
        className="relative mt-8 flex cursor-pointer flex-col items-center justify-center gap-3 overflow-hidden rounded-2xl border-2 border-dashed bg-surface px-6 py-14 text-center"
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === "Enter" && input.current?.click()}
      >
        {over && <motion.div layoutId="glow" className="absolute inset-0 bg-[radial-gradient(circle,var(--gold-soft),transparent_70%)]" />}
        <input ref={input} type="file" accept={ACCEPT} className="hidden" onChange={(e) => pick(e.target.files?.[0])} />
        <AnimatePresence mode="wait">
          {file ? (
            <motion.div key="file" initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} className="relative flex items-center gap-3 rounded-xl border border-line bg-bg-2 px-4 py-3">
              <FileText className="size-6 text-gold" />
              <div className="text-left">
                <p className="text-sm font-medium">{file.name}</p>
                <p className="text-xs text-ink-mute">{(file.size / 1024).toFixed(0)} KB</p>
              </div>
              <button onClick={(e) => (e.stopPropagation(), setFile(null))} className="ml-2 text-ink-mute hover:text-rose" aria-label="Remove file">
                <X className="size-4" />
              </button>
            </motion.div>
          ) : (
            <motion.div key="empty" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="relative flex flex-col items-center gap-3">
              <motion.div animate={{ y: over ? -8 : [0, -6, 0] }} transition={over ? {} : { duration: 2.5, repeat: Infinity }}>
                <UploadCloud className="size-12 text-gold" />
              </motion.div>
              <p className="font-display text-xl">Drop your manuscript here</p>
              <p className="text-sm text-ink-mute">PDF only, up to 20 MB</p>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
      {isMock && <p className="mt-2 text-xs text-ink-mute">Demo mode gives uploads a sample graph. Set VITE_API_MODE=http to ingest with the real backend.</p>}

      <div className="mt-8 grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-ink-mute">Title</span>
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="The Book of Lost Things" />
        </label>
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-ink-mute">Author</span>
          <input className="input" value={author} onChange={(e) => setAuthor(e.target.value)} placeholder="Optional" />
        </label>
      </div>

      <fieldset className="mt-6">
        <legend className="mb-2 text-xs font-medium uppercase tracking-wider text-ink-mute">Who can see it</legend>
        <div className="grid gap-3 sm:grid-cols-2">
          {([["public", Globe, "Public library", "Anyone can read it and write storylines."], ["private", Lock, "Only me", "Your upload and its storylines stay private."]] as const).map(([v, I, label, hint]) => (
            <button key={v} type="button" onClick={() => setVisibility(v)} className={`flex items-start gap-3 rounded-xl border p-4 text-left transition ${visibility === v ? "border-gold/60 bg-gold-soft" : "border-line hover:border-gold/30"}`}>
              <I className={`mt-0.5 size-4 ${visibility === v ? "text-gold" : "text-ink-mute"}`} />
              <span>
                <span className="block text-sm font-medium">{label}</span>
                <span className="block text-xs text-ink-mute">{hint}</span>
              </span>
            </button>
          ))}
        </div>
      </fieldset>

      <Button onClick={submit} disabled={!file} loading={busy} className="mt-8 w-full py-3">
        <UploadCloud className="size-4" /> Begin mapping
      </Button>
    </Page>
  );
}

const STAGES: { status: BookStatus; label: string }[] = [
  { status: "queued", label: "Queued" },
  { status: "parsing", label: "Parsing" },
  { status: "extracting", label: "Extracting" },
  { status: "merging", label: "Merging" },
  { status: "indexing", label: "Indexing" },
  { status: "ready", label: "Ready" },
];

/** Live ingestion progress: stepper plus a constellation that forms as the graph is built. */
export function IngestionStages({ bookId, onReady, onOpen }: { bookId: string; onReady?: () => void; onOpen?: () => void }) {
  const [p, setP] = useState<IngestionProgress | null>(null);
  const readyFired = useRef(false);
  useEffect(
    () =>
      api.watchIngestion(bookId, (next) => {
        setP(next);
        if (next.status === "ready" && !readyFired.current) {
          readyFired.current = true;
          onReady?.();
        }
      }),
    [bookId],
  );
  const idx = STAGES.findIndex((s) => s.status === p?.status);
  const failed = p?.status === "failed";

  return (
    <div>
      <ol className="flex items-center">
        {STAGES.map((s, i) => {
          const done = idx > i || p?.status === "ready";
          const active = idx === i && !done;
          return (
            <li key={s.status} className="flex flex-1 items-center last:flex-none">
              <div className="flex flex-col items-center gap-1.5">
                <motion.div
                  className={`grid size-8 place-items-center rounded-full border text-xs ${done ? "border-sage bg-sage/15 text-sage" : active ? "border-gold text-gold" : "border-line text-ink-mute"}`}
                  animate={active ? { boxShadow: ["0 0 0 0 var(--gold-soft)", "0 0 0 8px transparent"] } : {}}
                  transition={{ duration: 1.4, repeat: Infinity }}
                >
                  {done ? <Check className="size-4" /> : i + 1}
                </motion.div>
                <span className={`hidden text-[11px] sm:block ${active ? "text-gold" : "text-ink-mute"}`}>{s.label}</span>
              </div>
              {i < STAGES.length - 1 && (
                <div className="mx-1 mb-5 h-px flex-1 bg-line sm:mb-6">
                  <motion.div className="h-full bg-sage" animate={{ width: done ? "100%" : active ? `${Math.round((p?.progress ?? 0) * 100)}%` : "0%" }} />
                </div>
              )}
            </li>
          );
        })}
      </ol>

      <div className="mt-6 grid items-center gap-6 sm:grid-cols-[200px_1fr]">
        <Forming status={p?.status ?? "queued"} progress={p?.progress ?? 0} />
        <div>
          <AnimatePresence mode="wait">
            <motion.p key={p?.detail} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} className={`font-display text-xl italic ${failed ? "text-rose" : "text-ink-soft"}`}>
              {p?.detail ?? "Connecting…"}
            </motion.p>
          </AnimatePresence>
          {p?.status === "ready" && onOpen && (
            <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
              <Button onClick={onOpen} className="mt-4">Open the book</Button>
            </motion.div>
          )}
        </div>
      </div>
    </div>
  );
}

function Forming({ status, progress }: { status: BookStatus; progress: number }) {
  // Hand-placed so the finished constellation reads as a loose, winding path.
  const stars = useMemo(
    () => [[18, 92], [42, 60], [70, 78], [88, 40], [112, 22], [128, 52], [150, 34], [176, 58], [160, 92], [132, 108], [104, 96], [82, 118]].map(([x, y]) => ({ x, y })),
    [],
  );
  const order = STAGES.findIndex((s) => s.status === status);
  const shown = order < 2 ? 0 : order === 2 ? Math.ceil(progress * stars.length) : stars.length;
  const linked = order < 3 ? 0 : order === 3 ? Math.ceil(progress * (stars.length - 1)) : stars.length - 1;
  const ready = status === "ready";
  return (
    <svg viewBox="0 0 200 136" className="w-full" aria-hidden>
      {stars.slice(0, linked + 1).map((s, i) =>
        i === 0 ? null : (
          <motion.line key={`l${i}`} x1={stars[i - 1].x} y1={stars[i - 1].y} x2={s.x} y2={s.y} stroke={ready ? "var(--sage)" : "var(--gold)"} strokeOpacity="0.5" initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} />
        ),
      )}
      {stars.slice(0, shown).map((s, i) => (
        <motion.circle key={i} cx={s.x} cy={s.y} r={3} fill={ready ? "var(--sage)" : "var(--gold)"} initial={{ scale: 0, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ type: "spring" }} />
      ))}
      {order < 2 && (
        <motion.circle cx="100" cy="68" r="20" fill="none" stroke="var(--gold)" strokeDasharray="4 8" animate={{ rotate: 360 }} transition={{ duration: 6, repeat: Infinity, ease: "linear" }} style={{ originX: "100px", originY: "68px" }} />
      )}
    </svg>
  );
}
