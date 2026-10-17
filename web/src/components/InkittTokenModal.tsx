import { ExternalLink, KeyRound } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api/client";
import { errorText } from "../lib/ui";
import { Button, Modal } from "./ui";

const TOKEN_PAGE = "https://inkitt.com/settings?tab=mcp-server";

/** Asks for the user's Inkitt access token. The server checks it with Inkitt and stores it encrypted. */
export function InkittTokenModal({ open, onClose, onSaved }: { open: boolean; onClose: () => void; onSaved: () => void }) {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setToken("");
    setError("");
  }, [open]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!token.trim()) return;
    setBusy(true);
    setError("");
    try {
      await api.setInkittToken(token.trim());
      onSaved();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Connect Inkitt">
      <form onSubmit={submit} className="space-y-4">
        <p className="text-sm text-ink-soft">
          Paste your Inkitt personal access token so we can publish to your account. It is saved encrypted and used only when you ask.
        </p>
        <label className="relative block">
          <KeyRound className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ink-mute" />
          <input
            className="input pl-10"
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="ink_…"
            autoComplete="off"
            spellCheck={false}
            autoFocus
            aria-label="Inkitt access token"
          />
        </label>
        {error && <p className="text-sm text-rose">{error}</p>}
        <div className="flex items-center justify-between gap-3">
          <a href={TOKEN_PAGE} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-ink-mute hover:text-gold">
            Get a token <ExternalLink className="size-3" />
          </a>
          <div className="flex gap-2">
            <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
            <Button type="submit" loading={busy} disabled={!token.trim()}>Connect</Button>
          </div>
        </div>
      </form>
    </Modal>
  );
}
