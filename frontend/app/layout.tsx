import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Series",
  description: "Платформа для создания ИИ-сериалов по литературным произведениям",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
