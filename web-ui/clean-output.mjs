// Only the workspace's generated web assets are removed before a fresh build.
import {existsSync, lstatSync, rmSync} from 'node:fs';
import {fileURLToPath} from 'node:url';

const output = fileURLToPath(new URL('../web-dist', import.meta.url));
if (existsSync(output) && lstatSync(output).isSymbolicLink()) {
  throw new Error('The generated web-dist output must not be a symbolic link.');
}
rmSync(output, {recursive: true, force: true});
