// The access token is kept in localStorage so a reload doesn't sign the user out. When it expires,
// the refresh cookie renews it (see `authFetch` in api/http.ts).

const KEY = "storyline.accessToken";

const load = (): string | null => {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null; // storage blocked: fall back to memory only
  }
};

let accessToken: string | null = load();
const lostListeners = new Set<() => void>();

export const getAccessToken = () => accessToken;
export const setAccessToken = (token: string | null) => {
  accessToken = token;
  try {
    if (token) localStorage.setItem(KEY, token);
    else localStorage.removeItem(KEY);
  } catch {
    /* storage blocked: keep it in memory */
  }
};

/** Called when the session can't be renewed (refresh cookie missing, expired or revoked). */
export function onSessionLost(listener: () => void): () => void {
  lostListeners.add(listener);
  return () => lostListeners.delete(listener);
}
export const notifySessionLost = () => lostListeners.forEach((l) => l());
