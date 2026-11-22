import { useEffect } from "react";
import { axios } from "@/services/axios";
import { Auth, currentUser } from "@/services/auth";

export const PRESENCE_INTERVAL = 30000;
export const INTERACTION_WINDOW = 5 * 60 * 1000;

export function startPresence({ post, document: doc, window: win, storage, now = Date.now, makeId }) {
  // A duplicated browser tab can inherit sessionStorage; each hook lifetime needs its own ID.
  const tabId = makeId();
  try {
    storage?.setItem("bidash-presence-tab", tabId);
  } catch {
    /* Storage is optional. */
  }
  let lastInteraction = null;
  let inFlight = false;
  let stopped = false;
  let idlePending = false;
  const interact = () => {
    lastInteraction = now();
  };
  const send = async (hidden = false) => {
    if (stopped) return;
    if (inFlight) {
      if (hidden) idlePending = true;
      return;
    }
    if (!hidden && doc.hidden) return;
    inFlight = true;
    try {
      await post({
        tab_id: tabId,
        active: !hidden && lastInteraction !== null && now() - lastInteraction <= INTERACTION_WINDOW,
      });
    } catch {
      // Presence is best effort. The monitoring API reports unavailable/stale telemetry.
    } finally {
      inFlight = false;
      if (idlePending) {
        idlePending = false;
        send(doc.hidden);
      }
    }
  };
  const visibility = () => send(doc.hidden);
  const events = ["pointerdown", "keydown", "touchstart", "wheel"];
  events.forEach((event) => {
    win.addEventListener(event, interact, { passive: true });
  });
  doc.addEventListener("visibilitychange", visibility);
  const timer = win.setInterval(() => send(), PRESENCE_INTERVAL);
  send();
  return () => {
    stopped = true;
    win.clearInterval(timer);
    events.forEach((event) => {
      win.removeEventListener(event, interact);
    });
    doc.removeEventListener("visibilitychange", visibility);
  };
}

export default function usePresence() {
  useEffect(() => {
    if (!currentUser.id || Auth.getApiKey()) return undefined;
    let storage;
    try {
      storage = window.sessionStorage;
    } catch {
      storage = null;
    }
    return startPresence({
      post: (body) => axios.post("api/session/presence", body, { timeout: 5000 }),
      document,
      window,
      storage,
      makeId: () =>
        window.crypto.randomUUID
          ? window.crypto.randomUUID()
          : "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (c) =>
              (Number(c) ^ (window.crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (Number(c) / 4)))).toString(16)
            ),
    });
  }, []);
}
