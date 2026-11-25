import { existsSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve, sep } from "node:path";
import { searchForWorkspaceRoot } from "vite";

/** The dev server's `/@fs/` address of a file. A header carries ASCII only, so a repository under a path with
    non-ASCII characters (or spaces) is percent-encoded; the dev server decodes it with `decodeURI`. */
export function fsAddress(file, query = "") {
  return `/@fs/${encodeURI(file.replace(/\\/g, "/").replace(/^\//, ""))}${query ?? ""}`;
}

/** In development, a module file the window reads from the Host (`/api/modules/<id>/files/<digest>/<path>`) that is source in
    this repository's `modules/` is compiled by the dev server instead: the request is sent to its `/@fs/` address, so
    TypeScript and its relative imports work. Every module's frontend is still loaded from the address its declaration
    gives; files that are not here (an installed module's built frontend) go to the Host as they are. */
export function moduleSources() {
  const modules = fileURLToPath(new URL("../../modules/", import.meta.url));
  return {
    name: "talyx-module-sources",
    apply: "serve",
    // `/@fs/` serves only allowed folders, by default just this package; the modules folder is added.
    config(config) {
      return { server: { fs: { allow: [searchForWorkspaceRoot(resolve(config.root ?? process.cwd())), modules] } } };
    },
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        const match = /^\/api\/modules\/([a-z][a-z0-9_]*)\/files\/[^/?]+\/([^?]+)(\?.*)?$/.exec(request.url ?? "");
        if (!match) return next();
        const directory = resolve(modules, match[1]);
        const file = resolve(directory, decodeURIComponent(match[2]));
        if (!file.startsWith(directory + sep) || !existsSync(file) || !statSync(file).isFile()) return next();
        response.statusCode = 302;
        response.setHeader("Location", fsAddress(file, match[3]));
        response.end();
      });
    },
  };
}
