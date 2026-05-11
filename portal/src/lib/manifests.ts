import type { Manifest, Catalog } from '@/types';

// Fetch manifests from the artifacts worker (which serves the live R2-backed
// JSON) instead of raw.githubusercontent.com. Two reasons:
//   1. The worker serves freshly-pushed manifests as soon as the manifest-
//      update workflow finishes — no GitHub-side cache delay.
//   2. CORS is already configured on the worker, so the same fetch works
//      from both server-side (Next.js SSG/ISR) and client-side code.
//
// 60-second `revalidate` matches the worker's edge cache TTL — a release
// shows up within 60s without a portal rebuild.
const ARTIFACTS_BASE = 'https://artifacts.tanvrit.com';

export async function fetchManifest(product: string): Promise<Manifest | null> {
  try {
    const res = await fetch(`${ARTIFACTS_BASE}/${product}/latest.json`, {
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
    // catalog.json doesn't have a per-product URL on the worker — fall back
    // to the manifests path directly (the worker serves manifests/* from R2).
    const res = await fetch(`${ARTIFACTS_BASE}/manifests/catalog.json`, {
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
