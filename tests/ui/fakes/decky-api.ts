/**
 * @decky/api without Decky: `callable(name)` calls whatever the test put in `backend[name]`, and
 * records every call. Anything not set answers undefined.
 */
export const backend: Record<string, (...args: any[]) => any> = {};
export const calls: { name: string; args: any[] }[] = [];

export const callable =
  (name: string) =>
  async (...args: any[]) => {
    calls.push({ name, args });
    return backend[name]?.(...args);
  };

/** Backend events: tests send one with `fire(name, ...args)`. */
const listeners = new Map<string, Set<(...args: any[]) => void>>();
export const addEventListener = (name: string, handler: (...args: any[]) => void) => {
  if (!listeners.has(name)) listeners.set(name, new Set());
  listeners.get(name)!.add(handler);
  return handler;
};
export const removeEventListener = (name: string, handler: (...args: any[]) => void) => {
  listeners.get(name)?.delete(handler);
};
export const fire = (name: string, ...args: any[]) => listeners.get(name)?.forEach((h) => h(...args));
export const openFilePicker = async () => {
  throw new Error("no file picker in tests");
};
export const routerHook = { addRoute: () => {}, removeRoute: () => {} };
export const toaster = { toast: () => {} };
