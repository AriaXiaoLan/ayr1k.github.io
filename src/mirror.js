// Public downloads only. Never use this endpoint for launcher auto-updates.
export const MIRROR_ORIGIN = 'https://amber.ayuki-ayr1k.com';
const repos = {AMBER: 'ayr1k/amber-launcher', AmberE: 'ayr1k/AmberE'};

export function mirrorEntry(index, product, version, name) {
  if (index?.schema !== 1 || !Object.hasOwn(repos, product)) return null;
  const edition = index.products?.[product];
  const entry = edition?.versions?.[version]?.files?.[name];
  const path = `/mirror/v1/${product.toLowerCase()}/${version}/${name}`;
  if (edition?.repo !== repos[product] || !entry || entry.path !== path ||
      !Number.isSafeInteger(entry.size) || entry.size <= 0 ||
      !/^[a-f0-9]{64}$/.test(entry.sha256) ||
      !/^\d+\.\d+\.\d+$/.test(version) || !name.startsWith(`${product}-${version}-`) ||
      !/^[A-Za-z0-9.\-]+$/.test(name)) return null;
  return {...entry, url: MIRROR_ORIGIN + path};
}

export async function probeMirror(entry, fetcher = fetch, signal) {
  const response = await fetcher(entry.url, {method: 'HEAD', cache: 'no-store', signal});
  return response.ok && response.status === 200 &&
    response.headers.get('content-length') === String(entry.size);
}

export async function availableMirrors(product, version, names, fetcher = fetch, signal) {
  const response = await fetcher(MIRROR_ORIGIN + '/mirror/v1/index.json', {cache: 'no-store', signal});
  if (!response.ok) throw new Error('Mirror manifest unavailable');
  const index = await response.json();
  const pairs = await Promise.all(names.map(async name => {
    const entry = mirrorEntry(index, product, version, name);
    try { return entry && await probeMirror(entry, fetcher, signal) ? [name, entry] : null; }
    catch { return null; }
  }));
  return Object.fromEntries(pairs.filter(Boolean));
}
