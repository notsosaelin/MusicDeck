// Node module hooks (module.registerHooks) for the UI tests: compiles the frontend's TypeScript with the project's own
// compiler, resolves extensionless imports the way the bundler does, and puts stand-ins (tests/ui/fakes)
// in place of what only exists inside Steam: Decky's packages, hls.js, toasts.
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createRequire } from "node:module";
import path from "node:path";

const ts = createRequire(import.meta.url)("typescript");
const here = path.dirname(fileURLToPath(import.meta.url));
const fake = (name) => pathToFileURL(path.join(here, "fakes", name)).href;

// packages that need Steam, by import specifier
const PACKAGES = {
  "@decky/api": fake("decky-api.ts"),
  "@decky/ui": fake("decky-ui.ts"),
  "hls.js/dist/hls.light.min.js": fake("hls.ts"),
  react: fake("react.ts"),
};
// frontend modules that draw Steam UI, by path under src/
const MODULES = { "notify.tsx": fake("notify.ts") };

export function resolve(specifier, context, next) {
  if (PACKAGES[specifier]) return { url: PACKAGES[specifier], shortCircuit: true };
  if ((specifier.startsWith(".") || specifier.startsWith("/")) && context.parentURL?.startsWith("file:")) {
    const base = path.resolve(path.dirname(fileURLToPath(context.parentURL)), specifier);
    for (const candidate of [base, `${base}.ts`, `${base}.tsx`, path.join(base, "index.ts")]) {
      if (path.extname(candidate) && existsSync(candidate)) {
        const relative = path.relative(path.join(here, "..", "..", "src"), candidate).replaceAll("\\", "/");
        return { url: MODULES[relative] ?? pathToFileURL(candidate).href, shortCircuit: true };
      }
    }
  }
  return next(specifier, context);
}

export function load(url, context, next) {
  if (!/\.tsx?$/.test(url)) return next(url, context);
  const source = readFileSync(fileURLToPath(url), "utf8");
  const { outputText } = ts.transpileModule(source, {
    fileName: fileURLToPath(url),
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
      esModuleInterop: true,
    },
  });
  return { format: "module", source: outputText, shortCircuit: true };
}
