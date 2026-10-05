import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { AppNav } from "@/components/layout/app-nav";

import "./globals.css";
import { Providers } from "./providers";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Taxonomy Dashboard",
  description: "Visualise the Senseigigs taxonomy and validate new data sources against it.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col md:flex-row">
        <Providers>
          {/* Top bar on narrow screens, fixed-width sidebar from md up. */}
          <aside className="border-b p-4 md:w-56 md:shrink-0 md:border-r md:border-b-0">
            <p className="mb-2 px-3 text-sm font-semibold md:mb-4">Taxonomy Dashboard</p>
            <AppNav />
          </aside>
          <main className="min-w-0 flex-1 p-4 md:p-8">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
