import type { Manifest, Catalog } from '@/types';

const GITHUB_RAW = 'https://raw.githubusercontent.com/tanvrit/artifactory/main';

export async function fetchManifest(product: string): Promise<Manifest | null> {
  try {
    const res = await fetch(`${GITHUB_RAW}/manifests/${product}/latest.json`, {
      next: { revalidate: 60 },
    });
    if (!res.ok) return null;
    return res.json() as Promise<Manifest>;
  } catch {
    return null;
  }
}

export async function fetchCatalog(): Promise<Catalog | null> {
  try {
    const res = await fetch(`${GITHUB_RAW}/manifests/catalog.json`, {
      next: { revalidate: 30 },
    });
    if (!res.ok) return null;
    return res.json() as Promise<Catalog>;
  } catch {
    return null;
  }
}

export async function fetchAllManifests(
  slugs: readonly string[]
): Promise<Record<string, Manifest | null>> {
  const results = await Promise.all(slugs.map((s) => fetchManifest(s)));
  return Object.fromEntries(slugs.map((s, i) => [s, results[i]]));
}
