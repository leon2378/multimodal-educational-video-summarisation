import "katex/dist/katex.min.css";
import "./globals.css";

import { ClerkProvider } from "@clerk/nextjs";
import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import type { ReactNode } from "react";

import { THEME_SCRIPT } from "@/components/theme";
import { CLERK_KEY } from "@/lib/auth";

import { Providers } from "./providers";

/** Clerk's sign-in and account screens in the app's colours: CSS variables, so they follow the
 *  light or dark theme. Its styles go in their own layer (see globals.css). */
const clerkAppearance = {
  cssLayerName: "clerk",
  variables: {
    colorPrimary: "var(--primary)",
    colorPrimaryForeground: "var(--primary-foreground)",
    colorDanger: "var(--destructive)",
    colorSuccess: "var(--success)",
    colorWarning: "var(--warning)",
    colorBackground: "var(--popover)",
    colorForeground: "var(--popover-foreground)",
    colorMuted: "var(--muted)",
    colorMutedForeground: "var(--muted-foreground)",
    colorInput: "var(--background)",
    colorInputForeground: "var(--foreground)",
    colorBorder: "var(--border)",
    colorRing: "var(--ring)",
    colorNeutral: "var(--foreground)",
    fontFamily: "var(--font-geist-sans)",
    borderRadius: "var(--radius)",
  },
};

const sans = Geist({ subsets: ["latin"], variable: "--font-geist-sans" });
const mono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono" });

export const metadata: Metadata = {
  title: "Lecture Summariser",
  description: "Timestamped study notes, transcripts, slides, a quiz and cited Q&A for lecture videos.",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fafafb" },
    { media: "(prefers-color-scheme: dark)", color: "#0f0f12" },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // The theme script sets a class on <html> before React hydrates.
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className={`${sans.variable} ${mono.variable} font-sans`}>
        {/* Without a Clerk key the app runs as it did before sign-in, for local development. */}
        {CLERK_KEY ? (
          // No telemetry to Clerk, as Next's is off too (NEXT_TELEMETRY_DISABLED).
          <ClerkProvider publishableKey={CLERK_KEY} appearance={clerkAppearance} telemetry={false}>
            <Providers>{children}</Providers>
          </ClerkProvider>
        ) : (
          <Providers>{children}</Providers>
        )}
      </body>
    </html>
  );
}
