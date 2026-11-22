import Modal from "antd/lib/modal";
import { Auth, currentUser } from "@/services/auth";
import { restoreSession } from "./restoreSession";

jest.mock("antd/lib/modal", () => ({ warning: jest.fn() }));
jest.mock("@/services/auth", () => ({
  Auth: { getLoginUrl: () => "/login", reloadSession: jest.fn() },
  currentUser: { id: 1 },
}));

test("QA: an unrelated origin cannot forge login-popup completion", async () => {
  const popup = { closed: false, close: jest.fn(), focus: jest.fn() };
  const open = jest.spyOn(window, "open").mockReturnValue(popup);
  const closeModal = jest.fn();
  let restored = false;
  const pending = restoreSession().then(() => {
    restored = true;
  });
  Modal.warning.mock.calls[0][0].onOk(closeModal);
  const notify = (origin, source) => {
    const event = new MessageEvent("message", { origin, data: { type: "redash_session_restored", user_id: 1 } });
    Object.defineProperty(event, "source", { value: source });
    window.dispatchEvent(event);
  };
  try {
    notify("https://untrusted-window.invalid", null);
    notify("https://untrusted-window.invalid", popup);
    notify(window.location.origin, null);
    window.dispatchEvent(new MessageEvent("message", { origin: window.location.origin, data: null }));
    await Promise.resolve();
    expect(restored).toBe(false);
    expect(popup.close).not.toHaveBeenCalled();
    expect(closeModal).not.toHaveBeenCalled();
  } finally {
    notify(window.location.origin, popup);
    await pending;
    expect(restored).toBe(true);
    expect(popup.close).toHaveBeenCalledTimes(1);
    expect(closeModal).toHaveBeenCalledTimes(1);
    open.mockRestore();
  }
});

test.each([2, undefined])("QA: changed or missing restored actor %s reloads without replay", async (userId) => {
  Modal.warning.mockClear();
  Auth.reloadSession.mockClear();
  currentUser.id = 1;
  const popup = { closed: false, close: jest.fn(), focus: jest.fn() };
  const open = jest.spyOn(window, "open").mockReturnValue(popup);
  let replayed = false;
  const pending = restoreSession().then(() => {
    replayed = true;
  });
  const rejected = expect(pending).rejects.toThrow("authenticated account changed");
  try {
    Modal.warning.mock.calls[0][0].onOk(jest.fn());
    const event = new MessageEvent("message", {
      origin: window.location.origin,
      data: { type: "redash_session_restored", user_id: userId },
    });
    Object.defineProperty(event, "source", { value: popup });
    window.dispatchEvent(event);
    await rejected;
    expect(replayed).toBe(false);
    expect(Auth.reloadSession).toHaveBeenCalledTimes(1);
  } finally {
    open.mockRestore();
  }
});
