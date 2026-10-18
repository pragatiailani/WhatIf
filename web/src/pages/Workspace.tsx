import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowLeft, BookMarked, Check, ChevronDown, Globe, Lock, MessageSquare, MessagesSquare, Network, Plus, RotateCcw, Send, Sparkles, Wand2, X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { ACCEPT_PROPOSAL } from "../api/types";
import type { Book, ChatSummary, Citation, Message, Proposal, Reasoning, StoryDiff, StoryGraph, Storyline, TraceStep } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { GraphView, type Overlay } from "../components/GraphView";
import { InkDots, PageLoader, QuillLoader } from "../components/Loaders";
import { Markdown } from "../components/Markdown";
import { InkittTokenModal } from "../components/InkittTokenModal";
import { Badge, Button, timeAgo } from "../components/ui";
import { errorText, useToast } from "../lib/ui";

const ACCEPT_ALL_KEY = "storyline.acceptAll";

const looksLikeEdit =(t: string) => /^\s*(what if|suppose|imagine|rewrite|change|make|let)\b/i.test(t) || /\b(instead of|never|hadn't|didn't)\b/i.test(t);

interface Pending {
  user: Message;
  phase: string;
  text: string;
  steps: TraceStep[];
  reasoning?: Reasoning;
}

export default function Workspace() {
  const { bookId = "", storylineId } = useParams();
  const sid = storylineId ?? null;
  const [params, setParams] = useSearchParams();
  const { user } = useAuth();
  const nav = useNavigate();
  const toast = useToast();

  const [book, setBook] = useState<Book | null>(null);
  const [storyline, setStoryline] = useState<Storyline | null>(null);
  const [lines, setLines] = useState<Storyline[]>([]);
  const [graph, setGraph] = useState<StoryGraph | null>(null);
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  /** the open chat; null for a new one, saved when its first question is asked */
  const [chatId, setChatId] = useState<string | null>(null);
  const [chatLoading, setChatLoading] = useState(false);
  const shownChat = useRef<string | null>(null);
  const chatParam = useRef(params.get("chat"));
  chatParam.current = params.get("chat");
  const [pending, setPending] = useState<Pending | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [pane, setPane] = useState<"chat" | "graph">("chat");
  const [error, setError] = useState("");
  /** the message to send again once the user has connected Inkitt */
  const [inkittRetry, setInkittRetry] = useState<string | null>(null);
  const autoSent = useRef(false);
  /** bumped whenever another book or storyline is opened, so a turn still streaming for the old one stops updating the page */
  const view = useRef(0);
  /** accept all: the assistant settles a whole draft without asking before each batch */
  const [acceptAll, setAcceptAll] = useState(() => {
    try {
      return localStorage.getItem(ACCEPT_ALL_KEY) === "1";
    } catch {
      return false;
    }
  });
  const toggleAcceptAll = (on: boolean) => {
    setAcceptAll(on);
    try {
      localStorage.setItem(ACCEPT_ALL_KEY, on ? "1" : "0");
    } catch {
      /* storage blocked: the choice lasts for this page only */
    }
  };

  const mine = !!storyline && storyline.owner_id === user?.id;
  const readOnly = !!storyline && !mine;

  // Load book, storyline, graph and chats whenever the storyline changes, then open the chat named
  // in ?chat= or the most recent one.
  useEffect(() => {
    let live = true;
    view.current += 1;
    setMessages(null);
    setPending(null); // a turn still streaming belongs to the storyline we left; it is saved there
    Promise.all([api.getBook(bookId), sid ? api.getStoryline(sid) : Promise.resolve(null), api.getGraph(bookId, sid), api.listChats(bookId, sid), api.listStorylines(bookId)])
      .then(async ([b, s, g, cs, ls]) => {
        const pick = cs.find((c) => c.id === chatParam.current)?.id ?? cs[0]?.id ?? null;
        const h = pick ? await api.history(bookId, sid, pick) : [];
        if (!live) return;
        setBook(b);
        setStoryline(s);
        setGraph(g);
        setChats(cs);
        setChatId(pick);
        shownChat.current = pick;
        setMessages(h);
        setLines(ls);
        setActive(lastAnswer(h));
      })
      .catch((e) => live && setError(errorText(e)));
    return () => void (live = false);
  }, [bookId, sid]);

  const openChat = useCallback(
    async (id: string | null) => {
      shownChat.current = id;
      setChatId(id);
      setActive(null);
      setParams(id ? { chat: id } : {}, { replace: true });
      if (!id) return setMessages([]);
      setChatLoading(true);
      try {
        const h = await api.history(bookId, sid, id);
        if (shownChat.current !== id) return; // another chat was opened meanwhile
        setMessages(h);
        setActive(lastAnswer(h));
      } catch (e) {
        toast(errorText(e), "error");
      } finally {
        if (shownChat.current === id) setChatLoading(false);
      }
    },
    [bookId, sid, setParams, toast],
  );

  const send = useCallback(
    async (text: string) => {
      if (!text.trim() || pending) return;
      const userMsg: Message = { id: "tmp", role: "user", content: text, created_at: new Date().toISOString() };
      setPending({ user: userMsg, phase: "Opening the book…", text: "", steps: [] });
      setPane("chat");
      const started = view.current;
      const left = () => view.current !== started; // the reader opened another storyline meanwhile
      try {
        const turn = await api.send(bookId, sid, chatId, text, (e) =>
          setPending((p) => {
            if (!p || left()) return p;
            // A new phase means the text so far was the agent's reasoning; it comes back in a step.
            if (e.type === "phase") return { ...p, phase: e.label, text: "" };
            if (e.type === "step") return { ...p, steps: [...p.steps, e.step] };
            if (e.type === "reasoning") return { ...p, reasoning: e.reasoning };
            return { ...p, text: p.text + e.text };
          }),
          { auto: acceptAll },
        );
        if (left()) {
          // The turn is saved in the chat it was asked in; the page now shows another storyline.
          if (turn.storyline && !sid) {
            toast(`Your what-if started a new storyline: “${turn.storyline.title}”`, "success");
            setLines((ls) => [turn.storyline!, ...ls.filter((l) => l.id !== turn.storyline!.id)]);
          }
          return;
        }
        // Only the latest answer carries the waiting proposal; an earlier one is settled or replaced.
        setMessages((m) => [...(m ?? []).map(({ proposal: _settled, ...rest }) => rest), turn.user, turn.assistant]);
        if (turn.graph) setGraph(turn.graph);
        setActive(turn.assistant.id);
        if (turn.actions?.includes("inkitt_token_required")) setInkittRetry(text);
        if (turn.storyline && !sid) {
          // The chat moved into the new storyline: carry on in it there.
          toast(`Your what-if started a new storyline: “${turn.storyline.title}”`, "success");
          nav(`/read/${bookId}/${turn.storyline.id}?chat=${turn.chat.id}`);
          return;
        }
        if (turn.storyline) setStoryline(turn.storyline);
        shownChat.current = turn.chat.id;
        setChatId(turn.chat.id);
        setChats((cs) => [turn.chat, ...cs.filter((c) => c.id !== turn.chat.id)]);
        setParams({ chat: turn.chat.id }, { replace: true });
      } catch (e) {
        if (!left()) toast(errorText(e), "error");
      } finally {
        if (!left()) setPending(null);
      }
    },
    [bookId, sid, chatId, pending, acceptAll, nav, toast, setParams],
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
  // A waiting proposal previews the graph it would give; the storyline itself changes on accept.
  const proposal = activeMsg?.proposal;
  const change = proposal?.diff ?? activeMsg?.diff;
  const overlay: Overlay = change ? { kind: "diff", diff: change, trace: activeMsg?.trace } : activeMsg?.trace ? { kind: "trace", trace: activeMsg.trace } : null;

  if (error) return <div className="p-10 text-center text-rose">{error}</div>;
  if (!book || !graph || !messages) return <PageLoader label="Opening the story…" />;

  return (
    <div className="flex h-[calc(100dvh-4rem)] flex-col">
      <InkittTokenModal
        open={inkittRetry !== null}
        onClose={() => setInkittRetry(null)}
        onSaved={() => {
          const text = inkittRetry;
          setInkittRetry(null);
          toast("Inkitt connected.", "success");
          if (text) send(text);
        }}
      />
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
          {!readOnly && <ChatBar chats={chats} current={chatId} inStoryline={!!storyline} disabled={!!pending} onPick={openChat} />}
          <Chat
            messages={messages}
            loading={chatLoading}
            pending={pending}
            active={active}
            onActivate={setActive}
            onSend={send}
            graph={graph}
            readOnly={readOnly}
            storyline={storyline}
            onShowGraph={() => setPane("graph")}
            acceptAll={acceptAll}
            onAcceptAll={toggleAcceptAll}
          />
        </section>
        <section className={`relative min-h-0 flex-col lg:flex ${pane === "graph" ? "flex" : "hidden"}`}>
          <GraphHeader overlay={overlay} activeMsg={activeMsg} proposed={!!proposal} onClear={() => setActive(null)} />
          <div className="min-h-0 flex-1">
            <GraphView graph={proposal?.graph ?? graph} overlay={overlay} />
          </div>
        </section>
      </div>

    </div>
  );
}

const lastAnswer = (h: Message[]) => [...h].reverse().find((m) => m.role === "assistant")?.id ?? null;

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

function ChatBar({ chats, current, inStoryline, disabled, onPick }: { chats: ChatSummary[]; current: string | null; inStoryline: boolean; disabled: boolean; onPick: (id: string | null) => void }) {
  const [open, setOpen] = useState(false);
  const cur = chats.find((c) => c.id === current);
  return (
    <div className="flex items-center gap-2 border-b border-line/60 px-3 py-1.5">
      <div className="relative min-w-0 flex-1">
        <button onClick={() => setOpen((o) => !o)} disabled={disabled || chats.length === 0} className="flex max-w-full items-center gap-1.5 rounded-lg px-2 py-1 text-sm enabled:hover:bg-surface-2 disabled:cursor-default">
          <MessageSquare className="size-3.5 shrink-0 text-gold" />
          <span className="truncate">{cur ? cur.title ?? "Untitled chat" : "New chat"}</span>
          {chats.length > 0 && <ChevronDown className={`size-3.5 shrink-0 transition ${open ? "rotate-180" : ""}`} />}
        </button>
        <AnimatePresence>
          {open && (
            <>
              <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
              <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} className="card absolute left-0 top-full z-40 mt-1 max-h-96 w-80 max-w-[85vw] overflow-y-auto p-1.5">
                <p className="px-2.5 pb-1 pt-1.5 text-[10px] uppercase tracking-wider text-ink-mute">Your chats {inStoryline ? "in this storyline" : "on this book"}</p>
                {chats.map((c) => <PickItem key={c.id} icon={<MessageSquare className="size-3.5 text-gold" />} title={c.title ?? "Untitled chat"} sub={timeAgo(c.last_active)} active={c.id === current} onClick={() => (onPick(c.id), setOpen(false))} />)}
              </motion.div>
            </>
          )}
        </AnimatePresence>
      </div>
      <Button variant="ghost" className="shrink-0 px-2.5 py-1 text-xs" disabled={disabled || current === null} onClick={() => onPick(null)} title="Start a new chat">
        <Plus className="size-3.5" /> New chat
      </Button>
    </div>
  );
}

function Chat({
  messages, loading, pending, active, onActivate, onSend, graph, readOnly, storyline, onShowGraph, acceptAll, onAcceptAll,
}: {
  messages: Message[]; loading: boolean; pending: Pending | null; active: string | null; onActivate: (id: string) => void; onSend: (t: string) => void;
  graph: StoryGraph; readOnly: boolean; storyline: Storyline | null; onShowGraph: () => void;
  acceptAll: boolean; onAcceptAll: (on: boolean) => void;
}) {
  const [text, setText] = useState("");
  /** the reader pressed Discuss: their next message is feedback on the proposal */
  const [discussing, setDiscussing] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const ta = useRef<HTMLTextAreaElement>(null);
  const lastAnswerId = [...messages].reverse().find((m) => m.role === "assistant")?.id;
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
    setDiscussing(false);
  };
  const edit = looksLikeEdit(text) && !discussing;
  const discuss = () => {
    setDiscussing(true);
    ta.current?.focus();
  };

  return (
    <>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6">
        {readOnly && storyline && (
          <div className="mb-5 flex items-center gap-3 rounded-xl border border-violet/30 bg-violet/5 px-4 py-3 text-sm">
            <Globe className="size-4 shrink-0 text-violet" />
            <span className="flex-1 text-ink-soft">You're reading <b className="text-ink">{storyline.owner_name}</b>'s storyline.</span>
          </div>
        )}
        {loading && <div className="flex justify-center py-10"><InkDots className="text-gold" /></div>}
        {!loading && messages.length === 0 && !pending && (
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

        <div className={`space-y-5 ${loading ? "hidden" : ""}`}>
          {messages.map((m) =>
            m.role === "user" ? (
              <UserBubble key={m.id} m={m} />
            ) : (
              <AssistantBubble key={m.id} m={m} reverted={!!storyline && !!m.diff && m.diff.op_seq > storyline.head_op_seq} active={active === m.id} onActivate={() => onActivate(m.id)} onShowGraph={onShowGraph}>
                {m.proposal && m.id === lastAnswerId && !readOnly && (
                  <ProposalActions proposal={m.proposal} busy={!!pending} discussing={discussing} onAccept={() => (setDiscussing(false), onSend(ACCEPT_PROPOSAL))} onDiscuss={discuss} />
                )}
              </AssistantBubble>
            ),
          )}
          {pending && (
            <>
              <UserBubble m={pending.user} />
              <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="card max-w-[92%] p-4">
                {pending.steps.length > 0 && <div className="mb-3 border-b border-line/60 pb-3"><TraceSteps steps={pending.steps} /></div>}
                {pending.text ? (
                  <div className="prose-story text-[15px]">
                    <Markdown text={pending.text} />
                    <motion.span className="ml-0.5 inline-block h-4 w-0.5 translate-y-0.5 bg-gold" animate={{ opacity: [1, 0] }} transition={{ duration: 0.7, repeat: Infinity }} />
                    {pending.reasoning && <ReasoningChain reasoning={pending.reasoning} />}
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
          {discussing && (
            <motion.p initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="mb-2 flex items-center gap-1.5 text-xs text-violet">
              <MessagesSquare className="size-3.5" />
              <span className="flex-1">What should change in the proposal? Your suggestion goes back to the assistant.</span>
              <button type="button" onClick={() => setDiscussing(false)} className="text-ink-mute hover:text-ink" aria-label="Stop discussing"><X className="size-3.5" /></button>
            </motion.p>
          )}
          {edit && text && (
            <motion.p initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className={`mb-2 flex items-center gap-1.5 text-xs ${readOnly ? "text-rose" : "text-sage"}`}>
              <Wand2 className="size-3.5" />
              {readOnly ? "This looks like a rewrite. Only the storyline's owner can change it." : storyline ? "This will rewrite the storyline." : "This will branch a new storyline from canon."}
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
            placeholder={discussing ? "e.g. Keep the scene where they meet, but make it later" : storyline ? "Ask about this storyline, or “What if…”" : "Ask a question, or “What if…”"}
            className="max-h-40 min-h-[40px] flex-1 resize-none bg-transparent px-2 py-2 text-[15px] outline-none placeholder:text-ink-mute"
            disabled={!!pending}
          />
          <Button type="submit" disabled={!text.trim() || !!pending} className={`size-10 shrink-0 rounded-xl p-0 ${edit ? "!bg-sage" : ""}`} aria-label="Send">
            {pending ? <InkDots /> : edit ? <Wand2 className="size-4" /> : <Send className="size-4" />}
          </Button>
        </div>
        {!readOnly && (
          <label className="mt-2 flex w-fit cursor-pointer items-center gap-2 px-1 text-xs text-ink-mute select-none" title="Settle every change in a rewrite without asking before each batch. You still approve the result.">
            <button
              type="button"
              role="switch"
              aria-checked={acceptAll}
              onClick={() => onAcceptAll(!acceptAll)}
              className={`relative h-4 w-7 shrink-0 rounded-full transition ${acceptAll ? "bg-sage" : "bg-line"}`}
            >
              <span className={`absolute top-0.5 size-3 rounded-full bg-bg transition-all ${acceptAll ? "left-3.5" : "left-0.5"}`} />
            </button>
            <span className={acceptAll ? "text-sage" : ""}>Accept all: finish rewrites without asking</span>
          </label>
        )}
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

function AssistantBubble({ m, active, reverted, onActivate, onShowGraph, children }: { m: Message; active: boolean; reverted: boolean; onActivate: () => void; onShowGraph: () => void; children?: React.ReactNode }) {
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
      {children}
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
      <AnimatePresence>
        {why && m.trace?.reasoning && <ReasoningChain key="chain" reasoning={m.trace.reasoning} />}
        {why && m.trace && <TraceSteps key="steps" steps={m.trace.steps} />}
      </AnimatePresence>
    </motion.div>
  );
}

function ProposalActions({ proposal, busy, discussing, onAccept, onDiscuss }: { proposal: Proposal; busy: boolean; discussing: boolean; onAccept: () => void; onDiscuss: () => void }) {
  return (
    <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} onClick={(e) => e.stopPropagation()} className="mt-4 rounded-xl border border-sage/30 bg-sage/5 p-3">
      <p className="text-[11px] uppercase tracking-wider text-sage">Proposed change · not applied yet</p>
      <p className="mt-1 text-sm text-ink">{proposal.premise}</p>
      <div className="mt-2 flex flex-wrap gap-1.5"><DiffChips diff={proposal.diff} /></div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button className="!bg-sage px-3 py-1.5" disabled={busy} onClick={onAccept}><Check className="size-4" /> Accept</Button>
        <Button variant="outline" className={`px-3 py-1.5 ${discussing ? "!border-violet/50 text-violet" : ""}`} disabled={busy} onClick={onDiscuss}><MessagesSquare className="size-4" /> Discuss</Button>
      </div>
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
            {c.storyline_chunk ? "✦ storyline" : [c.chapter, c.page && `p. ${c.page}`].filter(Boolean).join(" · ")}
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

function ReasoningChain({ reasoning }: { reasoning: Reasoning }) {
  return (
    <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="mt-3 overflow-hidden">
      <p className="mb-2 text-[11px] uppercase tracking-wider text-ink-mute">Why this answer</p>
      <ol className="space-y-2">
        {reasoning.chain.map((l, i) => (
          <motion.li key={i} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.08 }} className="rounded-lg border border-line/60 px-3 py-2 text-sm">
            {l.kind === "relation" ? (
              <div className="flex flex-wrap items-center gap-1.5 font-medium text-ink">
                <span>{l.src}</span>
                <span className="rounded bg-violet/10 px-1.5 py-0.5 text-[10px] tracking-wider text-violet">{l.rel.replace(/_/g, " ")} →</span>
                <span>{l.dst}</span>
              </div>
            ) : (
              <div className="text-ink">
                <span className="mr-1.5 rounded bg-gold-soft px-1.5 py-0.5 text-[10px] tracking-wider text-gold">
                  {["EVENT", l.chapter, l.page && `p. ${l.page}`].filter(Boolean).join(" · ")}
                </span>
                {l.summary}
                {l.names.length > 0 && <span className="block text-xs text-ink-mute">{l.names.join(", ")}</span>}
              </div>
            )}
            <p className="mt-1 font-serif text-[13px] italic text-ink-soft">{l.why}</p>
          </motion.li>
        ))}
      </ol>
      <p className="mt-2 text-sm text-ink">∴ {reasoning.conclusion}</p>
    </motion.div>
  );
}

function TraceSteps({ steps }: { steps: TraceStep[] }) {
  return (
    <motion.ol initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="relative mt-3 space-y-3 overflow-hidden pl-5">
      <span className="absolute bottom-2 left-[5px] top-2 w-px bg-violet/30" />
      {steps.map((s, i) => (
        <motion.li key={i} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.08 }} className="relative text-sm">
          <span className="absolute -left-5 top-1.5 size-2.5 rounded-full border-2 border-violet bg-bg" />
          <span className="font-medium text-ink">{s.label}</span>
          {s.why && <span className="block font-serif italic text-ink-soft">{s.why}</span>}
          {s.found && <span className="block text-xs text-ink">→ {s.found}</span>}
          <span className="block text-xs text-ink-mute">{s.detail}</span>
        </motion.li>
      ))}
    </motion.ol>
  );
}

// --- graph header ------------------------------------------------------------

function GraphHeader({ overlay, activeMsg, proposed, onClear }: { overlay: Overlay; activeMsg?: Message; proposed: boolean; onClear: () => void }) {
  let title = "The whole story";
  let detail = "Select an answer to see the path it took.";
  if (proposed && overlay?.kind === "diff") {
    title = "Proposed change · not applied yet";
    detail = `${overlay.diff.added.length} would be added · ${overlay.diff.hidden.length} removed · Accept to apply`;
  } else if (overlay?.kind === "diff") {
    title = "What this edit changed";
    detail = `Diverges at story position ${overlay.diff.divergence_pos} · op ${overlay.diff.op_seq}`;
  } else if (overlay?.kind === "trace") {
    const t = overlay.trace;
    title = activeMsg?.route === "global" ? "Why: big-picture summary" : "Why this answer";
    detail = t.reasoning?.chain.length ? `${t.reasoning.chain.length} linked facts behind the answer` : t.communities.length ? `${t.communities.length} communities summarised` : t.seeds.length ? `${t.seeds.length} seed${t.seeds.length > 1 ? "s" : ""} → ${t.expanded.length} nodes reached in ${t.hops} hop · ${t.edges.length} relations walked` : "Nothing matched in the graph";
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
