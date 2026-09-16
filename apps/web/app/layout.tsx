import type { Metadata, Viewport } from "next";

import CommandPalette from "./components/CommandPalette";
import SiteNav from "./components/SiteNav";
import "./globals.css";

export const metadata: Metadata = {
  title: "Personal Agent OS",
  description: "Tek sahipli kişisel ajan işletim sistemi",
  applicationName: "Personal Agent OS",
  icons: { icon: "/icon.svg", apple: "/icon.svg" },
  appleWebApp: { capable: true, title: "Çekirdek", statusBarStyle: "black-translucent" },
};

/**
 * M18.3: the Core's ground is near-black and the stage is the viewport, so the
 * browser's own surfaces are told to match rather than framing it in grey.
 * `viewport-fit: cover` lets the stage reach under a notch; the overlays sit
 * inside the safe area on their own.
 */
export const viewport: Viewport = {
  themeColor: "#06050a",
  colorScheme: "dark",
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="tr">
      <body>
        {/*
          B25 req 724: the first thing in the document, and the first thing a keyboard or a
          screen reader meets. Without it, reaching the content past a fourteen-item nav
          costs fourteen tab stops on every page.
        */}
        <a className="skip-link" href="#main" data-skip-link>
          İçeriğe geç
        </a>
        {/*
          B23 req 685/716: the pages, on every page, including the Core — where it renders
          as a quiet affordance rather than a bar, because the manifest installs `/core`
          with no address bar and the alternative to a nav there is a room with no door.
          Rendered in the layout so a page added later cannot forget it — which B24 relied
          on, adding eight pages and touching nothing here.
        */}
        <SiteNav />
        {/*
          B25 req 702/703: one palette, in the layout, so ⌘K works on every page for the
          same reason the nav does — a page added later cannot forget it.
        */}
        <CommandPalette />
        <div id="main" tabIndex={-1} className="main-target">
          {children}
        </div>
      </body>
    </html>
  );
}
