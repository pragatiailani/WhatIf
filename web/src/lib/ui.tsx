// Small shared UI state: theme toggle and toasts.

import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, Info, TriangleAlert } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

// --- theme ---------------------------------------------------------------

export type Theme = "dark" | "light";
const THEME_KEY = "whatif.theme";

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      return (localStorage.getItem(THEME_KEY) as Theme) || "dark";
    } catch {
      return "dark";
    }
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* ignore */
    }
  }, [theme]);
  return { theme, toggle: () => setTheme((t) => (t === "dark" ? "light" : "dark")) };
}

// --- toasts --------------------------------------------------------------

type Tone = "info" | "success" | "error";
interface Toast {
  id: number;
  tone: Tone;
  text: string;
}

const ToastCtx = createContext<(text: string, tone?: Tone) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: string, tone: Tone = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, tone, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4200);
  }, []);
  const Icon = { info: Info, success: CheckCircle2, error: TriangleAlert };
  const color = { info: "text-violet", success: "text-sage", error: "text-rose" };
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-4 left-1/2 z-[100] flex w-[min(92vw,420px)] -translate-x-1/2 flex-col gap-2">
        <AnimatePresence>
          {toasts.map((t) => {
            const I = Icon[t.tone];
            return (
              <motion.div
                key={t.id}
                layout
                initial={{ opacity: 0, y: 24, scale: 0.95 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: 12, scale: 0.97 }}
                className="card pointer-events-auto flex items-start gap-3 px-4 py-3 text-sm"
                role="status"
              >
                <I className={`mt-0.5 size-4 shrink-0 ${color[t.tone]}`} />
                <span className="text-ink">{t.text}</span>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);

export function errorText(e: unknown) {
  return e instanceof Error ? e.message : String(e);
}
