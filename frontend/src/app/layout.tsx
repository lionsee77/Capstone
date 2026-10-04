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
      <body className="flex min-h-full">
        <Providers>
          <aside className="w-56 shrink-0 border-r p-4">
            <p className="mb-4 px-3 text-sm font-semibold">Taxonomy Dashboard</p>
            <AppNav />
          </aside>
          <main className="flex-1 p-8">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
