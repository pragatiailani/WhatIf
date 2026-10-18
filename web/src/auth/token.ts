// The access token lives only in memory: scripts on the page can't read it from storage, and a
// reload drops it. The session survives reloads through the refresh cookie (see `AuthProvider`).

let accessToken: string | null = null;
const lostListeners = new Set<() => void>();

export const getAccessToken = () => accessToken;
export const setAccessToken = (token: string | null) => {
  accessToken = token;
};

/** Called when the session can't be renewed (refresh cookie missing, expired or revoked). */
export function onSessionLost(listener: () => void): () => void {
  lostListeners.add(listener);
  return () => lostListeners.delete(listener);
}
export const notifySessionLost = () => lostListeners.forEach((l) => l());
