import { AnimatePresence, motion } from "framer-motion";
import { X } from "lucide-react";
import { useEffect, type ButtonHTMLAttributes, type ReactNode } from "react";
import { InkDots } from "./Loaders";

type Variant = "gold" | "ghost" | "outline" | "danger";

const VARIANTS: Record<Variant, string> = {
  gold: "bg-gold text-[#1b1508] hover:brightness-110 shadow-[0_6px_20px_-8px_var(--gold)]",
  ghost: "text-ink-soft hover:text-ink hover:bg-surface-2",
  outline: "border border-line text-ink hover:border-gold/60 hover:bg-gold-soft",
  danger: "border border-rose/40 text-rose hover:bg-rose/10",
};

export function Button({
  variant = "gold",
  loading,
  className = "",
  children,
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; loading?: boolean }) {
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      className={`inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition-all duration-200 active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-60 ${VARIANTS[variant]} ${className}`}
    >
      {loading ? <InkDots /> : children}
    </button>
  );
}

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title?: ReactNode; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  return (
    <AnimatePresence>
      {open && (
        <motion.div className="fixed inset-0 z-50 flex items-center justify-center p-4" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          <div className="absolute inset-0 bg-black/55 backdrop-blur-sm" onClick={onClose} />
          <motion.div
            role="dialog"
            aria-modal
            className={`card relative w-full ${wide ? "max-w-2xl" : "max-w-md"} max-h-[88vh] overflow-y-auto p-6`}
            initial={{ y: 24, scale: 0.96, opacity: 0 }}
            animate={{ y: 0, scale: 1, opacity: 1 }}
            exit={{ y: 12, scale: 0.98, opacity: 0 }}
            transition={{ type: "spring", stiffness: 320, damping: 28 }}
          >
            <button onClick={onClose} className="absolute right-4 top-4 rounded-lg p-1 text-ink-mute hover:text-ink" aria-label="Close">
              <X className="size-4" />
            </button>
            {title && <h2 className="mb-4 pr-8 font-display text-2xl font-semibold">{title}</h2>}
            {children}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function Badge({ children, tone = "gold" }: { children: ReactNode; tone?: "gold" | "sage" | "rose" | "violet" | "amber" | "mute" }) {
  const map = {
    gold: "text-gold bg-gold/10 border-gold/25",
    sage: "text-sage bg-sage/10 border-sage/25",
    rose: "text-rose bg-rose/10 border-rose/25",
    violet: "text-violet bg-violet/10 border-violet/25",
    amber: "text-amber bg-amber/10 border-amber/25",
    mute: "text-ink-mute bg-surface-2 border-line",
  };
  return <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium ${map[tone]}`}>{children}</span>;
}

export function Empty({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="flex flex-col items-center gap-3 py-16 text-center">
      <motion.div animate={{ y: [0, -6, 0] }} transition={{ duration: 3.5, repeat: Infinity, ease: "easeInOut" }} className="text-gold/80">
        {icon}
      </motion.div>
      <h3 className="font-display text-2xl">{title}</h3>
      <div className="max-w-sm text-sm text-ink-soft">{children}</div>
    </motion.div>
  );
}

/** Fade-and-rise wrapper for page content. */
export function Page({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <motion.main
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
      className={`mx-auto w-full max-w-6xl px-4 pb-20 pt-8 sm:px-6 ${className}`}
    >
      {children}
    </motion.main>
  );
}

export function timeAgo(iso: string) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  const units: [number, string][] = [[60, "minute"], [3600, "hour"], [86400, "day"], [2592000, "month"], [31536000, "year"]];
  let [div, name] = units[0];
  for (const u of units) if (s >= u[0]) [div, name] = u;
  const n = Math.floor(s / div);
  return `${n} ${name}${n > 1 ? "s" : ""} ago`;
}
