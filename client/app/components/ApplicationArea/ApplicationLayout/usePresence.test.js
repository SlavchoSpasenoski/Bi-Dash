import { startPresence, PRESENCE_INTERVAL, INTERACTION_WINDOW } from "./usePresence";

jest.mock("@/services/axios", () => ({ axios: { post: jest.fn() } }));
jest.mock("@/services/auth", () => ({ Auth: { getApiKey: () => null }, currentUser: { id: 1 } }));

let hidden;
let cleanup;
beforeEach(() => {
  jest.useFakeTimers(); hidden = false;
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
});
afterEach(() => { cleanup?.(); cleanup = null; jest.useRealTimers(); });
const settle = () => Promise.resolve().then(() => Promise.resolve());
function start(post = jest.fn().mockResolvedValue({}), now = () => Date.now()) {
  cleanup = startPresence({ post, document, window, storage: { getItem: () => null, setItem: jest.fn() }, now,
    makeId: () => "d1bba412-2e05-4313-9c4b-c22ba63302cb" });
  return post;
}

test("mount alone is idle; a real interaction makes subsequent heartbeats active", async () => {
  const post = start();
  expect(post).toHaveBeenLastCalledWith({ tab_id: "d1bba412-2e05-4313-9c4b-c22ba63302cb", active: false });
  await settle(); window.dispatchEvent(new Event("keydown"));
  jest.advanceTimersByTime(PRESENCE_INTERVAL); await settle();
  expect(post).toHaveBeenLastCalledWith(expect.objectContaining({ active: true }));
  jest.advanceTimersByTime(INTERACTION_WINDOW + PRESENCE_INTERVAL); await settle();
  jest.advanceTimersByTime(PRESENCE_INTERVAL); await settle();
  expect(post).toHaveBeenLastCalledWith(expect.objectContaining({ active: false }));
});

test("hidden tabs send idle once and pause periodic presence; cleanup removes listeners", async () => {
  const post = start(); await settle();
  hidden = true; document.dispatchEvent(new Event("visibilitychange")); await settle();
  expect(post).toHaveBeenCalledTimes(2);
  jest.advanceTimersByTime(PRESENCE_INTERVAL * 3); await settle();
  expect(post).toHaveBeenCalledTimes(2);
  hidden = false; document.dispatchEvent(new Event("visibilitychange")); await settle();
  expect(post).toHaveBeenCalledTimes(3);
  cleanup(); cleanup = null;
  document.dispatchEvent(new Event("visibilitychange")); jest.advanceTimersByTime(PRESENCE_INTERVAL);
  expect(post).toHaveBeenCalledTimes(3);
});

test("does not overlap slow requests or send client identity and timestamps", async () => {
  let resolve;
  const post = start(jest.fn().mockImplementation(() => new Promise(done => { resolve = done; })));
  window.dispatchEvent(new Event("keydown")); jest.advanceTimersByTime(PRESENCE_INTERVAL * 3);
  expect(post).toHaveBeenCalledTimes(1);
  expect(Object.keys(post.mock.calls[0][0]).sort()).toEqual(["active", "tab_id"]);
  resolve(); await settle(); jest.advanceTimersByTime(PRESENCE_INTERVAL);
  expect(post).toHaveBeenCalledTimes(2);
});


test("a cloned sessionStorage tab ID is never reused for another document", async () => {
  const post = jest.fn().mockResolvedValue({});
  const storage = { getItem: jest.fn(() => "cloned-tab-id"), setItem: jest.fn() };
  cleanup = startPresence({ post, document, window, storage, makeId: () => "fresh-document-id" });
  expect(storage.getItem).not.toHaveBeenCalled();
  expect(storage.setItem).toHaveBeenCalledWith("bidash-presence-tab", "fresh-document-id");
  expect(post).toHaveBeenCalledWith({ tab_id: "fresh-document-id", active: false });
  await settle();
});
