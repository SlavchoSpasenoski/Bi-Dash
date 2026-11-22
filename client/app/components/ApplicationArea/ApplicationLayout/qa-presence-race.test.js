import { startPresence, PRESENCE_INTERVAL } from "./usePresence";
jest.mock("@/services/axios", () => ({ axios: { post: jest.fn() } }));
jest.mock("@/services/auth", () => ({ Auth: { getApiKey: () => null }, currentUser: { id: 1 } }));
test("QA: hiding during an active heartbeat flushes the idle transition after completion", async () => {
  jest.useFakeTimers();
  let hidden = false;
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  let finish;
  const post = jest.fn().mockResolvedValue({});
  const cleanup = startPresence({ post, document, window, storage: null, makeId: () => "qa-tab" });
  try {
    await Promise.resolve();
    window.dispatchEvent(new Event("keydown"));
    post.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        })
    );
    jest.advanceTimersByTime(PRESENCE_INTERVAL);
    expect(post).toHaveBeenLastCalledWith(expect.objectContaining({ active: true }));
    hidden = true;
    document.dispatchEvent(new Event("visibilitychange"));
    finish({});
    await Promise.resolve();
    await Promise.resolve();
    expect(post).toHaveBeenLastCalledWith(expect.objectContaining({ active: false }));
  } finally {
    cleanup();
    jest.useRealTimers();
  }
});
