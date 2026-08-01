import { beforeEach } from "vitest";

// Node 22 exposes an experimental global `localStorage`. When the process was
// not started with a valid --localstorage-file it exists but has no Storage
// methods, and can shadow jsdom's implementation. Keep frontend tests
// deterministic with a small standards-shaped in-memory store.
const values = new Map<string, string>();
const memoryStorage: Storage = {
  get length() {
    return values.size;
  },
  clear() {
    values.clear();
  },
  getItem(key: string) {
    return values.has(String(key)) ? values.get(String(key))! : null;
  },
  key(index: number) {
    return [...values.keys()][index] ?? null;
  },
  removeItem(key: string) {
    values.delete(String(key));
  },
  setItem(key: string, value: string) {
    values.set(String(key), String(value));
  },
};

Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: memoryStorage,
});
Object.defineProperty(window, "localStorage", {
  configurable: true,
  value: memoryStorage,
});

beforeEach(() => memoryStorage.clear());
