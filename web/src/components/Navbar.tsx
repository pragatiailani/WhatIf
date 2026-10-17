import { AnimatePresence, motion } from "framer-motion";
import { BookOpen, Compass, LoaderCircle, LogOut, Menu, Moon, Sun, Upload, Library as LibraryIcon, X } from "lucide-react";
import { useState } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { isMock } from "../api/client";
import { useIngestion } from "../lib/ingestion";
import { useTheme } from "../lib/ui";

const LINKS = [
  { to: "/library", label: "Library", icon: LibraryIcon },
  { to: "/explore", label: "Storylines", icon: Compass },
  { to: "/shelf", label: "My Shelf", icon: BookOpen, auth: true },
  { to: "/upload", label: "Upload", icon: Upload, auth: true },
];

export function Logo() {
  return (
    <Link to="/" className="group flex items-center gap-2">
      <motion.svg width="28" height="28" viewBox="0 0 32 32" whileHover={{ rotate: -8 }} aria-hidden>
        <path d="M6 7 Q16 3 16 9 Q16 3 26 7 V25 Q16 21 16 27 Q16 21 6 25 Z" fill="none" stroke="var(--gold)" strokeWidth="1.6" strokeLinejoin="round" />
        <path d="M16 9 V27" stroke="var(--gold)" strokeWidth="1.2" />
        <motion.circle cx="24" cy="5" r="1.6" fill="var(--gold)" animate={{ opacity: [0.2, 1, 0.2] }} transition={{ duration: 2.4, repeat: Infinity }} />
      </motion.svg>
      <span className="font-display text-2xl font-semibold tracking-wide">
        What<span className="text-gold">If?</span>
      </span>
    </Link>
  );
}

export function Navbar() {
  const { user, signOut } = useAuth();
  const { theme, toggle } = useTheme();
  const [open, setOpen] = useState(false);
  const nav = useNavigate();
  const links = LINKS.filter((l) => !l.auth || user);
  const { active } = useIngestion();
  const mapping = `Mapping ${active.length} book${active.length > 1 ? "s" : ""}`;

  return (
    <header className="sticky top-0 z-40 border-b border-line/60 bg-bg/75 backdrop-blur-md">
      <div className="mx-auto flex h-16 max-w-6xl items-center gap-6 px-4 sm:px-6">
        <Logo />
        <nav className="hidden items-center gap-1 md:flex">
          {links.map((l) => (
            <NavLink key={l.to} to={l.to} className={({ isActive }) => `relative rounded-lg px-3 py-2 text-sm transition-colors ${isActive ? "text-ink" : "text-ink-soft hover:text-ink"}`}>
              {({ isActive }) => (
                <>
                  {l.label}
                  {isActive && <motion.span layoutId="nav-underline" className="absolute inset-x-3 -bottom-[13px] h-px bg-gold" />}
                </>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          {isMock && <span className="hidden rounded-full border border-line px-2 py-0.5 text-[10px] uppercase tracking-wider text-ink-mute lg:inline">demo data</span>}
          {active.length > 0 && (
            <Link
              to={active.length === 1 ? `/books/${active[0].book.id}` : "/shelf"}
              title={active.map((w) => `${w.book.title}: ${w.progress?.detail ?? "Starting…"}`).join("\n")}
              aria-label={mapping}
              className="flex items-center gap-1.5 rounded-full border border-gold/30 bg-gold-soft px-2.5 py-1 text-xs text-gold hover:border-gold/60"
            >
              <LoaderCircle className="size-3.5 animate-spin" aria-hidden />
              <span className="hidden sm:inline">{mapping}</span>
              <span className="sm:hidden">{active.length}</span>
            </Link>
          )}
          <button onClick={toggle} className="rounded-lg p-2 text-ink-soft transition hover:bg-surface-2 hover:text-gold" aria-label="Toggle theme">
            <AnimatePresence mode="wait" initial={false}>
              <motion.span key={theme} initial={{ rotate: -90, opacity: 0 }} animate={{ rotate: 0, opacity: 1 }} exit={{ rotate: 90, opacity: 0 }} className="block">
                {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
              </motion.span>
            </AnimatePresence>
          </button>
          {user ? (
            <div className="hidden items-center gap-2 md:flex">
              <div className="grid size-8 place-items-center rounded-full border border-gold/40 bg-gold-soft font-display text-sm font-semibold text-gold" title={user.email}>
                {user.name.charAt(0).toUpperCase()}
              </div>
              <button onClick={() => (signOut(), nav("/"))} className="rounded-lg p-2 text-ink-soft hover:text-ink" aria-label="Sign out">
                <LogOut className="size-4" />
              </button>
            </div>
          ) : (
            <div className="hidden items-center gap-2 md:flex">
              <Link to="/signin" className="rounded-lg px-3 py-2 text-sm text-ink-soft hover:text-ink">Sign in</Link>
              <Link to="/signup" className="rounded-xl bg-gold px-4 py-2 text-sm font-medium text-[#1b1508] hover:brightness-110">Begin</Link>
            </div>
          )}
          <button className="rounded-lg p-2 md:hidden" onClick={() => setOpen((o) => !o)} aria-label="Menu">
            {open ? <X className="size-5" /> : <Menu className="size-5" />}
          </button>
        </div>
      </div>
      <AnimatePresence>
        {open && (
          <motion.nav initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden border-t border-line/60 md:hidden">
            <div className="flex flex-col gap-1 p-3">
              {links.map((l) => (
                <NavLink key={l.to} to={l.to} onClick={() => setOpen(false)} className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-ink-soft hover:bg-surface-2 hover:text-ink">
                  <l.icon className="size-4 text-gold" /> {l.label}
                </NavLink>
              ))}
              {user ? (
                <button onClick={() => (signOut(), setOpen(false), nav("/"))} className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-left text-ink-soft hover:bg-surface-2">
                  <LogOut className="size-4" /> Sign out ({user.name})
                </button>
              ) : (
                <div className="flex gap-2 p-1">
                  <Link to="/signin" onClick={() => setOpen(false)} className="flex-1 rounded-xl border border-line py-2 text-center text-sm">Sign in</Link>
                  <Link to="/signup" onClick={() => setOpen(false)} className="flex-1 rounded-xl bg-gold py-2 text-center text-sm font-medium text-[#1b1508]">Begin</Link>
                </div>
              )}
            </div>
          </motion.nav>
        )}
      </AnimatePresence>
    </header>
  );
}
