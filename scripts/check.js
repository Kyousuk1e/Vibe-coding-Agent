import { readdir } from 'node:fs/promises';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
let count = 0;
for (const dir of ['src', 'scripts', 'test']) {
  for (const file of await readdir(dir)) {
    if (!file.endsWith('.js')) continue;
    const result = spawnSync(process.execPath, ['--check', join(dir, file)], { encoding: 'utf8' });
    if (result.status !== 0) { process.stderr.write(result.stderr); process.exit(1); }
    count++;
  }
}
console.log(`Syntax checked ${count} JavaScript files.`);
