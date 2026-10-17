import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowLeft, BookMarked, ChevronDown, GitFork, Globe, History, Lock, MessageSquare, Network, RotateCcw, Save, Send, Sparkles, Wand2, X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Book, Checkpoint, Citation, Message, RetrievalTrace, StoryDiff, StoryGraph, Storyline } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { GraphView, type Overlay } from "../components/GraphView";
import { InkDots, PageLoader, QuillLoader } from "../components/Loaders";
import { Markdown } from "../components/Markdown";
import { useFork } from "../components/StorylineCard";
import { Badge, Button, timeAgo } from "../components/ui";
import { errorText, useToast } from "../lib/ui";

const looksLikeEdit = (t: string) => /^\s*(what if|suppose|imagine|rewrite|change|make|let)\b/i.test(t) || /\b(instead of|never|hadn't|didn't)\b/i.test(t);

interface Pending {
  user: Message;
  phase: string;
  text: string;
}

export default function Workspace() {
  const { bookId = "", storylineId } = useParams();
  const sid = storylineId ?? null;
  const [params, setParams] = useSearchParams();
  const { user } = useAuth();
  const nav = useNavigate();
  const toast = useToast();
  const { fork, overlay: forkOverlay } = useFork();

  const [book, setBook] = useState<Book | null>(null);
  const [storyline, setStoryline] = useState<Storyline | null>(null);
  const [lines, setLines] = useState<Storyline[]>([]);
  const [graph, setGraph] = useState<StoryGraph | null>(null);
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [restoreOverlay, setRestoreOverlay] = useState<{ overlay: Overlay; label: string; ghost: StoryGraph } | null>(null);
  const [pane, setPane] = useState<"chat" | "graph">("chat");
  const [drawer, setDrawer] = useState(false);
  const [error, setError] = useState("");
  const autoSent = useRef(false);

  const mine = !!storyline && storyline.owner_id === user?.id;
  const readOnly = !!storyline && !mine;

  // Load book, storyline, graph and history whenever the storyline changes.
  useEffect(() => {
    let live = true;
    setMessages(null);
    setRestoreOverlay(null);
    Promise.all([api.getBook(bookId), sid ? api.getStoryline(sid) : Promise.resolve(null), api.getGraph(bookId, sid), api.history(bookId, sid), api.listStorylines(bookId)])
      .then(([b, s, g, h, ls]) => {
        if (!live) return;
        setBook(b);
        setStoryline(s);
        setGraph(g);
        setMessages(h);
        setLines(ls);
        setActive([...h].reverse().find((m) => m.role === "assistant")?.id ?? null);
      })
      .catch((e) => live && setError(errorText(e)));
    return () => void (live = false);
  }, [bookId, sid]);

  const send = useCallback(
    async (text: string) => {
      if (!text.trim() || pending) return;
      const userMsg: Message = { id: "tmp", role: "user", content: text, created_at: new Date().toISOString() };
      setPending({ user: userMsg, phase: "Opening the book…", text: "" });
      setRestoreOverlay(null);
      setPane("chat");
      try {
        const turn = await api.send(bookId, sid, text, (e) =>
          setPending((p) => p && (e.type === "phase" ? { ...p, phase: e.label } : { ...p, text: p.text + e.text })),
        );
        setMessages((m) => [...(m ?? []), turn.user, turn.assistant]);
        setGraph(turn.graph);
        setActive(turn.assistant.id);
        if (turn.storyline && !sid) {
          toast(`Your what-if branched a new storyline: “${turn.storyline.title}”`, "success");
          nav(`/read/${bookId}/${turn.storyline.id}`);
        } else if (turn.storyline) {
          setStoryline(turn.storyline);
        }
      } catch (e) {
        toast(errorText(e), "error");
      } finally {
        setPending(null);
      }
    },
    [bookId, sid, pending, nav, toast],
  );

  // A new storyline opens with ?prompt=<premise>; send it once.
  useEffect(() => {
    const prompt = params.get("prompt");
    if (prompt && messages && !autoSent.current) {
      autoSent.current = true;
      setParams({}, { replace: true });
      send(prompt);
    }
  }, [params, messages, send, setParams]);

  const activeMsg = messages?.find((m) => m.id === active);
  const overlay: Overlay = restoreOverlay?.overlay ?? (activeMsg?.diff ? { kind: "diff", diff: activeMsg.diff, trace: activeMsg.trace } : activeMsg?.trace ? { kind: "trace", trace: activeMsg.trace } : null);

  if (error) return <div className="p-10 text-center text-rose">{error}</div>;
  if (!book || !graph || !messages) return <PageLoader label="Opening the story…" />;

  return (
    <div className="flex h-[calc(100dvh-4rem)] flex-col">
      {forkOverlay}
      {/* top bar */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-line/60 px-4 py-2.5">
        <Link to={`/books/${book.id}`} className="flex items-center gap-1.5 text-sm text-ink-mute hover:text-gold" title="Back to book">
          <ArrowLeft className="size-4" />
          <span className="hidden max-w-48 truncate font-display text-base text-ink sm:inline">{book.title}</span>
        </Link>
        <span className="text-line">/</span>
        <StorylinePicker current={storyline} lines={lines} userId={user?.id} onPick={(id) => nav(id ? `/read/${bookId}/${id}` : `/read/${bookId}`)} />
        {storyline && <Badge tone="sage">op {storyline.head_op_seq}</Badge>}
        <div className="ml-auto flex items-center gap-1.5">
          {storyline && mine && (
            <Button variant="ghost" className="px-2.5" title={storyline.visibility === "public" ? "Public: click to make private" : "Private: click to publish"}
              onClick={async () => {
                const next = storyline.visibility === "public" ? "private" : "public";
                try {
                  setStoryline(await api.updateStoryline(storyline.id, { visibility: next }));
                  if (next === "public") navigator.clipboard?.writeText(window.location.href).catch(() => {});
                  toast(next === "public" ? "Published to Explore. Link copied." : "Storyline is private again.", "success");
                } catch (e) {
                  toast(errorText(e), "error");
                }
              }}>
              {storyline.visibility === "public" ? <Globe className="size-4 text-sage" /> : <Lock className="size-4" />}
              <span className="hidden sm:inline">{storyline.visibility === "public" ? "Published" : "Publish"}</span>
            </Button>
          )}
          {storyline && (
            <Button variant="ghost" className="px-2.5" onClick={() => fork(storyline)} title="Fork into your own storyline">
              <GitFork className="size-4" /> <span className="hidden sm:inline">Fork</span>
            </Button>
          )}
          {storyline && (
            <Button variant="ghost" className="px-2.5" onClick={() => setDrawer(true)} title="Checkpoints">
              <History className="size-4" /> <span className="hidden sm:inline">Checkpoints</span>
            </Button>
          )}
        </div>
      </div>

      {/* mobile pane switch */}
      <div className="flex border-b border-line/60 lg:hidden">
        {(["chat", "graph"] as const).map((p) => (
          <button key={p} onClick={() => setPane(p)} className={`relative flex flex-1 items-center justify-center gap-2 py-2.5 text-sm ${pane === p ? "text-gold" : "text-ink-mute"}`}>
            {p === "chat" ? <MessageSquare className="size-4" /> : <Network className="size-4" />}
            {p === "chat" ? "Conversation" : "Story graph"}
            {pane === p && <motion.span layoutId="pane" className="absolute inset-x-6 bottom-0 h-px bg-gold" />}
          </button>
        ))}
      </div>

      <div className="grid min-h-0 flex-1 lg:grid-cols-[minmax(380px,0.9fr)_1.1fr]">
        <section className={`min-h-0 flex-col border-line/60 lg:flex lg:border-r ${pane === "chat" ? "flex" : "hidden"}`}>
          <Chat
            messages={messages}
            pending={pending}
            active={active}
            onActivate={(id) => (setActive(id), setRestoreOverlay(null))}
            onSend={send}
            graph={graph}
            readOnly={readOnly}
            storyline={storyline}
            onFork={() => storyline && fork(storyline)}
            onShowGraph={() => setPane("graph")}
          />
        </section>
        <section className={`relative min-h-0 flex-col lg:flex ${pane === "graph" ? "flex" : "hidden"}`}>
          <GraphHeader overlay={overlay} activeMsg={activeMsg} restoreLabel={restoreOverlay?.label} onClear={() => (setActive(null), setRestoreOverlay(null))} />
          <div className="min-h-0 flex-1">
            <GraphView graph={restoreOverlay?.ghost ?? graph} overlay={overlay} />
          </div>
        </section>
      </div>

      {storyline && (
        <CheckpointDrawer
          open={drawer}
          onClose={() => setDrawer(false)}
          storyline={storyline}
          canRestore={mine}
          onRestored={(s, g) => {
            const before = visibleKeys(graph);
            const after = visibleKeys(g);
            const removed = [...before].filter((k) => !after.has(k));
            setStoryline(s);
            setGraph(g);
            setActive(null);
            setRestoreOverlay({
              label: `Restored to op ${s.head_op_seq}`,
              ghost: withGhosts(g, graph, removed),
              overlay: { kind: "diff", diff: { divergence_pos: 0, op_seq: s.head_op_seq, passage: "", added: [...after].filter((k) => !before.has(k)), hidden: removed, modified: [], added_edges: [] } },
            });
            setPane("graph");
          }}
        />
      )}
    </div>
  );
}

/** `g` plus nodes reverted away since `prev`, kept as hidden ghosts so the restore can show what left. */
function withGhosts(g: StoryGraph, prev: StoryGraph, removed: string[]): StoryGraph {
  const keys = new Set([...g.entities, ...g.events].map((n) => n.key));
  const gone = new Set(removed.filter((k) => !keys.has(k)));
  const relIds = new Set(g.relations.map((r) => r.id));
  return {
    ...g,
    entities: [...g.entities, ...prev.entities.filter((e) => gone.has(e.key))],
    events: [...g.events, ...prev.events.filter((e) => gone.has(e.key))],
    relations: [...g.relations, ...prev.relations.filter((r) => !relIds.has(r.id) && (gone.has(r.src) || gone.has(r.dst)))],
    hidden: [...g.hidden, ...gone],
  };
}

function visibleKeys(g: StoryGraph) {
  const hidden = new Set(g.hidden);
  return new Set([...g.entities, ...g.events].map((n) => n.key).filter((k) => !hidden.has(k)));
}

// --- storyline picker ------------------------------------------------------

function StorylinePicker({ current, lines, userId, onPick }: { current: Storyline | null; lines: Storyline[]; userId?: string; onPick: (id: string | null) => void }) {
  const [open, setOpen] = useState(false);
  const mine = lines.filter((s) => s.owner_id === userId);
  const others = lines.filter((s) => s.owner_id !== userId);
  return (
    <div className="relative">
      <button onClick={() => setOpen((o) => !o)} className="flex max-w-[60vw] items-center gap-1.5 rounded-lg px-2 py-1 text-sm hover:bg-surface-2 sm:max-w-80">
        {current ? <Wand2 className="size-3.5 shrink-0 text-sage" /> : <BookMarked className="size-3.5 shrink-0 text-gold" />}
        <span className="truncate">{current ? current.title : "Canon (original)"}</span>
        <ChevronDown className={`size-3.5 shrink-0 transition ${open ? "rotate-180" : ""}`} />
      </button>
      <AnimatePresence>
        {open && (
          <>
            <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
            <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} className="card absolute left-0 top-full z-40 mt-1 max-h-96 w-80 overflow-y-auto p-1.5">
              <PickItem icon={<BookMarked className="size-3.5 text-gold" />} title="Canon (original)" sub="The book as written" active={!current} onClick={() => (onPick(null), setOpen(false))} />
              {mine.length > 0 && <p className="px-2.5 pb-1 pt-3 text-[10px] uppercase tracking-wider text-ink-mute">Your storylines</p>}
              {mine.map((s) => <PickItem key={s.id} icon={<Wand2 className="size-3.5 text-sage" />} title={s.title} sub={`${s.head_op_seq} edits · ${timeAgo(s.updated_at)}`} active={current?.id === s.id} onClick={() => (onPick(s.id), setOpen(false))} />)}
              {others.length > 0 && <p className="px-2.5 pb-1 pt-3 text-[10px] uppercase tracking-wider text-ink-mute">Community</p>}
              {others.map((s) => <PickItem key={s.id} icon={<Globe className="size-3.5 text-violet" />} title={s.title} sub={`by ${s.owner_name} · ★ ${s.stars}`} active={current?.id === s.id} onClick={() => (onPick(s.id), setOpen(false))} />)}
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </div>
  );
}

function PickItem({ icon, title, sub, active, onClick }: { icon: React.ReactNode; title: string; sub: string; active: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className={`flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left ${active ? "bg-gold-soft" : "hover:bg-surface-2"}`}>
      <span className="mt-0.5">{icon}</span>
      <span className="min-w-0">
        <span className="block truncate text-sm">{title}</span>
        <span className="block text-xs text-ink-mute">{sub}</span>
      </span>
    </button>
  );
}

// --- chat ------------------------------------------------------------------

function Chat({
  messages, pending, active, onActivate, onSend, graph, readOnly, storyline, onFork, onShowGraph,
}: {
  messages: Message[]; pending: Pending | null; active: string | null; onActivate: (id: string) => void; onSend: (t: string) => void;
  graph: StoryGraph; readOnly: boolean; storyline: Storyline | null; onFork: () => void; onShowGraph: () => void;
}) {
  const [text, setText] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const ta = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, pending?.text, pending?.phase]);

  const suggestions = useMemo(() => {
    const top = [...graph.entities].filter((e) => !graph.hidden.includes(e.key)).sort((a, b) => b.mention_count - a.mention_count);
    const [a, b, c] = top;
    if (!a) return [];
    return [`Who is ${a.name}?`, b && `How are ${a.name} and ${b.name} connected?`, "What is this story about?", c && `What if ${c.name} never met ${a.name}?`].filter(Boolean) as string[];
  }, [graph]);

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    if (!text.trim()) return;
    onSend(text.trim());
    setText("");
  };
  const edit = looksLikeEdit(text);

  return (
    <>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6">
        {readOnly && storyline && (
          <div className="mb-5 flex items-center gap-3 rounded-xl border border-violet/30 bg-violet/5 px-4 py-3 text-sm">
            <Globe className="size-4 shrink-0 text-violet" />
            <span className="flex-1 text-ink-soft">You're reading <b className="text-ink">{storyline.owner_name}</b>'s storyline. Ask anything; fork it to rewrite.</span>
            <Button variant="outline" className="px-3 py-1.5" onClick={onFork}><GitFork className="size-3.5" /> Fork</Button>
          </div>
        )}
        {messages.length === 0 && !pending && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex flex-col items-center py-10 text-center">
            <motion.div animate={{ rotate: [0, 8, -6, 0] }} transition={{ duration: 6, repeat: Infinity }}>
              <Sparkles className="size-10 text-gold" />
            </motion.div>
            <h2 className="mt-4 font-display text-3xl">{storyline ? storyline.title : "Ask the book anything"}</h2>
            <p className="mt-2 max-w-sm text-sm text-ink-soft">
              {storyline ? <>“{storyline.premise}”</> : <>Ask about characters and events, or start with <i className="text-gold">“What if…”</i> to branch your own storyline.</>}
            </p>
            <div className="mt-6 flex max-w-md flex-wrap justify-center gap-2">
              {suggestions.map((s, i) => (
                <motion.button key={s} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 + i * 0.07 }} onClick={() => onSend(s)}
                  className={`rounded-full border px-3 py-1.5 text-sm transition ${looksLikeEdit(s) ? "border-sage/40 text-sage hover:bg-sage/10" : "border-line text-ink-soft hover:border-gold/50 hover:text-ink"}`}>
                  {looksLikeEdit(s) && "✦ "}{s}
                </motion.button>
              ))}
            </div>
          </motion.div>
        )}

        <div className="space-y-5">
          {messages.map((m) =>
            m.role === "user" ? <UserBubble key={m.id} m={m} /> : <AssistantBubble key={m.id} m={m} reverted={!!storyline && !!m.diff && m.diff.op_seq > storyline.head_op_seq} active={active === m.id} onActivate={() => onActivate(m.id)} onShowGraph={onShowGraph} />,
          )}
          {pending && (
            <>
              <UserBubble m={pending.user} />
              <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="card max-w-[92%] p-4">
                {pending.text ? (
                  <div className="prose-story text-[15px]">
                    <Markdown text={pending.text} />
                    <motion.span className="ml-0.5 inline-block h-4 w-0.5 translate-y-0.5 bg-gold" animate={{ opacity: [1, 0] }} transition={{ duration: 0.7, repeat: Infinity }} />
                  </div>
                ) : (
                  <div className="flex items-center gap-4">
                    <QuillLoader />
                    <AnimatePresence mode="wait">
                      <motion.span key={pending.phase} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} className="font-display text-lg italic text-ink-soft">
                        {pending.phase}
                      </motion.span>
                    </AnimatePresence>
                  </div>
                )}
              </motion.div>
            </>
          )}
        </div>
        <div ref={end} />
      </div>

      <form onSubmit={submit} className="border-t border-line/60 p-3 sm:p-4">
        <AnimatePresence>
          {edit && text && (
            <motion.p initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className={`mb-2 flex items-center gap-1.5 text-xs ${readOnly ? "text-rose" : "text-sage"}`}>
              <Wand2 className="size-3.5" />
              {readOnly ? "This looks like a rewrite. Fork the storyline to change it." : storyline ? "This will rewrite the storyline." : "This will branch a new storyline from canon."}
            </motion.p>
          )}
        </AnimatePresence>
        <div className={`flex items-end gap-2 rounded-2xl border bg-bg-2 p-2 transition ${edit && text ? "border-sage/50 shadow-[0_0_0_3px_color-mix(in_srgb,var(--sage)_15%,transparent)]" : "border-line focus-within:border-gold/60"}`}>
          <textarea
            ref={ta}
            rows={1}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              e.target.style.height = "auto";
              e.target.style.height = Math.min(160, e.target.scrollHeight) + "px";
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder={storyline ? "Ask about this storyline, or “What if…”" : "Ask a question, or “What if…”"}
            className="max-h-40 min-h-[40px] flex-1 resize-none bg-transparent px-2 py-2 text-[15px] outline-none placeholder:text-ink-mute"
            disabled={!!pending}
          />
          <Button type="submit" disabled={!text.trim() || !!pending} className={`size-10 shrink-0 rounded-xl p-0 ${edit ? "!bg-sage" : ""}`} aria-label="Send">
            {pending ? <InkDots /> : edit ? <Wand2 className="size-4" /> : <Send className="size-4" />}
          </Button>
        </div>
      </form>
    </>
  );
}

function UserBubble({ m }: { m: Message }) {
  return (
    <motion.div initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} className="flex justify-end">
      <div className={`max-w-[85%] rounded-2xl rounded-br-md px-4 py-2.5 text-[15px] ${looksLikeEdit(m.content) ? "border border-sage/30 bg-sage/10" : "bg-surface-2"}`}>{m.content}</div>
    </motion.div>
  );
}

const ROUTE_BADGE = {
  local: { tone: "violet", label: "Local search" },
  global: { tone: "violet", label: "Global search" },
  canon: { tone: "gold", label: "Canon" },
  edit: { tone: "sage", label: "Storyline edit" },
} as const;

function AssistantBubble({ m, active, reverted, onActivate, onShowGraph }: { m: Message; active: boolean; reverted: boolean; onActivate: () => void; onShowGraph: () => void }) {
  const [why, setWhy] = useState(false);
  const r = m.route ? ROUTE_BADGE[m.route] : null;
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      onClick={onActivate}
      className={`card relative max-w-[94%] cursor-pointer p-4 transition ${reverted ? "opacity-55" : ""} ${active ? "!border-gold/50 ring-1 ring-gold/20" : "hover:border-gold/30"}`}
    >
      {active && <motion.span layoutId="active-msg" className="absolute -left-[3px] top-4 h-8 w-[3px] rounded-full bg-gold" />}
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        {r && <Badge tone={r.tone}>{r.label}</Badge>}
        {m.diff && <DiffChips diff={m.diff} />}
        {reverted && <Badge tone="mute"><RotateCcw className="size-3" /> reverted</Badge>}
      </div>
      <Markdown text={m.content} className="prose-story text-[15px]" />
      {m.diff?.passage && <Passage text={m.diff.passage} />}
      {m.citations && m.citations.length > 0 && <Citations items={m.citations} />}
      {m.trace && (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line/60 pt-3">
          <button onClick={(e) => (e.stopPropagation(), setWhy((w) => !w), onActivate())} className="flex items-center gap-1.5 text-xs text-violet hover:underline">
            <Network className="size-3.5" /> {why ? "Hide reasoning" : m.diff ? "How was this applied?" : "Why this answer?"}
          </button>
          <button onClick={(e) => (e.stopPropagation(), onActivate(), onShowGraph())} className="ml-auto text-xs text-ink-mute hover:text-gold lg:hidden">
            Show on graph →
          </button>
        </div>
      )}
      <AnimatePresence>{why && m.trace && <TraceSteps trace={m.trace} />}</AnimatePresence>
    </motion.div>
  );
}

function DiffChips({ diff }: { diff: StoryDiff }) {
  return (
    <>
      {diff.added.length > 0 && <Badge tone="sage">+{diff.added.length} added</Badge>}
      {diff.hidden.length > 0 && <Badge tone="rose">−{diff.hidden.length} hidden</Badge>}
      {diff.modified.length > 0 && <Badge tone="amber">~{diff.modified.length} modified</Badge>}
    </>
  );
}

function Passage({ text }: { text: string }) {
  return (
    <motion.blockquote initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.2 }} className="relative mt-4 rounded-xl border border-sage/25 bg-sage/5 px-5 py-4 font-serif text-[15px] italic leading-relaxed text-ink">
      <span className="float-left mr-2 mt-1 font-display text-5xl not-italic leading-[0.8] text-sage">{text.charAt(0)}</span>
      {text.slice(1)}
      <span className="mt-2 block text-right text-[11px] not-italic text-ink-mute">new passage · this storyline</span>
    </motion.blockquote>
  );
}

function Citations({ items }: { items: Citation[] }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <div className="mt-3">
      <div className="flex flex-wrap gap-1.5">
        {items.map((c, i) => (
          <button key={i} onClick={(e) => (e.stopPropagation(), setOpen(open === i ? null : i))}
            className={`rounded-md border px-2 py-0.5 text-[11px] transition ${open === i ? "border-gold/50 bg-gold-soft text-gold" : c.storyline_chunk ? "border-sage/30 text-sage" : "border-line text-ink-mute hover:text-ink"}`}>
            {c.storyline_chunk ? "✦ storyline" : `${c.chapter ?? ""}${c.page ? ` · p. ${c.page}` : ""}`}
          </button>
        ))}
      </div>
      <AnimatePresence>
        {open !== null && (
          <motion.blockquote key={open} initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="mt-2 overflow-hidden border-l-2 border-gold/50 pl-3 font-serif text-sm italic text-ink-soft">
            “{items[open].quote}”
          </motion.blockquote>
        )}
      </AnimatePresence>
    </div>
  );
}

function TraceSteps({ trace }: { trace: RetrievalTrace }) {
  return (
    <motion.ol initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="relative mt-3 space-y-3 overflow-hidden pl-5">
      <span className="absolute bottom-2 left-[5px] top-2 w-px bg-violet/30" />
      {trace.steps.map((s, i) => (
        <motion.li key={i} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.08 }} className="relative text-sm">
          <span className="absolute -left-5 top-1.5 size-2.5 rounded-full border-2 border-violet bg-bg" />
          <span className="font-medium text-ink">{s.label}</span>
          <span className="block text-xs text-ink-soft">{s.detail}</span>
        </motion.li>
      ))}
    </motion.ol>
  );
}

// --- graph header ------------------------------------------------------------

function GraphHeader({ overlay, activeMsg, restoreLabel, onClear }: { overlay: Overlay; activeMsg?: Message; restoreLabel?: string; onClear: () => void }) {
  let title = "The whole story";
  let detail = "Select an answer to see the path it took.";
  if (restoreLabel && overlay?.kind === "diff") {
    title = restoreLabel;
    detail = `${overlay.diff.added.length} nodes returned · ${overlay.diff.hidden.length} removed`;
  } else if (overlay?.kind === "diff") {
    title = "What this edit changed";
    detail = `Diverges at story position ${overlay.diff.divergence_pos} · op ${overlay.diff.op_seq}`;
  } else if (overlay?.kind === "trace") {
    const t = overlay.trace;
    title = activeMsg?.route === "global" ? "Why: big-picture summary" : "Why this answer";
    detail = t.communities.length ? `${t.communities.length} communities summarised` : t.seeds.length ? `${t.seeds.length} seed${t.seeds.length > 1 ? "s" : ""} → ${t.expanded.length} nodes reached in ${t.hops} hop · ${t.edges.length} relations walked` : "Nothing matched in the graph";
  }
  return (
    <div className="flex items-center gap-3 border-b border-line/60 px-4 py-2.5">
      <Network className="size-4 text-gold" />
      <div className="min-w-0 flex-1">
        <AnimatePresence mode="wait">
          <motion.div key={title + detail} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }}>
            <p className="truncate font-display text-lg leading-tight">{title}</p>
            <p className="truncate text-xs text-ink-mute">{detail}</p>
          </motion.div>
        </AnimatePresence>
      </div>
      {overlay && (
        <button onClick={onClear} className="flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-ink-mute hover:bg-surface-2 hover:text-ink">
          <X className="size-3.5" /> Clear
        </button>
      )}
    </div>
  );
}

// --- checkpoints ---------------------------------------------------------------

function CheckpointDrawer({ open, onClose, storyline, canRestore, onRestored }: { open: boolean; onClose: () => void; storyline: Storyline; canRestore: boolean; onRestored: (s: Storyline, g: StoryGraph) => void }) {
  const toast = useToast();
  const [list, setList] = useState<Checkpoint[] | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);

  useEffect(() => {
    if (open) api.listCheckpoints(storyline.id).then(setList);
  }, [open, storyline.id, storyline.head_op_seq]);

  const saveCp = async (e: FormEvent) => {
    e.preventDefault();
    setBusy("save");
    try {
      const c = await api.saveCheckpoint(storyline.id, name);
      setList((l) => [c, ...(l ?? [])]);
      setName("");
      toast(`Saved “${c.name}”.`, "success");
    } catch (err) {
      toast(errorText(err), "error");
    } finally {
      setBusy(null);
    }
  };
  const restore = async (c: Checkpoint) => {
    setBusy(c.id);
    try {
      const { storyline: s, graph } = await api.restoreCheckpoint(storyline.id, c.id);
      onRestored(s, graph);
      toast(`Restored to “${c.name}”. Your previous head was auto-saved.`, "success");
      onClose();
    } catch (err) {
      toast(errorText(err), "error");
    } finally {
      setBusy(null);
      setConfirm(null);
    }
  };

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div className="fixed inset-0 z-40 bg-black/40" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={onClose} />
          <motion.aside initial={{ x: "100%" }} animate={{ x: 0 }} exit={{ x: "100%" }} transition={{ type: "spring", stiffness: 300, damping: 34 }}
            className="fixed inset-y-0 right-0 z-50 flex w-full max-w-sm flex-col border-l border-line bg-surface shadow-2xl">
            <div className="flex items-center justify-between border-b border-line px-5 py-4">
              <div>
                <h2 className="font-display text-2xl">Checkpoints</h2>
                <p className="text-xs text-ink-mute">Head is at op {storyline.head_op_seq}</p>
              </div>
              <button onClick={onClose} className="text-ink-mute hover:text-ink" aria-label="Close"><X className="size-5" /></button>
            </div>
            {canRestore && (
              <form onSubmit={saveCp} className="flex gap-2 border-b border-line p-4">
                <input className="input py-2" value={name} onChange={(e) => setName(e.target.value)} placeholder={`Checkpoint at op ${storyline.head_op_seq}`} />
                <Button type="submit" loading={busy === "save"} className="shrink-0"><Save className="size-4" /></Button>
              </form>
            )}
            <div className="flex-1 overflow-y-auto p-4">
              {!list ? (
                <div className="flex justify-center py-10"><InkDots className="text-gold" /></div>
              ) : list.length === 0 ? (
                <p className="py-10 text-center text-sm text-ink-mute">No checkpoints yet. Save one before a risky rewrite.</p>
              ) : (
                <ol className="relative space-y-3 pl-5">
                  <span className="absolute bottom-3 left-[5px] top-3 w-px bg-line" />
                  {list.map((c, i) => (
                    <motion.li key={c.id} initial={{ opacity: 0, x: 10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.04 }} className="relative">
                      <span className={`absolute -left-5 top-3 size-2.5 rounded-full border-2 ${c.op_seq === storyline.head_op_seq ? "border-gold bg-gold" : "border-line bg-surface"}`} />
                      <div className="rounded-xl border border-line p-3">
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <p className="truncate text-sm font-medium">{c.name}</p>
                            <p className="text-xs text-ink-mute">op {c.op_seq} · {timeAgo(c.created_at)}{c.auto && " · auto"}</p>
                          </div>
                          {canRestore && c.op_seq !== storyline.head_op_seq && (
                            confirm === c.id ? (
                              <Button variant="danger" className="px-2.5 py-1 text-xs" loading={busy === c.id} onClick={() => restore(c)}>Confirm</Button>
                            ) : (
                              <Button variant="outline" className="px-2.5 py-1 text-xs" onClick={() => setConfirm(c.id)}><RotateCcw className="size-3" /> Restore</Button>
                            )
                          )}
                        </div>
                      </div>
                    </motion.li>
                  ))}
                </ol>
              )}
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
