import { motion, useReducedMotion } from "framer-motion";
import { useMemo } from "react";

/** Slow-drifting motes of light behind the page. Kept sparse and dim on purpose. */
export function Ambient({ count = 18 }: { count?: number }) {
  const reduce = useReducedMotion();
  const motes = useMemo(
    () =>
      Array.from({ length: count }, (_, i) => ({
        id: i,
        left: Math.random() * 100,
        top: Math.random() * 100,
        size: 2 + Math.random() * 3,
        dur: 14 + Math.random() * 16,
        delay: -Math.random() * 20,
        drift: (Math.random() - 0.5) * 60,
      })),
    [count],
  );
  if (reduce) return null;
  return (
    <div className="pointer-events-none fixed inset-0 -z-10 overflow-hidden" aria-hidden>
      {motes.map((m) => (
        <motion.span
          key={m.id}
          className="absolute rounded-full bg-gold"
          style={{ left: `${m.left}%`, top: `${m.top}%`, width: m.size, height: m.size, boxShadow: "0 0 8px var(--gold)" }}
          animate={{ y: [0, -120, 0], x: [0, m.drift, 0], opacity: [0, 0.35, 0] }}
          transition={{ duration: m.dur, repeat: Infinity, delay: m.delay, ease: "easeInOut" }}
        />
      ))}
    </div>
  );
}
