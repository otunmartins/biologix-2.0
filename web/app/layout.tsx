import type { Metadata } from 'next';
import { IBM_Plex_Mono, IBM_Plex_Sans } from 'next/font/google';
import './globals.css';

const sans = IBM_Plex_Sans({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--font-sans',
});
const mono = IBM_Plex_Mono({ subsets: ['latin'], weight: ['400', '500'], variable: '--font-mono' });

export const metadata: Metadata = {
  title: 'Biologix 2.0',
  description: 'Excipient triage, polymer design and molecular dynamics for biologic formulations. By Algonix AI.',
  applicationName: 'Biologix 2.0',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // suppressHydrationWarning: browser extensions (Grammarly, ColorZilla, Dark
  // Reader, password managers, translators) add attributes to <html> and <body>
  // before React loads, which React reports as a hydration mismatch. It covers
  // only these two elements' own attributes -- everything inside them is still
  // checked, so a real mismatch in the app is still reported.
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`} suppressHydrationWarning>
      <body className="font-sans" suppressHydrationWarning>
        {children}
      </body>
    </html>
  );
}
