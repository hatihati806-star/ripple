import { createReadStream, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { defineConfig, type Plugin } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

/**
 * MapLibre GL 6 resolves its web worker at runtime:
 *
 *   let e = import.meta.url
 *   let t = e.endsWith('-dev.mjs') ? 'maplibre-gl-worker-dev.mjs' : 'maplibre-gl-worker.mjs'
 *   return new URL(`./${t}`, e).href
 *
 * Because that path is constructed dynamically, bundlers cannot see it and never emit the
 * worker -- nor its `maplibre-gl-shared.mjs` sibling, which the worker imports relatively.
 * The result is silent: the worker request 404s into the SPA fallback (returning HTML with
 * a 200), the style never finishes loading, `load` never fires, and the map renders an
 * empty canvas with no console error.
 *
 * This plugin serves the pair at a fixed path in dev and emits them into the build, and the
 * app points MapLibre at that path with `setWorkerUrl`.
 */
const WORKER_FILES = ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"] as const;
const WORKER_ROUTE = "/maplibre";

function maplibreWorker(): Plugin {
  const require = createRequire(import.meta.url);
  const distDir = path.join(path.dirname(require.resolve("maplibre-gl/package.json")), "dist");

  return {
    name: "ripple:maplibre-worker",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = (req as { url?: string }).url ?? "";
        const name = WORKER_FILES.find((file) => url.endsWith(`/${file}`));
        if (!name) return next();
        res.setHeader("Content-Type", "text/javascript; charset=utf-8");
        createReadStream(path.join(distDir, name)).pipe(res);
      });
    },
    generateBundle() {
      for (const file of WORKER_FILES) {
        this.emitFile({
          type: "asset",
          fileName: `${WORKER_ROUTE.slice(1)}/${file}`,
          source: readFileSync(path.join(distDir, file)),
        });
      }
    },
  };
}

/**
 * Serve the assistant function in `vite dev`.
 *
 * The endpoint is a Vercel function (`api/assistant.ts`) and the browser calls `/api/assistant`
 * in production. Without this, `npm run dev` would 404 that path and the panel would look
 * broken locally while working deployed — the exact class of difference that hides bugs until
 * a deploy. The handler is loaded through Vite's SSR module runner and called with a real
 * `Request`, so dev and production execute the same code, not two implementations of it.
 */
function assistantApi(): Plugin {
  return {
    name: "ripple:assistant-api",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = req.url ?? "";
        if (!url.startsWith("/api/assistant")) return next();
        // Connect's req/res *are* the Node objects the handler takes, so dev calls the same
        // function the deploy does -- body and all -- rather than a second implementation.
        void (async () => {
          try {
            const module = await server.ssrLoadModule("/api/assistant.ts");
            await module.default(req, res);
          } catch (error) {
            if (!res.headersSent) res.statusCode = 500;
            res.end(JSON.stringify({ error: `dev assistant failed: ${String(error)}` }));
          }
        })();
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), maplibreWorker(), assistantApi()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
