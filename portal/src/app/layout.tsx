import type { Metadata } from 'next';
import { Inter } from 'next/font/google';
import './globals.css';

const inter = Inter({ subsets: ['latin'], display: 'swap' });

export const metadata: Metadata = {
  title: 'Downloads — Tanvrit',
  description: 'Download Tanvrit apps for macOS, Windows, and Linux.',
  openGraph: {
    title: 'Downloads — Tanvrit',
    description: 'Download Tanvrit apps for all platforms.',
    url: 'https://artifacts.tanvrit.com',
    siteName: 'Tanvrit Artifacts',
  },
  metadataBase: new URL('https://artifacts.tanvrit.com'),
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={inter.className}>{children}</body>
    </html>
  );
}
