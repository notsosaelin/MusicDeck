/** Toasts, recorded instead of shown. */
export const toasts: { title: string; body: string }[] = [];
export const notify = (title: string, body: string) => {
  toasts.push({ title, body });
};
export const releaseToastHooks = () => {};
