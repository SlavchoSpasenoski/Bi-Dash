import React from "react";
import { act, fireEvent, render } from "@testing-library/react";
import { helpTriggerWithTypes } from "./HelpTrigger";

jest.mock("@/components/Tooltip", () => ({ children }) => <>{children}</>);
jest.mock("@/components/Link", () => ({ children, ...props }) => <a {...props}>{children}</a>);
jest.mock("@/components/DynamicComponent", () => ({
  __esModule: true,
  default: () => null,
  registerComponent: jest.fn(),
}));
jest.mock(
  "antd/lib/drawer",
  () =>
    ({ visible, children }) =>
      visible ? <div>{children}</div> : null
);
const Help = helpTriggerWithTypes({ A: ["https://redash.io/help/a", "A"], B: ["https://redash.io/help/b", "B"] }, [
  "https://redash.io",
]);

function notify(origin, url, source = null) {
  const event = new MessageEvent("message", { origin, data: { type: "iframe_url", message: url } });
  Object.defineProperty(event, "source", { value: source });
  act(() => window.dispatchEvent(event));
}

test("QA: a lookalike origin cannot replace help navigation", () => {
  const { container } = render(<Help type="A">Help A</Help>);
  notify("https://redash.io.attacker.invalid", "https://attacker.invalid/phishing");
  expect(container.querySelector(".help-trigger").getAttribute("href")).toBe("https://redash.io/help/a");
});

test("QA: a help frame updates only the HelpTrigger that owns that frame", () => {
  jest.useFakeTimers();
  try {
    const { container } = render(
      <>
        <Help type="A">Help A</Help>
        <Help type="B">Help B</Help>
      </>
    );
    const links = container.querySelectorAll(".help-trigger");
    fireEvent.click(links[0]);
    act(() => jest.advanceTimersByTime(300));
    const iframe = container.querySelector("iframe");
    notify("https://redash.io", "https://redash.io/help/other", iframe.contentWindow);
    expect(links[0].getAttribute("href")).toBe("https://redash.io/help/other");
    expect(links[1].getAttribute("href")).toBe("https://redash.io/help/b");
  } finally {
    jest.useRealTimers();
  }
});

test.each(["https://redash.io.attacker.invalid/help", "javascript:alert(1)", "https://attacker.invalid/help"])(
  "QA: owning frame cannot supply an unsafe help URL %s",
  (url) => {
    jest.useFakeTimers();
    try {
      const { container } = render(<Help type="A">Help A</Help>);
      fireEvent.click(container.querySelector(".help-trigger"));
      act(() => jest.advanceTimersByTime(300));
      const source = container.querySelector("iframe").contentWindow;
      notify("https://redash.io.attacker.invalid", "https://redash.io/help/spoof", source);
      expect(container.querySelector(".help-trigger").getAttribute("href")).toBe("https://redash.io/help/a");
      notify("https://redash.io", url, source);
      expect(container.querySelector(".help-trigger").getAttribute("href")).toBe("https://redash.io/help/a");
    } finally {
      jest.useRealTimers();
    }
  }
);
