import { fetchAllManifests, fetchCatalog } from '@/lib/manifests';
import { PRODUCTS, PLATFORM_META, PLATFORM_DISPLAY_ORDER } from '@/lib/products';
import type { Manifest, PlatformEntry } from '@/types';
import styles from './page.module.css';

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatDate(iso: string): string {
  try {
    return new Intl.DateTimeFormat('en-IN', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

function formatBytes(bytes: number): string {
  if (!bytes || bytes === 0) return '';
  if (bytes >= 1_000_000_000) return `${(bytes / 1_000_000_000).toFixed(1)} GB`;
  if (bytes >= 1_000_000) return `${(bytes / 1_000_000).toFixed(0)} MB`;
  return `${(bytes / 1_000).toFixed(0)} KB`;
}

function getPlatformEmoji(platform: string): string {
  if (platform.startsWith('macos')) return '\uD83C\uDF4E';   // 🍎
  if (platform.startsWith('windows')) return '\uD83E\uDEDF'; // 🪟
  if (platform.startsWith('linux')) return '\uD83D\uDC27';   // 🐧
  return '\uD83D\uDCBE';
}

// ── Sub-components ────────────────────────────────────────────────────────────

interface PlatformButtonProps {
  platform: string;
  entry: PlatformEntry;
  product: string;
  wide?: boolean;
}

function PlatformButton({ platform, entry, product, wide }: PlatformButtonProps) {
  const meta = PLATFORM_META[platform];
  if (!meta) return null;

  const label = meta.label;
  const sublabel = meta.sublabel;
  const isWeb = entry.format === 'web';
  const emoji = isWeb ? '🌐' : getPlatformEmoji(platform);
  const isAvailable = entry.available;

  const size = isWeb ? '' : formatBytes(entry.size_bytes);

  const className = [
    styles.platformBtn,
    !isAvailable ? styles.platformDisabled : '',
    wide ? styles.platformBtnWide : '',
  ]
    .filter(Boolean)
    .join(' ');

  const inner = (
    <>
      <span className={styles.platformIcon}>{emoji}</span>
      <span className={styles.platformText}>
        <span className={styles.platformLabel}>{label}</span>
        <span className={styles.platformSublabel}>
          {sublabel}
          {size ? ` · ${size}` : ''}
        </span>
      </span>
      {isAvailable ? (
        isWeb
          ? <span className={styles.platformFormat}>↗</span>
          : <span className={styles.platformFormat}>.{entry.format}</span>
      ) : (
        <span className={styles.comingSoonTag}>Soon</span>
      )}
    </>
  );

  if (!isAvailable) {
    return (
      <span className={className} aria-disabled="true" title="Not yet available">
        {inner}
      </span>
    );
  }

  return (
    <a
      className={className}
      href={entry.url}
      title={isWeb ? `Open ${label}` : `Download ${label} ${sublabel} (${entry.format.toUpperCase()})`}
      target={isWeb ? '_blank' : undefined}
      rel={isWeb ? 'noopener noreferrer' : undefined}
    >
      {inner}
    </a>
  );
}

interface ProductCardProps {
  slug: string;
  name: string;
  tagline: string;
  color: string;
  iconUrl: string;
  manifest: Manifest | null;
}

function ProductCard({ slug, name, tagline, color, iconUrl, manifest }: ProductCardProps) {
  // Build ordered list of platforms present in the manifest
  const platformsToShow: Array<{ key: string; entry: PlatformEntry }> = [];

  if (manifest?.platforms) {
    for (const key of PLATFORM_DISPLAY_ORDER) {
      const entry = manifest.platforms[key];
      if (entry) {
        platformsToShow.push({ key, entry });
      }
    }
    // Also pick up any platforms NOT in the predefined order
    for (const [key, entry] of Object.entries(manifest.platforms)) {
      if (!PLATFORM_DISPLAY_ORDER.includes(key)) {
        platformsToShow.push({ key, entry });
      }
    }
  }

  const displayName = manifest?.display_name || name;
  const displayTagline = manifest?.tagline || tagline;
  const version = manifest?.version ?? '—';
  const releasedAt = manifest?.released_at ? formatDate(manifest.released_at) : null;
  const releaseNotes = manifest?.release_notes;

  // Determine if any platform is available
  const hasAnyAvailable = platformsToShow.some((p) => p.entry.available);

  return (
    <article className={styles.card}>
      {/* Accent bar */}
      <div className={styles.cardAccent} style={{ background: color }} />

      <div className={styles.cardBody}>
        {/* Header */}
        <div className={styles.cardHeader}>
          <div className={styles.productIcon}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={iconUrl}
              alt={`${name} icon`}
              width={52}
              height={52}
            />
          </div>
          <div className={styles.cardMeta}>
            <div className={styles.cardName}>{displayName}</div>
            <div className={styles.cardTagline}>{displayTagline}</div>
          </div>
          <span className={styles.versionPill} title={releasedAt ?? undefined}>
            v{version}
          </span>
        </div>

        {/* Divider */}
        <div className={styles.cardDivider} />

        {/* Platform download buttons */}
        {platformsToShow.length === 0 ? (
          <div className={styles.platforms} style={{ gridTemplateColumns: '1fr' }}>
            <span className={[styles.platformBtn, styles.platformDisabled].join(' ')}>
              <span className={styles.platformIcon}>📦</span>
              <span className={styles.platformText}>
                <span className={styles.platformLabel}>No downloads yet</span>
                <span className={styles.platformSublabel}>Check back soon</span>
              </span>
            </span>
          </div>
        ) : (
          <div className={styles.platforms}>
            {platformsToShow.map(({ key, entry }, idx) => {
              // If total is odd and this is the last one, make it wide
              const isLast = idx === platformsToShow.length - 1;
              const isOdd = platformsToShow.length % 2 !== 0;
              return (
                <PlatformButton
                  key={key}
                  platform={key}
                  entry={entry}
                  product={slug}
                  wide={isLast && isOdd}
                />
              );
            })}
          </div>
        )}

        {/* Release notes — only if non-empty */}
        {releaseNotes && releaseNotes.trim() && (
          <div className={styles.releaseNotes}>{releaseNotes}</div>
        )}
      </div>
    </article>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default async function DownloadsPage() {
  const slugs = PRODUCTS.map((p) => p.slug);

  // Fetch in parallel at build time
  const [catalog, manifests] = await Promise.all([
    fetchCatalog(),
    fetchAllManifests(slugs),
  ]);

  const updatedAt = catalog?.updated_at ? formatDate(catalog.updated_at) : null;

  // Count products with at least one available download
  const availableCount = PRODUCTS.filter((p) => {
    const m = manifests[p.slug];
    if (!m) return false;
    return Object.values(m.platforms ?? {}).some((e) => e.available);
  }).length;

  const totalProducts = PRODUCTS.length;
  const totalPlatforms = 3; // macOS, Windows, Linux

  return (
    <div className={styles.wrapper}>
      <main className={styles.main}>
        {/* ── Hero ─────────────────────────────────────── */}
        <section className={styles.hero}>
          <div className={styles.heroTop}>
            <div className={styles.wordmark}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src="/icons/tanvrit-mark.svg"
                alt="Tanvrit"
                width={28}
                height={28}
                className={styles.wordmarkIcon}
              />
              <span className={styles.wordmarkText}>Tanvrit</span>
            </div>
            {updatedAt && (
              <span className={styles.updatedBadge}>Updated {updatedAt}</span>
            )}
          </div>

          <h1 className={styles.heroTitle}>Downloads</h1>
          <p className={styles.heroSub}>
            Install Tanvrit apps on macOS, Windows, or Linux.
            All builds are verified and delivered directly from GitHub Releases.
          </p>

          <div className={styles.heroStats}>
            <div className={styles.stat}>
              <span className={styles.statNum}>{totalProducts}</span>
              <span className={styles.statLabel}>Products</span>
            </div>
            <div className={styles.statDiv} />
            <div className={styles.stat}>
              <span className={styles.statNum}>{totalPlatforms}</span>
              <span className={styles.statLabel}>Platforms</span>
            </div>
            <div className={styles.statDiv} />
            <div className={styles.stat}>
              <span className={styles.statNum}>{availableCount}</span>
              <span className={styles.statLabel}>Live</span>
            </div>
          </div>
        </section>

        {/* ── Section label ─────────────────────────── */}
        <div className={styles.sectionHeader}>
          <span className={styles.sectionTitle}>All products</span>
          <div className={styles.sectionLine} />
        </div>

        {/* ── Product grid ──────────────────────────── */}
        <div className={styles.grid}>
          {PRODUCTS.map((product) => (
            <ProductCard
              key={product.slug}
              slug={product.slug}
              name={product.name}
              tagline={product.tagline}
              color={product.color}
              iconUrl={product.iconUrl}
              manifest={manifests[product.slug] ?? null}
            />
          ))}
        </div>
      </main>

      {/* ── Footer ────────────────────────────────── */}
      <footer className={styles.footer}>
        <div className={styles.footerInner}>
          <div className={styles.footerLeft}>
            <span className={styles.footerWordmark}>Tanvrit</span>
            <span className={styles.footerText}>
              &copy; 2026 Tanvrit
            </span>
          </div>
          <div className={styles.footerLinks}>
            <a
              href="https://artifacts.tanvrit.com/catalog.json"
              className={styles.footerLink}
              target="_blank"
              rel="noopener noreferrer"
            >
              catalog.json
            </a>
            <a
              href="https://tanvrit.com"
              className={styles.footerLink}
              target="_blank"
              rel="noopener noreferrer"
            >
              tanvrit.com
            </a>
            <a
              href="https://github.com/tanvrit/artifactory"
              className={styles.footerLink}
              target="_blank"
              rel="noopener noreferrer"
            >
              GitHub
            </a>
          </div>
        </div>
      </footer>
    </div>
  );
}
