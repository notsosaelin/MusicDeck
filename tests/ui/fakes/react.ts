/** React stand-in for modules that export hooks next to their logic; the tests don't render. */
export const useSyncExternalStore = (_subscribe: unknown, get: () => unknown) => get();
export const useState = (initial: unknown) => [initial, () => {}];
export const useEffect = () => {};
export const useRef = (value: unknown) => ({ current: value });
