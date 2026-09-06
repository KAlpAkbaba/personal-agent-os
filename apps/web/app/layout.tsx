import type { Metadata, Viewport } from "next";
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
      <body>{children}</body>
    </html>
  );
}
