import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'PropSense.AI — Singapore Property Advisor',
  description: 'AI-powered Buy Readiness Score for Singapore property buyers',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
