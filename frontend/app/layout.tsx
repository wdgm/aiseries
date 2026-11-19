import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Storyfoundry — AI Series",
  description: "Студия для превращения литературных произведений в истории",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
