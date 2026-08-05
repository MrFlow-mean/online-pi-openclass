import { InterfaceLanguageProvider } from "@/contexts/interface-language-context";
import type { Metadata } from "next";
import "katex/dist/katex.min.css";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "OpenClass",
    template: "%s | OpenClass",
  },
  description: "An AI course workspace for source management, rich lesson editing, and version history.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning className="h-full antialiased">
      <body className="min-h-full flex flex-col">
        <InterfaceLanguageProvider>{children}</InterfaceLanguageProvider>
      </body>
    </html>
  );
}
