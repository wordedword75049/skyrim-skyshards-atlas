import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

test('every named import used by the browser entry module is exported', async () => {
  const entry = new URL('../src/skyshard_tracker/static/app.js', import.meta.url);
  const source = await readFile(entry, 'utf8');
  const imports = [...source.matchAll(/import\s*\{([^}]+)\}\s*from\s*['"](\.[^'"]+)['"]/g)];
  assert.equal(imports.length, 2);
  for (const [, names, specifier] of imports) {
    const module = await import(new URL(specifier, entry));
    for (const name of names.split(',').map(value => value.trim()).filter(Boolean)) {
      assert.ok(Object.hasOwn(module, name), `${specifier} does not export ${name}`);
    }
  }
});
