"use client";

import { useEffect, useState } from "react";

type ApiStatus = {
  status?: string;
  service?: string;
  message?: string;
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export default function Home() {
  const [api, setApi] = useState<ApiStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API_URL}/api/v1`)
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return (await response.json()) as ApiStatus;
      })
      .then(setApi)
      .catch((reason: Error) => setError(reason.message));
  }, []);

  return (
    <main className="shell">
      <div className="eyebrow">AI SERIES · DEVELOPMENT</div>
      <h1>Создаём сериалы<br /><span>по литературным книгам</span></h1>
      <p className="intro">
        Рабочая среда проекта. Сейчас мы закладываем фундамент платформы:
        книги, персонажи, визуальные образы и производство эпизодов.
      </p>
      <section className="status">
        <div>
          <div className="label">BACKEND API</div>
          {api ? (
            <div className="state online">● Подключён</div>
          ) : error ? (
            <div className="state offline">● Нет соединения: {error}</div>
          ) : (
            <div className="state pending">● Проверяем соединение…</div>
          )}
        </div>
        <div className="api-message">{api?.message ?? "Ожидание ответа API"}</div>
      </section>
      <div className="footer">MVP · FOUNDATION PHASE · v0.1</div>
    </main>
  );
}
