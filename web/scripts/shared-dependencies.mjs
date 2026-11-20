import { build } from "esbuild";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const root = fileURLToPath(new URL("../", import.meta.url));
export const sharedNames = ["react", "react-dom", "react/jsx-runtime", "@talyx/sdk", "@talyx/vocab"];
export const sharedImports = Object.fromEntries(sharedNames.map((name, i) => [name, `/shared/${i}.js`]));
sharedImports["react-dom/client"] = sharedImports["react-dom"];
sharedImports["react/jsx-dev-runtime"] = sharedImports["react/jsx-runtime"];
export const sharedVersions = {
  react: require("react/package.json").version, "react-dom": require("react-dom/package.json").version,
  "react/jsx-runtime": require("react/package.json").version,
  "@talyx/sdk": require("../../sdk/ui/linkage.package.json").version,
  "@talyx/vocab": require("../../vocabulary/package.json").version,
};
const versionDefines = {
  __TALYX_SDK_VERSION__: JSON.stringify(sharedVersions["@talyx/sdk"]),
  __TALYX_VOCAB_VERSION__: JSON.stringify(sharedVersions["@talyx/vocab"]),
};
const generated = new Map();
export async function sharedAssets(environment = "production") {
  if (generated.has(environment)) return generated.get(environment);
  const pending = (async () => {
    const modules = [
      `import value from "react"; export default value; export const {${Object.keys(require("react")).filter(k => k !== "default").join(",")}} = value;`,
      `import value from "react-dom"; import client from "react-dom/client"; export default value; export const {${Object.keys(require("react-dom")).filter(k => k !== "default" && k !== "createRoot" && k !== "hydrateRoot").join(",")}} = value; export const {createRoot, hydrateRoot} = client;`,
      `import value from "react/jsx-runtime"; export default value; export const {Fragment, jsx, jsxs} = value;`,
      null, null,
    ];
    if (environment === "development") modules.push(`import value from "react/jsx-dev-runtime"; export default value; export const {Fragment, jsxDEV} = value;`);
    const assets = new Map();
    for (let i = 0; i < modules.length; i++) {
      const result = await build({
        absWorkingDir: root, bundle: true, write: false, format: "esm", jsx: "automatic", platform: "browser", minify: environment === "production",
        nodePaths: [fileURLToPath(new URL("../node_modules", import.meta.url))],
        define: { ...versionDefines, "process.env.NODE_ENV": JSON.stringify(environment) },
        external: i < 3 || i === 5 ? ["/shared/0.js"] : ["/shared/0.js", "react-dom", "react-dom/client", "react/jsx-runtime", "react/jsx-dev-runtime"],
        plugins: i === 0 ? [] : [{ name: "shared-react-interop", setup(builder) {
          builder.onResolve({ filter: /^react$/ }, () => ({ path: "react", namespace: "shared-react" }));
          builder.onLoad({ filter: /.*/, namespace: "shared-react" }, () => ({ contents: 'export * from "/shared/0.js"; export {default} from "/shared/0.js";', loader: "js" }));
        } }],
        ...(modules[i] ? { stdin: { contents: modules[i], resolveDir: root } } :
          { entryPoints: [i === 3 ? "../sdk/ui/src/linkage.tsx" : "../vocabulary/ts/index.ts"] }),
      });
      assets.set(`shared/${i}.js`, result.outputFiles[0].text);
    }
    return assets;
  })();
  generated.set(environment, pending);
  return pending;
}
export function sharedDependencies() {
  let serving = false;
  let imports = sharedImports;
  const assets = () => sharedAssets(serving ? "development" : "production");
  return {
    name: "talyx-shared-dependencies",
    enforce: "pre",
    config(_config, env) {
      serving = env.command === "serve" && env.mode !== "test";
      imports = serving ? { ...sharedImports, "react/jsx-dev-runtime": "/shared/5.js" } : sharedImports;
      return {
        define: versionDefines,
        ...(serving ? { resolve: { alias: Object.entries(imports).map(([name, url]) => ({ find: new RegExp(`^${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`), replacement: url })) },
          optimizeDeps: { exclude: Object.keys(sharedImports), esbuildOptions: { plugins: [{ name: "talyx-shared-interop", setup(builder) {
            builder.onResolve({ filter: /^\/shared\/[0-5]\.js$/ }, args => ({ path: args.path, external: true }));
            builder.onResolve({ filter: /^(react|react-dom)(\/.*)?$/ }, args => ({ path: args.path, namespace: "talyx-shared" }));
            builder.onLoad({ filter: /.*/, namespace: "talyx-shared" }, args => {
              const url = imports[args.path];
              if (!url) throw new Error(`Undeclared shared dependency: ${args.path}`);
              return { contents: `export * from "${url}"; export {default} from "${url}";`, loader: "js" };
            });
          } }] } } } : { resolve: { alias: {
            "@talyx/sdk": fileURLToPath(new URL("../../sdk/ui/src/linkage.tsx", import.meta.url)),
            "@talyx/vocab": fileURLToPath(new URL("../../vocabulary/ts/index.ts", import.meta.url)),
          } } }),
      };
    },
    configResolved(config) {
      if (serving) config.optimizeDeps.include = (config.optimizeDeps.include ?? []).filter(name => !(name in sharedImports));
    },
    async buildStart() { await assets(); },
    resolveId(id) {
      if (id === "virtual:talyx-shared-versions") return "\0talyx-versions";
      if (serving && Object.values(imports).includes(id)) return { id, external: true };
      if (serving && id in imports) return `\0talyx-shared:${id}`;
    },
    async load(id) {
      if (serving && Object.values(imports).includes(id)) return (await assets()).get(id.slice(1));
      if (id === "\0talyx-versions") return `export default ${JSON.stringify(sharedVersions)};`;
      if (!id.startsWith("\0talyx-shared:")) return;
      const name = id.slice("\0talyx-shared:".length);
      const url = imports[name];
      return `export * from ${JSON.stringify(url)};${name === "react" || name === "react-dom" ? `export {default} from ${JSON.stringify(url)};` : ""}`;
    },
    transformIndexHtml: { order: "pre", handler() {
      return [{ tag: "script", attrs: { type: "importmap" }, children: JSON.stringify({ imports }), injectTo: "head-prepend" }];
    } },
    async configureServer(server) {
      await assets();
      // SDK sources are outside Vite's root. Rebuild shared assets on edits so
      // development actually displays the current design system.
      const sharedRoots = ["../../sdk/ui/src/", "../../vocabulary/ts/"].map(path => fileURLToPath(new URL(path, import.meta.url)).replace(/\\/g, "/"));
      server.watcher.add(sharedRoots);
      server.watcher.on("change", async path => {
        if (!sharedRoots.some(root => path.replace(/\\/g, "/").startsWith(root))) return;
        generated.delete("development");
        try { await assets(); server.ws.send({ type: "full-reload" }); }
        catch (error) { server.config.logger.error(String(error)); }
      });
      server.middlewares.use(async (request, response, next) => {
        const key = (request.url ?? "").split("?")[0].replace(/^\//, "");
        if (!key.startsWith("shared/")) return next();
        const content = (await assets()).get(key);
        if (content === undefined) return next();
        response.setHeader("Content-Type", "text/javascript"); response.end(content);
      });
    },
    async generateBundle() {
      for (const [fileName, source] of await assets()) this.emitFile({ type: "asset", fileName, source });
      this.emitFile({ type: "asset", fileName: "shared/versions.json", source: JSON.stringify(sharedVersions) });
    },
  };
}
