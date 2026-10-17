import { AnimatePresence, motion } from "framer-motion";
import { Eye, EyeOff, KeyRound, Mail, User as UserIcon } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { BookCover } from "../components/BookCover";
import { Button, Page } from "../components/ui";
import { errorText, useToast } from "../lib/ui";

export default function AuthPage({ mode }: { mode: "signin" | "signup" }) {
  const { user, signIn, signUp } = useAuth();
  const [params] = useSearchParams();
  const next = params.get("next") || "/library";
  const nav = useNavigate();
  const toast = useToast();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [shake, setShake] = useState(0);

  if (user) return <Navigate to={next} replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (mode === "signup") await signUp(name, email, password);
      else await signIn(email, password);
      toast(mode === "signup" ? `Welcome to the library, ${name.split(" ")[0]}.` : "Welcome back.", "success");
      nav(next, { replace: true });
    } catch (err) {
      setError(errorText(err));
      setShake((s) => s + 1);
    } finally {
      setBusy(false);
    }
  };

  const signup = mode === "signup";
  const strength = Math.min(4, [/.{8,}/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/].filter((r) => r.test(password)).length);

  return (
    <Page className="flex max-w-5xl items-center">
      <div className="card grid w-full overflow-hidden md:grid-cols-2">
        {/* illustration */}
        <div className="relative hidden items-center justify-center overflow-hidden border-r border-line bg-bg-2 p-10 md:flex">
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_40%,var(--gold-soft),transparent_65%)]" />
          <motion.div className="relative" animate={{ y: [0, -10, 0], rotate: [-2, 1, -2] }} transition={{ duration: 6, repeat: Infinity, ease: "easeInOut" }}>
            <BookCover book={{ title: "Your Story", author: "begins here", cover_hue: signup ? 150 : 250 }} size="lg" />
          </motion.div>
          {["✦", "❦", "✧", "☾", "✶"].map((g, i) => (
            <motion.span
              key={i}
              className="absolute font-display text-2xl text-gold/50"
              style={{ left: `${15 + i * 17}%`, top: `${20 + ((i * 37) % 60)}%` }}
              animate={{ y: [0, -18, 0], opacity: [0.2, 0.7, 0.2] }}
              transition={{ duration: 4 + i, repeat: Infinity, delay: i * 0.6 }}
            >
              {g}
            </motion.span>
          ))}
        </div>

        {/* form */}
        <div className="p-8 sm:p-10">
          <AnimatePresence mode="wait">
            <motion.div key={mode} initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -16 }}>
              <h1 className="font-display text-4xl font-semibold">{signup ? "Join the library" : "Welcome back"}</h1>
              <p className="mt-2 text-sm text-ink-soft">{signup ? "Your storylines, uploads and forks, all in one place." : "Your storylines are right where you left them."}</p>

              <motion.form onSubmit={submit} className="mt-8 space-y-4" animate={shake ? { x: [0, -10, 9, -6, 4, 0] } : {}} key={shake} transition={{ duration: 0.4 }}>
                {signup && <Field icon={UserIcon} label="Name"><input className="input pl-10" value={name} onChange={(e) => setName(e.target.value)} placeholder="Dorothy Gale" autoComplete="name" required /></Field>}
                <Field icon={Mail} label="Email"><input className="input pl-10" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" autoComplete="email" required /></Field>
                <Field icon={KeyRound} label="Password">
                  <input className="input pl-10 pr-10" type={show ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)} placeholder={signup ? "At least 8 characters" : "••••••••"} autoComplete={signup ? "new-password" : "current-password"} required />
                  <button type="button" onClick={() => setShow((s) => !s)} className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-mute hover:text-ink" aria-label={show ? "Hide password" : "Show password"}>
                    {show ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                  </button>
                </Field>
                {signup && password && (
                  <div className="flex gap-1" aria-label={`Password strength ${strength} of 4`}>
                    {[0, 1, 2, 3].map((i) => (
                      <motion.div key={i} className="h-1 flex-1 rounded-full" animate={{ backgroundColor: i < strength ? ["var(--rose)", "var(--amber)", "var(--gold)", "var(--sage)"][strength - 1] : "var(--line)" }} />
                    ))}
                  </div>
                )}
                <AnimatePresence>
                  {error && (
                    <motion.p initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="text-sm text-rose" role="alert">
                      {error}
                    </motion.p>
                  )}
                </AnimatePresence>
                <Button type="submit" loading={busy} className="w-full py-3">
                  {signup ? "Create account" : "Sign in"}
                </Button>
              </motion.form>

              <p className="mt-6 text-center text-sm text-ink-soft">
                {signup ? "Already have a library card? " : "New here? "}
                <Link to={`${signup ? "/signin" : "/signup"}${params.get("next") ? `?next=${encodeURIComponent(next)}` : ""}`} className="text-gold hover:underline">
                  {signup ? "Sign in" : "Create an account"}
                </Link>
              </p>
            </motion.div>
          </AnimatePresence>
        </div>
      </div>
    </Page>
  );
}

function Field({ icon: I, label, children }: { icon: typeof Mail; label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-ink-mute">{label}</span>
      <span className="relative block">
        <I className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ink-mute" />
        {children}
      </span>
    </label>
  );
}
