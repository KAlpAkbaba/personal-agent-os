import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Personal Agent OS",
  description: "Tek sahipli kişisel ajan işletim sistemi",
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
