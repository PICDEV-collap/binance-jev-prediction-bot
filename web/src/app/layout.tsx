import type { Metadata } from 'next';
import './globals.css';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export const metadata: Metadata = {
  title: 'Binance Prediction Markets Bot | Jev AI Engine',
  description: 'High-Performance Event-Driven Quantitative Prediction Trading Bot integrated with Jev AI Decision Engine',
  icons: {
    icon: '/favicon.ico',
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-[#070a10] text-slate-100 antialiased selection:bg-emerald-500/30 selection:text-emerald-300">
        {children}
      </body>
    </html>
  );
}
