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

export const addEventListener = () => {};
export const removeEventListener = () => {};
export const openFilePicker = async () => {
  throw new Error("no file picker in tests");
};
export const routerHook = { addRoute: () => {}, removeRoute: () => {} };
export const toaster = { toast: () => {} };
