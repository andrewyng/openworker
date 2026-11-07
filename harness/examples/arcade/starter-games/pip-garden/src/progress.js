import { snapshot, restore } from "./journey.js";
const SLOT = "adventure",
  VERSION = 1;
export function progressStore(api = globalThis.arcadeSave) {
  let queue = Promise.resolve();
  const ordered = (fn) => {
    const p = queue.then(fn);
    queue = p.catch(() => {});
    return p;
  };
  return {
    available: !!api,
    load: () =>
      !api
        ? Promise.resolve(null)
        : ordered(async () => {
            const r = await api.load(SLOT, VERSION);
            return r ? restore(r.value) : null;
          }),
    save: (s) => {
      const value = snapshot(s);
      return !api
        ? Promise.resolve(false)
        : ordered(async () => {
            await api.save(SLOT, VERSION, value);
            return true;
          });
    },
    reset: () => (!api ? Promise.resolve() : ordered(() => api.remove(SLOT))),
  };
}
