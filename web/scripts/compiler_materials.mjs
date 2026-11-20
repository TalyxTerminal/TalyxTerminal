// Platform-owned compiler receipt: use the same shared dependency source as
// the production frontend build, never the author's npm installation.
import { createRequire } from 'node:module';
import { readFile, access, mkdir, writeFile, realpath } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { sharedVersions } from './shared-dependencies.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
const compilerPackageJson = path.join(root, 'web/package.json');
const nodeModules = path.join(root, 'web/node_modules');
const require = createRequire(compilerPackageJson);
const compiler = require('esbuild');
const lock = JSON.parse(await readFile(path.join(root, 'web/package-lock.json'), 'utf8'));
const expected = lock.packages['node_modules/esbuild'].version;
if (compiler.version !== expected) throw new Error(`Prepare the locked frontend compiler ${expected}; installed ${compiler.version}`);
await access(nodeModules);
const typescript = require('typescript');
const typescriptVersion = lock.packages['node_modules/typescript'].version;
if (typescript.version !== typescriptVersion) throw new Error(`Prepare the locked TypeScript ${typescriptVersion}; installed ${typescript.version}`);
const webConfig = typescript.readConfigFile(path.join(root, 'web/tsconfig.json'), typescript.sys.readFile).config;
const paths = Object.fromEntries(Object.entries(webConfig.compilerOptions.paths).map(([name, values]) =>
  [name, values.map(value => path.resolve(root, 'web', webConfig.compilerOptions.baseUrl, value).replaceAll('\\', '/'))]));
const language = { state: 'ready', typescriptVersion, paths,
  typeRoots: [path.join(nodeModules, '@types')], sdkVersion: sharedVersions['@talyx/sdk'] };
if (process.argv[2] === '--prepare-language') {
  const projectRoot = await realpath(process.argv[3]);
  const config = ['tsconfig.json', 'jsconfig.json'].map(name => path.join(projectRoot, name))
    .find(name => typescript.sys.fileExists(name));
  let own = {};
  if (config) {
    const input = typescript.readConfigFile(config, typescript.sys.readFile);
    if (input.error) throw new Error(`The project language configuration is invalid: ${config}`);
    own = typescript.parseJsonConfigFileContent(input.config, typescript.sys, projectRoot).options;
  }
  const controlled = path.join(projectRoot, '.talyx');
  await mkdir(controlled, { recursive: true });
  const actual = await realpath(controlled);
  if (!actual.toLowerCase().startsWith(projectRoot.toLowerCase() + path.sep)) throw new Error('The language configuration directory escapes the project.');
  const overlay = path.join(controlled, 'tsconfig.environment.json');
  if (typescript.sys.fileExists(overlay) && !JSON.parse(await readFile(overlay, 'utf8')).talyxEnvironment) throw new Error('The language configuration path is occupied by a project file.');
  const merged = { ...paths };
  for (const [name, values] of Object.entries(own.paths || {})) merged[name] = values.map(value => path.resolve(own.pathsBasePath || own.baseUrl || projectRoot, value).replaceAll('\\', '/'));
  const document = { talyxEnvironment: { sdkVersion: language.sdkVersion, typescriptVersion },
    ...(config ? { extends: config.replaceAll('\\', '/') } : { include: [path.join(projectRoot, '**/*.ts').replaceAll('\\', '/'), path.join(projectRoot, '**/*.tsx').replaceAll('\\', '/'), path.join(projectRoot, '**/*.js').replaceAll('\\', '/')] }),
    compilerOptions: { baseUrl: projectRoot.replaceAll('\\', '/'), paths: merged,
      typeRoots: [...new Set([...(own.typeRoots || []), ...language.typeRoots])], ...(config ? {} : { jsx: 'react-jsx', target: 'ES2022', moduleResolution: 'Bundler', module: 'ESNext', allowJs: true, noEmit: true }) } };
  await writeFile(overlay, JSON.stringify(document, null, 2) + '\n');
  process.stdout.write(JSON.stringify({ state: 'ready', configurationPath: overlay, extends: config, sdkVersion: language.sdkVersion }) + '\n');
} else process.stdout.write(JSON.stringify({ state: 'ready', compilerPackageJson, nodeModules,
  compilerVersion: compiler.version, shared: sharedVersions, language }) + '\n');
