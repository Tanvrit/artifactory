export interface PlatformEntry {
  url: string;
  direct_url: string;
  sha256: string;
  size_bytes: number;
  format: string;
  available: boolean;
}

export interface Manifest {
  product: string;
  display_name: string;
  tagline: string;
  version: string;
  build: number;
  released_at: string;
  release_notes: string;
  min_required_version: string;
  platforms: Record<string, PlatformEntry>;
}

export interface CatalogEntry {
  version: string;
  build: number;
  released_at: string;
  manifest_url: string;
}

export interface Catalog {
  updated_at: string;
  products: Record<string, CatalogEntry>;
}
