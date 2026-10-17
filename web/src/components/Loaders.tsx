import { motion } from "framer-motion";

/** A quill writing a looping line of ink. Used while the bot is thinking. */
export function QuillLoader({ size = 56 }: { size?: number }) {
  return (
    <svg width={size} height={size * 0.6} viewBox="0 0 100 60" aria-hidden className="overflow-visible">
      <motion.path
        d="M5 45 C 18 30, 26 55, 38 42 S 58 30, 68 44 S 86 50, 95 38"
        fill="none"
        stroke="var(--gold)"
        strokeWidth="2.2"
        strokeLinecap="round"
        initial={{ pathLength: 0, opacity: 0.9 }}
        animate={{ pathLength: [0, 1, 1], opacity: [0.9, 0.9, 0] }}
        transition={{ duration: 2.2, repeat: Infinity, ease: "easeInOut", times: [0, 0.75, 1] }}
      />
      <motion.g
        animate={{ x: [0, 90, 90, 0], y: [0, -6, -6, 0], rotate: [-8, 4, 4, -8] }}
        transition={{ duration: 2.2, repeat: Infinity, ease: "easeInOut", times: [0, 0.75, 0.9, 1] }}
        style={{ originX: "5px", originY: "45px" }}
      >
        {/* feather */}
        <path d="M5 45 L 22 8 C 30 -2, 38 6, 30 14 Z" fill="var(--ink-soft)" opacity="0.85" />
        <path d="M5 45 L 26 10" stroke="var(--bg)" strokeWidth="1" />
      </motion.g>
    </svg>
  );
}

/** A small book whose pages keep turning. Full-page loading state. */
export function BookLoader({ size = 72 }: { size?: number }) {
  return (
    <div className="relative" style={{ width: size, height: size * 0.7, perspective: 400 }} aria-hidden>
      <div className="absolute inset-0 rounded-sm border border-gold/40 bg-surface-2" />
      <div className="absolute inset-y-0 left-1/2 w-px bg-gold/50" />
      {[0, 1, 2].map((i) => (
        <motion.div
          key={i}
          className="absolute inset-y-[6%] left-1/2 w-[46%] rounded-r-sm border border-line bg-surface"
          style={{ transformOrigin: "left center" }}
          animate={{ rotateY: [0, -180] }}
          transition={{ duration: 1.6, repeat: Infinity, delay: i * 0.35, ease: [0.6, 0, 0.4, 1], repeatDelay: 0.4 }}
        >
          <div className="mx-2 mt-2 space-y-1">
            {[80, 60, 70].map((w, j) => (
              <div key={j} className="h-0.5 rounded bg-ink-mute/40" style={{ width: `${w}%` }} />
            ))}
          </div>
        </motion.div>
      ))}
    </div>
  );
}

export function PageLoader({ label = "Turning pages…" }: { label?: string }) {
  return (
    <div className="flex min-h-[50vh] flex-col items-center justify-center gap-5">
      <BookLoader />
      <motion.p
        className="font-display text-lg italic text-ink-soft"
        animate={{ opacity: [0.5, 1, 0.5] }}
        transition={{ duration: 2.4, repeat: Infinity }}
      >
        {label}
      </motion.p>
    </div>
  );
}

/** Three ink drops. Inline "working" indicator for buttons. */
export function InkDots({ className = "" }: { className?: string }) {
  return (
    <span className={`inline-flex items-end gap-1 ${className}`} aria-label="Loading">
      {[0, 1, 2].map((i) => (
        <motion.span
          key={i}
          className="size-1.5 rounded-full bg-current"
          animate={{ y: [0, -5, 0], opacity: [0.4, 1, 0.4] }}
          transition={{ duration: 0.9, repeat: Infinity, delay: i * 0.15 }}
        />
      ))}
    </span>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div className={`relative overflow-hidden rounded-lg bg-surface-2 ${className}`}>
      <motion.div
        className="absolute inset-0 -translate-x-full bg-gradient-to-r from-transparent via-gold/10 to-transparent"
        animate={{ x: ["-100%", "100%"] }}
        transition={{ duration: 1.6, repeat: Infinity, ease: "linear" }}
      />
    </div>
  );
}
