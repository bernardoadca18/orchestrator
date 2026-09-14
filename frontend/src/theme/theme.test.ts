import assert from "node:assert/strict";
import test, { after, beforeEach, describe } from "node:test";

const storedValues = new Map<string, string>();
const storage = {
  get length() { return storedValues.size; },
  clear() { storedValues.clear(); },
  getItem(key: string) { return storedValues.get(key) ?? null; },
  key(index: number) { return [...storedValues.keys()][index] ?? null; },
  removeItem(key: string) { storedValues.delete(key); },
  setItem(key: string, value: string) { storedValues.set(key, value); },
};
const dataset: Record<string, string> = {};
const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
const originalStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
const originalDocument = Object.getOwnPropertyDescriptor(globalThis, "document");

Object.defineProperty(globalThis, "window", { configurable: true, value: { localStorage: storage } });
Object.defineProperty(globalThis, "localStorage", { configurable: true, value: storage });
Object.defineProperty(globalThis, "document", {
  configurable: true,
  value: { documentElement: { dataset } },
});

const { applyTheme, getStoredTheme, initTheme, setStoredTheme } = await import("./theme");

function restoreGlobal(name: "window" | "localStorage" | "document", descriptor?: PropertyDescriptor) {
  if (descriptor) Object.defineProperty(globalThis, name, descriptor);
  else Reflect.deleteProperty(globalThis, name);
}

after(() => {
  restoreGlobal("window", originalWindow);
  restoreGlobal("localStorage", originalStorage);
  restoreGlobal("document", originalDocument);
});

beforeEach(() => {
  storage.clear();
  delete dataset.theme;
});

describe("theme production implementation", () => {
  test("defaults to dark when no valid preference exists", () => {
    assert.equal(getStoredTheme(), "dark");
    storage.setItem("neovids-theme", "invalid");
    assert.equal(getStoredTheme(), "dark");
  });

  test("reads stored dark and light preferences", () => {
    storage.setItem("neovids-theme", "dark");
    assert.equal(getStoredTheme(), "dark");
    storage.setItem("neovids-theme", "light");
    assert.equal(getStoredTheme(), "light");
  });

  test("persists the selected theme", () => {
    setStoredTheme("light");
    assert.equal(storage.getItem("neovids-theme"), "light");
    setStoredTheme("dark");
    assert.equal(storage.getItem("neovids-theme"), "dark");
  });

  test("applies the selected theme to the document root", () => {
    applyTheme("light");
    assert.equal(dataset.theme, "light");
    applyTheme("dark");
    assert.equal(dataset.theme, "dark");
  });

  test("initializes and applies the default dark theme", () => {
    assert.equal(initTheme(), "dark");
    assert.equal(dataset.theme, "dark");
  });

  test("initializes and applies a stored light theme", () => {
    storage.setItem("neovids-theme", "light");
    assert.equal(initTheme(), "light");
    assert.equal(dataset.theme, "light");
  });
});
