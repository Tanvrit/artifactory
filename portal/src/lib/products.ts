export const PRODUCTS = [
  {
    slug: 'friendly',
    name: 'Friendly',
    tagline: 'POS & Retail for Bharat',
    color: '#1D4ED8',
    iconUrl: '/icons/friendly.svg',
  },
  {
    slug: 'desipops',
    name: 'DesiPops',
    tagline: 'Entertainment for Bharat',
    color: '#EA580C',
    iconUrl: '/icons/desipops.svg',
  },
  {
    slug: 'mandee',
    name: 'Mandee',
    tagline: 'Business management, simplified',
    color: '#7C3AED',
    iconUrl: '/icons/mandee.svg',
  },
  {
    slug: 'swyft',
    name: 'Swyft',
    tagline: 'Rides & delivery, now',
    color: '#D97706',
    iconUrl: '/icons/swyft.svg',
  },
  {
    slug: 'bharat-bandhu',
    name: 'Bharat Bandhu',
    tagline: 'Community & connections',
    color: '#BE185D',
    iconUrl: '/icons/bharat-bandhu.svg',
  },
  {
    slug: 'school',
    name: 'School',
    tagline: 'Education management',
    color: '#0891B2',
    iconUrl: '/icons/school.svg',
  },
  {
    slug: 'wedding',
    name: 'Friendly Wedding',
    tagline: 'Celebrations made memorable',
    color: '#9D174D',
    iconUrl: '/icons/wedding.svg',
  },
  {
    slug: 'control',
    name: 'Tanvrit Control',
    tagline: 'Mission control for Tanvrit',
    color: '#1A5C3A',
    iconUrl: '/icons/control.svg',
  },
] as const;

export type ProductSlug = typeof PRODUCTS[number]['slug'];

export const PLATFORM_META: Record<string, { label: string; sublabel: string; icon: string }> = {
  'macos-arm64':        { label: 'macOS',   sublabel: 'Apple Silicon', icon: '' },
  'macos-x64':          { label: 'macOS',   sublabel: 'Intel',         icon: '' },
  'macos-universal':    { label: 'macOS',   sublabel: 'Universal',     icon: '' },
  'windows-x64':        { label: 'Windows', sublabel: 'x64',           icon: '' },
  'linux-x64':          { label: 'Linux',   sublabel: 'DEB',           icon: '' },
  'linux-x64-rpm':      { label: 'Linux',   sublabel: 'RPM',           icon: '' },
  'linux-x64-appimage': { label: 'Linux',   sublabel: 'AppImage',      icon: '' },
};

// Ordered display groups — first available wins as "primary" CTA
export const PLATFORM_DISPLAY_ORDER = [
  'macos-arm64',
  'macos-x64',
  'macos-universal',
  'windows-x64',
  'linux-x64',
  'linux-x64-rpm',
  'linux-x64-appimage',
];
