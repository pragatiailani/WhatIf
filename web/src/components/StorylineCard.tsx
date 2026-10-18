import { motion } from "framer-motion";
import { Lock, Star } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Storyline } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { errorText, useToast } from "../lib/ui";
import { Badge, timeAgo } from "./ui";

export function StorylineCard({ s, bookTitle, onChange, index = 0 }: { s: Storyline; bookTitle?: string; onChange?: (s: Storyline) => void; index?: number }) {
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
      </div>
    </motion.article>
  );
}
