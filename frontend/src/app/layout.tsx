import type { Metadata } from "next";
import { Fraunces, Geist, Geist_Mono } from "next/font/google";

import { ApiStatus } from "@/components/layout/api-status";
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

// Display serif for page and section headings; body text stays in Geist.
const fraunces = Fraunces({
  variable: "--font-serif",
  subsets: ["latin"],
  axes: ["opsz"],
});

export const metadata: Metadata = {
  title: "Senseigigs Taxonomy",
  description: "Visualise the Senseigigs taxonomy and validate new data sources against it.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} ${fraunces.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col md:flex-row">
        <Providers>
          {/* Top bar on narrow screens, fixed-width sidebar from md up. */}
          <aside className="flex flex-col gap-4 border-b border-sidebar-border bg-sidebar px-4 py-4 md:sticky md:top-0 md:h-screen md:w-60 md:shrink-0 md:gap-8 md:border-r md:border-b-0 md:px-5 md:py-8">
            <div className="px-3">
              <p className="label-caps">Senseigigs</p>
              <p className="font-heading text-2xl leading-tight font-medium tracking-tight">Taxonomy</p>
            </div>
            <AppNav />
            <div className="mt-auto hidden px-3 md:block">
              <ApiStatus />
            </div>
          </aside>
          <main className="min-w-0 flex-1 px-4 py-6 md:px-12 md:py-10">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
