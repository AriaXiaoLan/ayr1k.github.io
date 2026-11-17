import assert from 'node:assert/strict';
import test from 'node:test';
import {availableMirrors, mirrorEntry, MIRROR_ORIGIN, probeMirror} from '../src/mirror.js';

const sha256 = 'a'.repeat(64);
function fixture(product = 'AMBER', version = '1.7.4', suffix = 'Setup-x64.exe') {
  const name = `${product}-${version}-${suffix}`;
  return {name, index: {schema: 1, products: {[product]: {
    repo: `ayr1k/${product === 'AMBER' ? 'amber-launcher' : 'AmberE'}`,
    versions: {[version]: {files: {[name]: {size: 123, sha256,
      path: `/mirror/v1/${product.toLowerCase()}/${version}/${name}`}}}},
  }}}};
}

test('product, page version, platform and complete filename must all match', () => {
  for (const product of ['AMBER', 'AmberE']) {
    for (const suffix of ['Setup-x64.exe', 'mac-x64.zip', 'mac-arm64.zip', 'linux-x64.deb', 'freebsd-x64-experimental.tar.gz']) {
      const {name, index} = fixture(product, '1.7.4', suffix);
      assert.equal(mirrorEntry(index, product, '1.7.4', name).url,
        `${MIRROR_ORIGIN}/mirror/v1/${product.toLowerCase()}/1.7.4/${name}`);
      assert.equal(mirrorEntry(index, product, '1.7.5', name), null);
      assert.equal(mirrorEntry(index, product === 'AMBER' ? 'AmberE' : 'AMBER', '1.7.4', name), null);
      if (name.includes('x64')) assert.equal(mirrorEntry(index, product, '1.7.4', name.replace('x64', 'ia32')), null);
    }
  }
});

test('manifest cannot redirect downloads to another repo, version, origin or path', () => {
  for (const mutation of [
    index => {index.schema = 2;},
    index => {index.products.AMBER.repo = 'someone/else';},
    index => {index.products.AMBER.versions['1.7.4'].files['AMBER-1.7.4-Setup-x64.exe'].path = 'https://elsewhere.test/file';},
    index => {index.products.AMBER.versions['1.7.4'].files['AMBER-1.7.4-Setup-x64.exe'].sha256 = '';},
    index => {index.products.AMBER.versions['1.7.4'].files['AMBER-1.7.4-Setup-x64.exe'].size = -1;},
  ]) {
    const {name, index} = fixture(); mutation(index);
    assert.equal(mirrorEntry(index, 'AMBER', '1.7.4', name), null);
  }
});

test('404, HTML fallback, bad length, network failure and pending versions hide mirror', async () => {
  const {name, index} = fixture();
  for (const response of [new Response(null, {status: 404}), new Response(null, {status: 200}),
    new Response(null, {status: 200, headers: {'content-length': '99'}})]) {
    assert.deepEqual(await availableMirrors('AMBER', '1.7.4', [name], async (_, options) =>
      options?.method === 'HEAD' ? response : Response.json(index)), {});
  }
  assert.deepEqual(await availableMirrors('AMBER', '1.7.4', [name], async (_, options) => {
    if (options?.method === 'HEAD') throw new Error('offline');
    return Response.json(index);
  }), {});
  assert.deepEqual(await availableMirrors('AMBER', '1.7.5', [name], async () => Response.json(index)), {});
});

test('verified entries are probed with HEAD and no-store; click can recheck', async () => {
  const {name, index} = fixture(); const calls = [];
  const fetcher = async (url, options) => {
    calls.push([url, options]);
    return options.method === 'HEAD' ? new Response(null, {headers: {'content-length': '123'}}) : Response.json(index);
  };
  const entries = await availableMirrors('AMBER', '1.7.4', [name], fetcher);
  assert.equal(entries[name].sha256, sha256);
  assert.equal(await probeMirror(entries[name], fetcher), true);
  assert.ok(calls.every(([, options]) => options.cache === 'no-store'));
});
