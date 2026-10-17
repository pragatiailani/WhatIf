import { AnimatePresence, motion } from "framer-motion";
import { GitFork, Lock, Star } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Storyline } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { errorText, useToast } from "../lib/ui";
import { Badge, timeAgo } from "./ui";

/** Fork a storyline with a branching animation, then open the fork. */
export function useFork() {
  const { user } = useAuth();
  const nav = useNavigate();
  const toast = useToast();
  const [forking, setForking] = useState<Storyline | null>(null);
  const fork = async (s: Storyline) => {
    if (!user) return nav(`/signin?next=${encodeURIComponent(`/read/${s.book_id}/${s.id}`)}`);
    setForking(s);
    try {
      const [f] = await Promise.all([api.forkStoryline(s.id), new Promise((r) => setTimeout(r, 1400))]);
      toast(`Forked “${s.title}” into your shelf.`, "success");
      nav(`/read/${f.book_id}/${f.id}`);
    } catch (e) {
      toast(errorText(e), "error");
    } finally {
      setForking(null);
    }
  };
  const overlay = <ForkOverlay storyline={forking} />;
  return { fork, overlay, forking };
}

function ForkOverlay({ storyline }: { storyline: Storyline | null }) {
  return (
    <AnimatePresence>
      {storyline && (
        <motion.div className="fixed inset-0 z-50 grid place-items-center bg-black/55 backdrop-blur-sm" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          <motion.div className="card flex flex-col items-center gap-4 px-10 py-8" initial={{ scale: 0.9 }} animate={{ scale: 1 }}>
            <svg width="180" height="110" viewBox="0 0 180 110" aria-hidden>
              <motion.path d="M10 55 H80" stroke="var(--gold)" strokeWidth="2" fill="none" initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: 0.5 }} />
              <motion.path d="M80 55 C110 55, 120 20, 170 20" stroke="var(--gold)" strokeWidth="2" fill="none" initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ delay: 0.5, duration: 0.6 }} />
              <motion.path d="M80 55 C110 55, 120 90, 170 90" stroke="var(--sage)" strokeWidth="2" strokeDasharray="5 5" fill="none" initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ delay: 0.7, duration: 0.6 }} />
              <motion.circle cx="80" cy="55" r="6" fill="var(--gold)" initial={{ scale: 0 }} animate={{ scale: [0, 1.4, 1] }} transition={{ delay: 0.45 }} />
              <motion.circle cx="170" cy="20" r="5" fill="var(--gold)" initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ delay: 1.05 }} />
              <motion.circle cx="170" cy="90" r="6" fill="var(--sage)" initial={{ scale: 0 }} animate={{ scale: [0, 1.5, 1] }} transition={{ delay: 1.25 }} />
            </svg>
            <p className="font-display text-xl">Branching “{storyline.title}”…</p>
            <p className="text-xs text-ink-mute">Copying {storyline.head_op_seq} op{storyline.head_op_seq === 1 ? "" : "s"} into your new storyline</p>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function StorylineCard({ s, bookTitle, onFork, onChange, index = 0 }: { s: Storyline; bookTitle?: string; onFork: (s: Storyline) => void; onChange?: (s: Storyline) => void; index?: number }) {
  const { user } = useAuth();
  const toast = useToast();
  const [burst, setBurst] = useState(0);
  const mine = user?.id === s.owner_id;
  const star = async () => {
    if (!user) return toast("Sign in to star storylines.");
    setBurst((b) => b + 1);
    try {
      onChange?.({ ...(await api.starStoryline(s.id)) });
    } catch (e) {
      toast(errorText(e), "error");
    }
  };
  return (
    <motion.article
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.05 }}
      whileHover={{ y: -3 }}
      className="card group flex flex-col p-5"
    >
      <div className="mb-3 flex flex-wrap items-center gap-1.5">
        {bookTitle && <Badge tone="mute">{bookTitle}</Badge>}
        {s.parent_storyline_id && <Badge tone="violet"><GitFork className="size-3" /> fork</Badge>}
        {s.visibility === "private" && <Badge tone="mute"><Lock className="size-3" /> private</Badge>}
      </div>
      <Link to={`/read/${s.book_id}/${s.id}`} className="font-display text-2xl font-semibold leading-tight transition group-hover:text-gold">
        {s.title}
      </Link>
      <p className="mt-2 line-clamp-3 font-serif text-sm italic text-ink-soft">“{s.premise}”</p>
      <div className="mt-auto flex items-center gap-3 pt-5 text-xs text-ink-mute">
        <span className="truncate">{mine ? "You" : s.owner_name} · {timeAgo(s.updated_at)}</span>
        <span className="ml-auto flex items-center gap-1" title={`${s.head_op_seq} edits`}>✎ {s.head_op_seq}</span>
        <button onClick={star} className="relative flex items-center gap-1 rounded-md px-1.5 py-1 hover:text-gold" aria-label="Star">
          <motion.span key={burst} animate={burst ? { scale: [1, 1.6, 1], rotate: [0, 20, 0] } : {}}>
            <Star className="size-3.5" />
          </motion.span>
          {s.stars}
        </button>
        <button onClick={() => onFork(s)} className="flex items-center gap-1 rounded-md px-1.5 py-1 hover:text-sage" aria-label="Fork">
          <GitFork className="size-3.5" /> {s.forks}
        </button>
      </div>
    </motion.article>
  );
}
