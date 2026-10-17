import { motion } from "framer-motion";
import { ArrowRight, GitFork, MessageCircleQuestion, Network, Sparkles } from "lucide-react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { Page } from "../components/ui";

// Canon path across the top, a what-if branch peeling away from node 2.
const CANON = [[60, 160], [170, 110], [290, 150], [410, 100], [530, 140]] as const;
const BRANCH = [[290, 150], [380, 230], [500, 250], [600, 215]] as const;

function Constellation() {
  const path = (pts: readonly (readonly [number, number])[]) => "M" + pts.map((p) => p.join(" ")).join(" L");
  return (
    <svg viewBox="0 0 660 300" className="h-auto w-full" aria-hidden>
      <motion.path d={path(CANON)} fill="none" stroke="var(--gold)" strokeWidth="1.4" strokeOpacity="0.7" initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: 2.2, ease: "easeInOut" }} />
      <motion.path d={path(BRANCH)} fill="none" stroke="var(--sage)" strokeWidth="1.6" strokeDasharray="5 6" initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: 1.6, delay: 2, ease: "easeInOut" }} />
      {CANON.map(([x, y], i) => (
        <motion.g key={`c${i}`} initial={{ opacity: 0, scale: 0 }} animate={{ opacity: i > 2 ? [0, 1, 0.35] : 1, scale: 1 }} transition={{ delay: 0.3 + i * 0.4, duration: i > 2 ? 3 : 0.5, times: i > 2 ? [0, 0.4, 1] : undefined }} style={{ originX: `${x}px`, originY: `${y}px` }}>
          <circle cx={x} cy={y} r={i === 2 ? 9 : 6} fill="var(--gold)" />
          <circle cx={x} cy={y} r={i === 2 ? 16 : 11} fill="none" stroke="var(--gold)" strokeOpacity="0.3" />
        </motion.g>
      ))}
      {BRANCH.slice(1).map(([x, y], i) => (
        <motion.g key={`b${i}`} initial={{ opacity: 0, scale: 0 }} animate={{ opacity: 1, scale: 1 }} transition={{ delay: 2.4 + i * 0.45, type: "spring" }} style={{ originX: `${x}px`, originY: `${y}px` }}>
          <motion.circle cx={x} cy={y} fill="none" stroke="var(--sage)" initial={{ r: 8, opacity: 0 }} animate={{ r: [8, 22], opacity: [0.6, 0] }} transition={{ duration: 2, repeat: Infinity, delay: 3 + i * 0.3 }} />
          <circle cx={x} cy={y} r={7} fill="var(--sage)" />
        </motion.g>
      ))}
      <motion.text x="214" y="215" className="fill-ink-soft font-display text-[17px] italic" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 2.2 }}>
        what if…?
      </motion.text>
      <motion.text x="540" y="128" className="fill-ink-mute font-sans text-[11px]" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 3.6 }}>
        canon
      </motion.text>
      <motion.text x="600" y="242" textAnchor="middle" className="fill-sage font-sans text-[11px]" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 3.8 }}>
        your storyline
      </motion.text>
    </svg>
  );
}

const FEATURES = [
  { icon: MessageCircleQuestion, title: "Ask the book", text: "Grounded answers with chapter and page citations, for small questions or the big picture." },
  { icon: Sparkles, title: "Rewrite the plot", text: "Say “what if…” and the story branches. Events that no longer hold fade away and new ones are written in." },
  { icon: Network, title: "See why", text: "Every answer lights up the path it took through the story graph. Nothing is hidden behind the curtain." },
  { icon: GitFork, title: "Fork & share", text: "Publish your storylines, explore other readers' worlds, and branch any of them into your own." },
];

export default function Landing() {
  const { user } = useAuth();
  return (
    <Page className="max-w-6xl">
      <section className="grid items-center gap-10 pt-6 md:grid-cols-[1.1fr_1fr] md:pt-14">
        <div>
          <motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mb-4 font-display text-lg italic text-ink-soft">
            A library where every book has another ending
          </motion.p>
          <motion.h1 initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }} className="font-display text-5xl font-semibold leading-[1.05] sm:text-6xl">
            What if the story <span className="gilded italic">went another way?</span>
          </motion.h1>
          <motion.p initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }} className="mt-5 max-w-lg font-serif text-lg text-ink-soft">
            Open a book, ask it anything, then change one moment and watch the whole tale rearrange itself, character by character, in a living map of the story.
          </motion.p>
          <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 }} className="mt-8 flex flex-wrap gap-3">
            <Link to="/library" className="group inline-flex items-center gap-2 rounded-xl bg-gold px-5 py-3 font-medium text-[#1b1508] shadow-[0_8px_30px_-10px_var(--gold)] transition hover:brightness-110">
              Enter the library <ArrowRight className="size-4 transition group-hover:translate-x-1" />
            </Link>
            {!user && (
              <Link to="/signup" className="inline-flex items-center gap-2 rounded-xl border border-line px-5 py-3 text-ink hover:border-gold/60">
                Create an account
              </Link>
            )}
          </motion.div>
        </div>
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.2 }} className="card relative p-4">
          <Constellation />
        </motion.div>
      </section>

      <div className="flourish my-20 font-display text-xl">✦</div>

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {FEATURES.map((f, i) => (
          <motion.div
            key={f.title}
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ delay: i * 0.08 }}
            whileHover={{ y: -4 }}
            className="card p-5"
          >
            <div className="mb-4 grid size-10 place-items-center rounded-xl border border-gold/30 bg-gold-soft text-gold">
              <f.icon className="size-5" />
            </div>
            <h3 className="font-display text-xl font-semibold">{f.title}</h3>
            <p className="mt-2 text-sm leading-relaxed text-ink-soft">{f.text}</p>
          </motion.div>
        ))}
      </section>
    </Page>
  );
}
