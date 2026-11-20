import { preview } from "vite";

const [port, backendPort] = process.argv.slice(2).map(Number);
const target = `http://127.0.0.1:${backendPort}`;
const server = await preview({
  configFile: false,
  logLevel: "error",
  preview: {
    host: "127.0.0.1", port, strictPort: true,
    proxy: { "/api": target, "/ws": { target, ws: true }, "/m": { target, ws: true } },
  },
});
process.stdout.write(`http://127.0.0.1:${server.httpServer.address().port}/\n`);
