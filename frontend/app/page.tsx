
"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

type Project = { id: string; name: string; description: string | null; status: string };
type Book = {
  id: string;
  title: string;
  author: string | null;
  language: string | null;
  source_filename: string | null;
  status: string;
  chapters_count: number;
};
type Chapter = { id: string; chapter_number: number; title: string | null; text_length: number };
type TextChunk = { id: string; chunk_index: number; token_count: number; content: string };
type ChapterDetail = {
  id: string;
  chapter_number: number;
  title: string | null;
  text: string;
  chunks: TextChunk[];
};
type AnalysisJob = {
  id: string;
  book_id: string;
  status: string;
  total_chunks: number;
  processed_chunks: number;
  failed_chunks: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  progress_percent: number;
  current_chapter_number: number | null;
  current_chunk_index: number | null;
  report: Record<string, unknown> | null;
  last_error: string | null;
};
type AnalysisResult = {
  status: string;
  saved_to_database: boolean;
  usage?: { input_tokens: number; output_tokens: number; total_tokens: number };
  analysis?: { characters?: Array<{ name: string; description?: string | null; character_type: string; importance: string; aliases: string[]; attributes: Array<{ category: string; value: string }>; relationships: Array<{ target_character_name: string; relationship_type: string }> }> };
  report?: Record<string, unknown>;
};

type CharacterAlias = { id: string; alias: string; alias_type: string };
type CharacterAttribute = { id: string; key: string; value: string; evidence?: string | null; confidence?: number | null; status?: string; source_chapter_id?: string | null };
type CharacterMention = { id: string; chapter_id: string; text_chunk_id?: string | null; mention_text: string; start_position?: number | null; end_position?: number | null; confidence?: number | null };
type CharacterRelationship = { id: string; target_character_id?: string; target_character_name?: string | null; source_character_id?: string; source_character_name?: string | null; relationship_type: string; description?: string | null; evidence?: string | null; confidence?: number | null; status?: string };
type CharacterReference = { id: string; character_id?: string; title: string; url?: string | null; notes?: string | null };
type CharacterSummary = {
  id: string;
  project_id: string;
  canonical_name: string;
  description: string | null;
  character_type: string;
  importance: string;
  status: string;
  aliases: CharacterAlias[];
  attributes: CharacterAttribute[];
  mentions: CharacterMention[];
  relationships: CharacterRelationship[];
  target_relationships: CharacterRelationship[];
  references: CharacterReference[];
};

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, init);
  const text = await response.text();
  let payload: unknown;

  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = text;
  }

  if (!response.ok) {
    const body = payload as { detail?: unknown; message?: string } | null;
    const detail = typeof body?.detail === "string" ? body.detail : body?.message;
    throw new Error(detail ?? `Ошибка сервера (${response.status})`);
  }

  return payload as T;
}

function Icon({ name, size = 20 }: { name: "books" | "grid" | "plus" | "chevron" | "upload" | "search" | "arrow" | "sparkles" | "file" | "close" | "refresh" | "clock" | "check" | "menu"; size?: number }) {
  const common = { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true as const };
  const paths = {
    books: <><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v17H6.5A2.5 2.5 0 0 0 4 22z" /><path d="M4 5.5v14A2.5 2.5 0 0 1 6.5 17H20M8 7h8M8 11h7" /></>,
    grid: <><rect x="3.5" y="3.5" width="7" height="7" rx="1.5" /><rect x="13.5" y="3.5" width="7" height="7" rx="1.5" /><rect x="3.5" y="13.5" width="7" height="7" rx="1.5" /><rect x="13.5" y="13.5" width="7" height="7" rx="1.5" /></>,
    plus: <><path d="M12 5v14M5 12h14" /></>,
    chevron: <path d="m9 18 6-6-6-6" />,
    upload: <><path d="M12 16V4m0 0L7 9m5-5 5 5" /><path d="M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4" /></>,
    search: <><circle cx="10.8" cy="10.8" r="6.8" /><path d="m16 16 4.5 4.5" /></>,
    arrow: <><path d="M5 12h14m-6-6 6 6-6 6" /></>,
    sparkles: <><path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Z" /><path d="m19 15 .9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9L19 15Z" /></>,
    file: <><path d="M13 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V10z" /><path d="M13 3v7h7M8 14h8M8 17h8" /></>,
    close: <><path d="m18 6-12 12M6 6l12 12" /></>,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M5.5 9A7 7 0 0 1 18 6l2 2M4 16l2 2a7 7 0 0 0 12.5-3" /></>,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    menu: <><path d="M4 6h16M4 12h16M4 18h16" /></>,
  };
  return <svg {...common}>{paths[name]}</svg>;
}

function formatNumber(value: number) {
  return new Intl.NumberFormat("ru-RU").format(value);
}

function formatSize(characters: number) {
  if (characters >= 1_000_000) return `${(characters / 1_000_000).toFixed(1)} млн знаков`;
  if (characters >= 1_000) return `${(characters / 1_000).toFixed(1)} тыс. знаков`;
  return `${characters} знаков`;
}

export default function Home() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [books, setBooks] = useState<Book[]>([]);
  const [selectedBookId, setSelectedBookId] = useState("");
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [chapterDetail, setChapterDetail] = useState<ChapterDetail | null>(null);
  const [selectedChunk, setSelectedChunk] = useState(0);
  const [job, setJob] = useState<AnalysisJob | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(null);
  const [characters, setCharacters] = useState<CharacterSummary[]>([]);
  const [selectedCharacterId, setSelectedCharacterId] = useState("");
  const [characterDraft, setCharacterDraft] = useState("");
  const [characterSearch, setCharacterSearch] = useState("");
  const [characterRoleFilter, setCharacterRoleFilter] = useState("all");
  const [characterStatusFilter, setCharacterStatusFilter] = useState("all");
  const [characterSubTab, setCharacterSubTab] = useState<"cards" | "detail">("cards");
  const [characterReferenceDraft, setCharacterReferenceDraft] = useState({ title: "", url: "", notes: "" });
  const [search, setSearch] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  const [createNewProject, setCreateNewProject] = useState(false);
  const [loading, setLoading] = useState(true);
  const [apiOnline, setApiOnline] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [pageError, setPageError] = useState("");
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  const selectedProject = projects.find((project) => project.id === projectId) ?? null;
  const selectedBook = books.find((book) => book.id === selectedBookId) ?? null;
  const selectedCharacter = characters.find((character) => character.id === selectedCharacterId) ?? characters[0] ?? null;
  const currentChunk = chapterDetail?.chunks.find((chunk) => chunk.chunk_index === selectedChunk) ?? null;

  const filteredBooks = useMemo(
    () => books.filter((book) => `${book.title} ${book.author ?? ""}`.toLowerCase().includes(search.toLowerCase())),
    [books, search],
  );

  const characterRoleOptions = useMemo(
    () => Array.from(new Set(characters.flatMap((character) => [character.character_type, character.importance, character.status]))).filter(Boolean).sort(),
    [characters],
  );

  const characterStatusCounts = useMemo(
    () =>
      characters.reduce((counts, character) => {
        counts[character.status] = (counts[character.status] ?? 0) + 1;
        return counts;
      }, {} as Record<string, number>),
    [characters],
  );

  const filteredCharacters = useMemo(() => {
    const query = characterSearch.trim().toLowerCase();
    return characters.filter((character) => {
      const haystack = [
        character.canonical_name,
        character.character_type,
        character.importance,
        character.status,
        ...character.aliases.map((alias) => alias.alias),
        ...character.attributes.map((attribute) => `${attribute.key} ${attribute.value}`),
      ]
        .join(" ")
        .toLowerCase();
      const matchesQuery = !query || haystack.includes(query);
      const matchesRole =
        characterRoleFilter === "all" ||
        [character.character_type, character.importance, character.status].some((value) => value.toLowerCase() === characterRoleFilter.toLowerCase());
      const matchesStatus = characterStatusFilter === "all" || character.status.toLowerCase() === characterStatusFilter.toLowerCase();
      return matchesQuery && matchesRole && matchesStatus;
    });
  }, [characterSearch, characterRoleFilter, characterStatusFilter, characters]);

  function openImportModal() {
    setActionError("");
    setCreateNewProject(projects.length === 0);
    setModalOpen(true);
  }

  const loadProjects = useCallback(async () => {
    const list = await apiRequest<Project[]>("/api/v1/projects");
    setProjects(list);
    setProjectId((current) => (current && list.some((project) => project.id === current) ? current : list[0]?.id ?? ""));
  }, []);

  const loadBooks = useCallback(async (id: string) => {
    const list = await apiRequest<Book[]>(`/api/v1/projects/${id}/books`);
    setBooks(list);
    setSelectedBookId((current) => (current && list.some((book) => book.id === current) ? current : list[0]?.id ?? ""));
  }, []);

  const loadCharacters = useCallback(async (id: string) => {
    const list = await apiRequest<CharacterSummary[]>(`/api/v1/projects/${id}/characters`);
    setCharacters(list);
    setSelectedCharacterId((current) => (current && list.some((character) => character.id === current) ? current : list[0]?.id ?? ""));
  }, []);

  useEffect(() => {
    let active = true;
    apiRequest<Project[]>("/api/v1/projects")
      .then((list) => {
        if (!active) return;
        setApiOnline(true);
        setProjects(list);
        setProjectId(list[0]?.id ?? "");
      })
      .catch((error: Error) => {
        if (active) {
          setApiOnline(false);
          setPageError(error.message);
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!projectId) {
      setBooks([]);
      setSelectedBookId("");
      setCharacters([]);
      setSelectedCharacterId("");
      return;
    }

    let active = true;

    apiRequest<Book[]>(`/api/v1/projects/${projectId}/books`)
      .then((list) => {
        if (!active) return;
        setBooks(list);
        setSelectedBookId((current) => (current && list.some((book) => book.id === current) ? current : list[0]?.id ?? ""));
      })
      .catch((error: Error) => {
        if (active) setActionError(error.message);
      });

    apiRequest<CharacterSummary[]>(`/api/v1/projects/${projectId}/characters`)
      .then((list) => {
        if (!active) return;
        setCharacters(list);
        setSelectedCharacterId((current) => (current && list.some((character) => character.id === current) ? current : list[0]?.id ?? ""));
      })
      .catch((error: Error) => {
        if (active) setActionError(error.message);
      });

    return () => { active = false; };
  }, [projectId]);

  useEffect(() => {
    if (!filteredCharacters.length) return;
    if (!filteredCharacters.some((character) => character.id === selectedCharacterId)) {
      setSelectedCharacterId(filteredCharacters[0].id);
    }
  }, [filteredCharacters, selectedCharacterId]);

  useEffect(() => {
    if (!selectedBookId) {
      setChapters([]);
      setChapterDetail(null);
      return;
    }

    let active = true;

    apiRequest<Chapter[]>(`/api/v1/books/${selectedBookId}/chapters`)
      .then((list) => {
        if (!active) return;
        setChapters(list);
        if (list.length) {
          apiRequest<ChapterDetail>(`/api/v1/chapters/${list[0].id}`)
            .then((detail) => {
              if (active) {
                setChapterDetail(detail);
                setSelectedChunk(detail.chunks[0]?.chunk_index ?? 0);
              }
            })
            .catch((error: Error) => {
              if (active) setActionError(error.message);
            });
        } else {
          setChapterDetail(null);
        }
      })
      .catch((error: Error) => {
        if (active) setActionError(error.message);
      });

    return () => { active = false; };
  }, [selectedBookId]);

  useEffect(() => {
    if (!selectedCharacter) {
      setCharacterDraft("");
      return;
    }
    setCharacterDraft(selectedCharacter.description ?? "");
  }, [selectedCharacter]);

  useEffect(() => {
    if (!job || !["pending", "running"].includes(job.status)) return;
    let active = true;
    const timer = window.setInterval(() => {
      apiRequest<AnalysisJob>(`/api/v1/analysis-jobs/${job.id}`)
        .then((result) => {
          if (!active) return;
          setJob(result);
          if (["completed", "completed_with_errors", "failed"].includes(result.status)) {
            if (result.status !== "failed") void loadBooks(projectId);
          }
        })
        .catch((error: Error) => {
          if (active) setActionError(`Не удалось обновить статус анализа: ${error.message}`);
        });
    }, 2500);

    return () => { active = false; window.clearInterval(timer); };
  }, [job, loadBooks, projectId]);

  async function openChapter(chapter: Chapter) {
    setActionError("");
    setAnalysis(null);
    setSelectedChunk(0);
    try {
      const detail = await apiRequest<ChapterDetail>(`/api/v1/chapters/${chapter.id}`);
      setChapterDetail(detail);
      setSelectedChunk(detail.chunks[0]?.chunk_index ?? 0);
    } catch (error) {
      setActionError((error as Error).message);
    }
  }

  async function submitImport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setActionError("");
    setNotice("");
    const form = new FormData(event.currentTarget);
    const file = form.get("file");

    if (!(file instanceof File) || !file.size) {
      setActionError("Выберите файл книги для загрузки.");
      setBusy(false);
      return;
    }

    const existingProject = form.get("project_id") as string;

    try {
      let destinationId = existingProject;
      if (existingProject === "__new__") {
        const project = await apiRequest<Project>("/api/v1/projects", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: form.get("project_name"), description: form.get("description") || null }),
        });
        destinationId = project.id;
      }

      const upload = new FormData();
      upload.append("file", file);
      for (const field of ["title", "author", "language"]) {
        const value = form.get(field);
        if (typeof value === "string" && value.trim()) upload.append(field, value.trim());
      }

      const result = await apiRequest<{ book: Book; analysis: { job_id: string; status: string } }>(`/api/v1/projects/${destinationId}/books/import`, { method: "POST", body: upload });
      setProjectId(destinationId);
      setSelectedBookId(result.book.id);
      setJob({
        id: result.analysis.job_id,
        book_id: result.book.id,
        status: result.analysis.status,
        total_chunks: 0,
        processed_chunks: 0,
        failed_chunks: 0,
        input_tokens: 0,
        output_tokens: 0,
        total_tokens: 0,
        progress_percent: 0,
        current_chapter_number: null,
        current_chunk_index: null,
        report: null,
        last_error: null,
      });
      setAnalysis(null);
      setModalOpen(false);
      setNotice(`«${result.book.title}» загружена. Анализ уже запущен.`);
      await loadProjects();
      await loadBooks(destinationId);
    } catch (error) {
      setActionError(`Не удалось загрузить книгу: ${(error as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  async function analyzeCurrentChunk(save: boolean) {
    if (!selectedBook || !chapterDetail || !currentChunk) return;
    setBusy(true);
    setActionError("");
    setAnalysis(null);

    try {
      const action = save ? "chunk" : "preview";
      const result = await apiRequest<AnalysisResult>(`/api/v1/books/${selectedBook.id}/analyze/${action}?chapter_number=${chapterDetail.chapter_number}&chunk_index=${currentChunk.chunk_index}`, { method: "POST" });
      setAnalysis(result);
      if (save) {
        setNotice("Результат анализа сохранён в проект.");
        if (projectId) {
          await loadCharacters(projectId);
        }
      }
    } catch (error) {
      setActionError(`Анализ не выполнен: ${(error as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  async function reanalyzeBook() {
    if (!selectedBook) return;
    setBusy(true);
    setActionError("");
    setNotice("");

    try {
      const result = await apiRequest<{ job_id: string; status: string; message: string }>(`/api/v1/books/${selectedBook.id}/analyze/reanalyze`, {
        method: "POST",
      });

      setJob({
        id: result.job_id,
        book_id: selectedBook.id,
        status: "pending",
        total_chunks: 0,
        processed_chunks: 0,
        failed_chunks: 0,
        input_tokens: 0,
        output_tokens: 0,
        total_tokens: 0,
        progress_percent: 0,
        current_chapter_number: null,
        current_chunk_index: null,
        report: null,
        last_error: null,
      });

      setNotice(result.message || "Повторный анализ книги запущен.");
      if (projectId) {
        await loadCharacters(projectId);
      }
    } catch (error) {
      setActionError(`Не удалось запустить повторный анализ: ${(error as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  async function updateCharacterField(updates: Partial<Pick<CharacterSummary, "description" | "status" | "character_type" | "importance">>) {
    if (!selectedCharacter) return null;
    setBusy(true);
    setActionError("");
    try {
      const updated = await apiRequest<CharacterSummary>(`/api/v1/characters/${selectedCharacter.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updates),
      });
      setCharacters((current) => current.map((character) => (character.id === updated.id ? updated : character)));
      setSelectedCharacterId(updated.id);
      return updated;
    } catch (error) {
      setActionError(`Не удалось обновить персонажа: ${(error as Error).message}`);
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function saveCharacterDescription() {
    if (!selectedCharacter) return;
    const updated = await updateCharacterField({ description: characterDraft.trim() || null });
    if (updated) {
      setNotice("Описание персонажа сохранено.");
    }
  }

  async function updateCharacterStatus(nextStatus: string) {
    const updated = await updateCharacterField({ status: nextStatus });
    if (updated) {
      setNotice(`Статус персонажа обновлён на ${nextStatus}.`);
    }
  }

  async function createCharacterReference(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedCharacter || !characterReferenceDraft.title.trim()) {
      setActionError("Нужно указать заголовок ссылки.");
      return;
    }

    setBusy(true);
    setActionError("");
    try {
      const reference = await apiRequest<CharacterReference>(`/api/v1/characters/${selectedCharacter.id}/references`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: characterReferenceDraft.title.trim(),
          url: characterReferenceDraft.url.trim() || null,
          notes: characterReferenceDraft.notes.trim() || null,
        }),
      });

      setCharacters((current) =>
        current.map((character) =>
          character.id === selectedCharacter.id
            ? { ...character, references: [reference, ...character.references] }
            : character,
        ),
      );
      setCharacterReferenceDraft({ title: "", url: "", notes: "" });
      setNotice("Ссылка на источник добавлена.");
    } catch (error) {
      setActionError(`Не удалось добавить ссылку: ${(error as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  async function deleteCharacterReference(referenceId: string) {
    if (!selectedCharacter) return;
    setBusy(true);
    setActionError("");
    try {
      await apiRequest<{ status: string; id: string }>(`/api/v1/characters/${selectedCharacter.id}/references/${referenceId}`, {
        method: "DELETE",
      });

      setCharacters((current) =>
        current.map((character) =>
          character.id === selectedCharacter.id
            ? { ...character, references: character.references.filter((reference) => reference.id !== referenceId) }
            : character,
        ),
      );
      setNotice("Ссылка удалена.");
    } catch (error) {
      setActionError(`Не удалось удалить ссылку: ${(error as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  const completedJob = job && ["completed", "completed_with_errors", "failed"].includes(job.status);

  return (
    <main className="app-shell">
      <aside className={`sidebar ${mobileNavOpen ? "sidebar-open" : ""}`}>
        <a className="brand" href="#" aria-label="AI Series — главная">
          <span className="brand-mark"><Icon name="books" size={22} /></span>
          <span className="brand-name">story<span>foundry</span></span>
        </a>

        <div className="workspace-picker">
          <span className="workspace-avatar">{selectedProject?.name.slice(0, 1).toUpperCase() ?? "S"}</span>
          <span className="workspace-name">{selectedProject?.name ?? "Моя студия"}</span>
          <Icon name="chevron" size={16} />
        </div>

        <div className="nav-caption">РАБОЧЕЕ ПРОСТРАНСТВО</div>
        <nav className="main-nav" aria-label="Основная навигация">
          <a className="nav-item active" href="#overview" onClick={() => setMobileNavOpen(false)}><Icon name="grid" size={18} />Обзор</a>
          <a className="nav-item" href={projects.length ? "#library" : undefined} onClick={() => { if (!projects.length) openImportModal(); setMobileNavOpen(false); }}><Icon name="books" size={18} />Библиотека<span className="nav-count">{books.length}</span></a>
        </nav>

        <div className="sidebar-projects-head">
          <span className="nav-caption">МОИ ПРОЕКТЫ</span>
          <button className="icon-button subtle" onClick={openImportModal} aria-label="Добавить проект"><Icon name="plus" size={17} /></button>
        </div>

        <div className="project-list">
          {projects.map((project, index) => (
            <button key={project.id} className={`project-link ${project.id === projectId ? "selected" : ""}`} onClick={() => { setProjectId(project.id); setMobileNavOpen(false); }}>
              <span className={`project-dot dot-${index % 4}`} />{project.name}
            </button>
          ))}
          {!projects.length && !loading && <div className="sidebar-empty">Здесь появятся ваши проекты</div>}
        </div>

        <div className="sidebar-bottom">
          <span className="profile-avatar">А</span>
          <span className="profile-name">Автор</span>
          <span className="profile-plan">БЕТА</span>
        </div>
      </aside>

      <section className="main-area">
        <header className="topbar">
          <button className="mobile-menu icon-button" aria-label="Открыть меню" onClick={() => setMobileNavOpen(!mobileNavOpen)}><Icon name="menu" /></button>
          <div className="breadcrumbs"><span>Рабочее пространство</span><Icon name="chevron" size={14} /><strong>{selectedProject?.name ?? "Обзор"}</strong></div>
          <div className="topbar-right">
            <span className={`connection-indicator ${apiOnline === false ? "connection-offline" : ""}`}><i />{apiOnline === false ? "Нет соединения" : apiOnline ? "API подключён" : "Подключение…"}</span>
            <button className="avatar-button" aria-label="Профиль">А</button>
          </div>
        </header>

        <div className="content" id="overview">
          <div className="page-heading">
            <div>
              <div className="date-line">СТУДИЯ ИСТОРИЙ <span className="date-divider" /> ВАША БИБЛИОТЕКА</div>
              <h1>Добро пожаловать<span className="heading-period">.</span></h1>
              <p className="page-subtitle">Превратите любимые книги в истории, которые хочется увидеть.</p>
            </div>
            <button className="primary-button" onClick={openImportModal}><Icon name="plus" size={18} />Добавить книгу</button>
          </div>

          {(pageError || actionError || notice) && (
            <div className={`alert ${pageError || actionError ? "alert-error" : "alert-success"}`} role="status">
              <span>{pageError || actionError || notice}</span>
              <button className="alert-close" aria-label="Закрыть" onClick={() => { setPageError(""); setActionError(""); setNotice(""); }}><Icon name="close" size={16} /></button>
            </div>
          )}

          {job && selectedBook?.id === job.book_id && (
            <section className={`analysis-banner ${completedJob ? "analysis-done" : ""}`}>
              <div className="analysis-icon"><Icon name={completedJob ? (job.status === "failed" ? "close" : "check") : "sparkles"} size={19} /></div>
              <div className="analysis-copy">
                <strong>{job.status === "failed" ? "Не удалось завершить анализ" : completedJob ? "Анализ книги завершён" : "ИИ изучает вашу книгу"}</strong>
                <span>{job.status === "failed" ? job.last_error : completedJob ? `${formatNumber(job.processed_chunks)} фрагментов обработано${job.failed_chunks ? ` · ${job.failed_chunks} с ошибкой` : ""}` : `Фрагмент ${job.processed_chunks + 1}${job.total_chunks ? ` из ${formatNumber(job.total_chunks)}` : ""} · это может занять несколько минут`}</span>
                <span>Токены: {formatNumber(job.total_tokens)} всего · {formatNumber(job.input_tokens)} входящих · {formatNumber(job.output_tokens)} исходящих</span>
              </div>
              {!completedJob && <div className="analysis-progress"><div className="progress-track"><span style={{ width: `${job.progress_percent}%` }} /></div><b>{job.progress_percent}%</b></div>}
            </section>
          )}

          {loading ? (
            <div className="loading-state"><span className="spinner" />Загружаем вашу студию…</div>
          ) : !projects.length ? (
            <section className="welcome-card">
              <div className="welcome-art"><span className="art-glow" /><div className="art-book"><Icon name="books" size={52} /></div><span className="art-sparkle sparkle-one">✦</span><span className="art-sparkle sparkle-two">✧</span></div>
              <div className="welcome-copy"><span className="section-kicker">НАЧНИТЕ С ПЕРВОЙ ИСТОРИИ</span><h2>Каждый большой мир<br />начинается с книги.</h2><p>Загрузите произведение — ИИ выделит персонажей, события и связи между ними.</p><button className="primary-button" onClick={openImportModal}><Icon name="upload" size={17} />Загрузить первую книгу<Icon name="arrow" size={16} /></button></div>
            </section>
          ) : (
            <>
              <section className="stats-grid" aria-label="Статистика проекта">
                <article className="stat-card stat-highlight"><div className="stat-label">КНИГИ В ПРОЕКТЕ <span className="stat-icon"><Icon name="books" size={17} /></span></div><div className="stat-value">{formatNumber(books.length)}<span className="stat-unit">книг</span></div><div className="stat-foot"><span className="stat-dot" />В вашей библиотеке</div></article>
                <article className="stat-card"><div className="stat-label">ГЛАВЫ <span className="stat-icon"><Icon name="file" size={17} /></span></div><div className="stat-value">{formatNumber(books.reduce((sum, book) => sum + book.chapters_count, 0))}<span className="stat-unit">глав</span></div><div className="stat-foot">Изучаемые произведения</div></article>
                <article className="stat-card"><div className="stat-label">АКТИВНЫЙ ПРОЕКТ <span className="stat-icon"><Icon name="sparkles" size={17} /></span></div><div className="stat-project-name">{selectedProject?.name ?? "—"}</div><div className="stat-foot"><span className="status-dot" />Проект активен</div></article>
              </section>

              <div className="section-heading" id="library">
                <div><div className="section-kicker">ВАШИ ПРОИЗВЕДЕНИЯ</div><h2>Библиотека</h2></div>
                <div className="library-tools">
                  <label className="search-box"><Icon name="search" size={17} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Найти книгу..." aria-label="Найти книгу" /><kbd>⌘ K</kbd></label>
                  <button className="text-button" onClick={openImportModal}>Добавить <Icon name="arrow" size={15} /></button>
                </div>
              </div>

              {filteredBooks.length ? (
                <div className="book-grid">
                  {filteredBooks.map((book, index) => (
                    <button className={`book-card ${book.id === selectedBookId ? "book-selected" : ""}`} key={book.id} onClick={() => { setSelectedBookId(book.id); setJob((current) => current?.book_id === book.id ? current : null); setAnalysis(null); }}>
                      <div className={`book-cover cover-${index % 4}`}>
                        <span className="cover-topline">STORYFOUNDRY · LIBRARY</span><span className="cover-ornament">✳</span><span className="cover-title">{book.title}</span><span className="cover-author">{book.author || "Автор не указан"}</span><span className="cover-bottomline">· · ·</span>
                        <span className="book-format">{book.language?.toUpperCase() ?? "КНИГА"}</span>
                      </div>
                      <div className="book-card-meta"><div><strong>{book.title}</strong><span>{book.author || "Автор не указан"}</span></div><Icon name="arrow" size={17} /></div>
                      <div className="book-card-details"><span><Icon name="file" size={14} />{book.chapters_count} глав</span><span>{book.status === "analyzed" ? "Проанализирована" : book.source_filename ?? "В библиотеке"}</span></div>
                    </button>
                  ))}
                  <button className="add-book-card" onClick={openImportModal}><span className="add-book-icon"><Icon name="plus" size={22} /></span><strong>Добавить книгу</strong><span>Расширьте свою библиотеку</span></button>
                </div>
              ) : (
                <div className="empty-search"><Icon name="search" size={22} /><span>Книг по запросу «{search}» не найдено</span></div>
              )}

              {characters.length > 0 && (
                <section className="character-section">
                  <div className="section-heading reader-heading">
                    <div><div className="section-kicker">ПЕРСОНАЖИ</div><h2>Карточки персонажей</h2></div>
                    <div className="reader-book-meta">
                      <span>{formatNumber(characters.length)} персонажей</span>
                      <span className="status-pip status-affirmed" />
                      <span>{characterStatusCounts.confirmed ?? 0} подтверждены</span>
                    </div>
                  </div>

                  <div className="character-subtabs" aria-label="Вкладки персонажей">
                    <button type="button" className={characterSubTab === "cards" ? "active" : ""} onClick={() => setCharacterSubTab("cards")}>Карточки</button>
                    <button type="button" className={characterSubTab === "detail" ? "active" : ""} onClick={() => setCharacterSubTab("detail")} disabled={!selectedCharacter}>Детали</button>
                  </div>

                  <div className="character-tools">
                    <label className="search-box"><Icon name="search" size={17} /><input value={characterSearch} onChange={(event) => setCharacterSearch(event.target.value)} placeholder="Поиск персонажей..." aria-label="Поиск персонажей" /></label>
                    <select value={characterRoleFilter} onChange={(event) => setCharacterRoleFilter(event.target.value)} aria-label="Фильтр по роли">
                      <option value="all">Все роли</option>
                      {characterRoleOptions.map((role) => <option key={role} value={role}>{role}</option>)}
                    </select>
                    <div className="status-pills">
                      {(["all", "confirmed", "pending", "discovered"] as const).map((status) => (
                        <button key={status} type="button" className={characterStatusFilter === status ? "active" : ""} onClick={() => setCharacterStatusFilter(status)}>
                          {status === "all" ? "Все" : status === "confirmed" ? "Подтверждены" : status === "pending" ? "На проверке" : "Обнаружены"}
                        </button>
                      ))}
                    </div>
                  </div>

                  {characterSubTab === "cards" ? (
                    filteredCharacters.length ? (
                      <div className="character-card-grid">
                        {filteredCharacters.map((character) => (
                          <button key={character.id} type="button" className={`character-card ${selectedCharacter?.id === character.id ? "selected" : ""}`} onClick={() => { setSelectedCharacterId(character.id); setCharacterSubTab("detail"); }}>
                            <div className="character-card-header">
                              <span className="character-avatar large-avatar">{character.canonical_name.slice(0, 1).toUpperCase()}</span>
                              <span className={`character-status-badge ${character.status === "confirmed" ? "status-confirmed" : character.status === "pending" ? "status-pending" : "status-discovered"}`}>
                                {character.status === "confirmed" ? "Подтверждён" : character.status === "pending" ? "На проверке" : "Обнаружен"}
                              </span>
                            </div>
                            <strong>{character.canonical_name}</strong>
                            <span className="character-card-role">{character.character_type} · {character.importance}</span>
                            <div className="character-card-meta">
                              <span>{character.references.length} ссылок</span>
                              <span>{character.aliases.length} псевд.</span>
                            </div>
                          </button>
                        ))}
                      </div>
                    ) : (
                      <div className="empty-search"><Icon name="search" size={22} /><span>Персонажи по запросу не найдены</span></div>
                    )
                  ) : selectedCharacter ? (
                    <article className="character-editor">
                      <div className="character-editor-header">
                        <div>
                          <div className="section-kicker">ПЕРСОНАЖ</div>
                          <h3>{selectedCharacter.canonical_name}</h3>
                        </div>
                        <span className="character-badge">{selectedCharacter.character_type}</span>
                      </div>

                      <div className="character-meta-grid">
                        <label className="meta-field">
                          <span>Тип</span>
                          <select value={selectedCharacter.character_type} onChange={(event) => void updateCharacterField({ character_type: event.target.value })}>
                            <option value="person">person</option>
                            <option value="npc">npc</option>
                            <option value="antagonist">antagonist</option>
                            <option value="ally">ally</option>
                            <option value="group">group</option>
                          </select>
                        </label>

                        <label className="meta-field">
                          <span>Важность</span>
                          <select value={selectedCharacter.importance} onChange={(event) => void updateCharacterField({ importance: event.target.value })}>
                            <option value="primary">primary</option>
                            <option value="secondary">secondary</option>
                            <option value="supporting">supporting</option>
                            <option value="minor">minor</option>
                          </select>
                        </label>

                        <div className="status-inline-picker">
                          <span>Статус:</span>
                          {(["confirmed", "pending", "discovered"] as const).map((status) => (
                            <button key={status} type="button" className={selectedCharacter.status === status ? "active" : ""} onClick={() => void updateCharacterStatus(status)}>
                              {status === "confirmed" ? "Подтверждён" : status === "pending" ? "На проверке" : "Обнаружен"}
                            </button>
                          ))}
                        </div>
                      </div>

                      <label className="editor-field">Описание персонажа
                        <textarea value={characterDraft} onChange={(event) => setCharacterDraft(event.target.value)} rows={7} placeholder="Опишите персонажа, его цели, мотивы и историю..." />
                      </label>

                      <div className="character-actions">
                        <button className="secondary-button" type="button" onClick={() => setCharacterDraft(selectedCharacter.description ?? "")}>Сбросить</button>
                        <button className="primary-button compact-button" type="button" onClick={() => void saveCharacterDescription()} disabled={busy}>Сохранить описание</button>
                      </div>

                      <div className="reference-section">
                        <div className="reference-head">
                          <h4>Ссылки и источники</h4>
                          <span>{selectedCharacter.references.length} шт.</span>
                        </div>

                        <div className="reference-list">
                          {selectedCharacter.references.length ? (
                            selectedCharacter.references.map((reference) => (
                              <div key={reference.id} className="reference-item">
                                <div className="reference-copy">
                                  <strong>{reference.title}</strong>
                                  {reference.url && <a href={reference.url} target="_blank" rel="noreferrer">{reference.url}</a>}
                                  {reference.notes && <p>{reference.notes}</p>}
                                </div>
                                <button type="button" className="text-button danger" onClick={() => void deleteCharacterReference(reference.id)}>Удалить</button>
                              </div>
                            ))
                          ) : (
                            <p className="empty-state-text">Ссылок пока нет.</p>
                          )}
                        </div>

                        <form className="reference-form" onSubmit={(event) => void createCharacterReference(event)}>
                          <div className="reference-grid">
                            <label>
                              Заголовок
                              <input value={characterReferenceDraft.title} onChange={(event) => setCharacterReferenceDraft((current) => ({ ...current, title: event.target.value }))} placeholder="Название источника" />
                            </label>
                            <label>
                              Ссылка
                              <input value={characterReferenceDraft.url} onChange={(event) => setCharacterReferenceDraft((current) => ({ ...current, url: event.target.value }))} placeholder="https://..." />
                            </label>
                          </div>
                          <label>
                            Комментарий
                            <textarea value={characterReferenceDraft.notes} onChange={(event) => setCharacterReferenceDraft((current) => ({ ...current, notes: event.target.value }))} rows={3} placeholder="Кратко что важно в этом источнике" />
                          </label>
                          <div className="character-actions">
                            <button className="primary-button compact-button" type="submit" disabled={busy}>Добавить ссылку</button>
                          </div>
                        </form>
                      </div>

                      <div className="character-detail-grid">
                        <div className="character-panel">
                          <h4>Псевдонимы</h4>
                          {selectedCharacter.aliases.length ? (
                            <ul>{selectedCharacter.aliases.map((alias) => <li key={alias.id}>{alias.alias}</li>)}</ul>
                          ) : <p>Псевдонимы не указаны.</p>}
                        </div>
                        <div className="character-panel">
                          <h4>Атрибуты</h4>
                          {selectedCharacter.attributes.length ? (
                            <ul>{selectedCharacter.attributes.map((attribute) => <li key={attribute.id}><strong>{attribute.key}:</strong> {attribute.value}</li>)}</ul>
                          ) : <p>Атрибуты не указаны.</p>}
                        </div>
                        <div className="character-panel">
                          <h4>Упоминания</h4>
                          {selectedCharacter.mentions.length ? (
                            <ul>{selectedCharacter.mentions.slice(0, 5).map((mention) => <li key={mention.id}>{mention.mention_text}</li>)}</ul>
                          ) : <p>Упоминания не найдены.</p>}
                        </div>
                        <div className="character-panel">
                          <h4>Связи</h4>
                          {selectedCharacter.relationships.length || selectedCharacter.target_relationships.length ? (
                            <ul>{[...selectedCharacter.relationships, ...selectedCharacter.target_relationships].slice(0, 6).map((relationship, index) => <li key={`${relationship.id}-${index}`}>{relationship.relationship_type}: {relationship.target_character_name ?? relationship.source_character_name ?? "неизвестно"}</li>)}</ul>
                          ) : <p>Связи не найдены.</p>}
                        </div>
                      </div>
                    </article>
                  ) : (
                    <div className="empty-search"><Icon name="search" size={22} /><span>Персонаж не выбран</span></div>
                  )}
                </section>
              )}

              {selectedBook && (
                <section className="reader-section">
                  <div className="section-heading reader-heading">
                    <div><div className="section-kicker">ИЗУЧЕНИЕ ПРОИЗВЕДЕНИЯ</div><h2>{selectedBook.title}</h2></div>
                    <div className="reader-book-meta">
                      <span>{selectedBook.author || "Автор не указан"}</span>
                      <span />
                      <span>{formatNumber(selectedBook.chapters_count)} глав</span>
                    </div>
                  </div>
                  <div className="reader-toolbar">
                    <button className="secondary-button" type="button" onClick={() => void reanalyzeBook()} disabled={busy}>
                      <Icon name="refresh" size={16} />Переанализировать книгу
                    </button>
                  </div>                  <div className="reader-layout">
                    <aside className="chapter-sidebar">
                      <div className="chapter-sidebar-title"><span>СОДЕРЖАНИЕ</span><span>{chapters.length} глав</span></div>
                      <div className="chapter-list">
                        {chapters.map((chapter) => (
                          <button key={chapter.id} className={`chapter-link ${chapter.id === chapterDetail?.id ? "chapter-active" : ""}`} onClick={() => void openChapter(chapter)}>
                            <span className="chapter-number">{String(chapter.chapter_number).padStart(2, "0")}</span><span className="chapter-title">{chapter.title || `Глава ${chapter.chapter_number}`}</span><Icon name="chevron" size={15} />
                          </button>
                        ))}
                        {!chapters.length && <div className="chapter-empty">Главы пока не найдены</div>}
                      </div>
                    </aside>
                    <article className="reader-card">
                      {chapterDetail ? (
                        <>
                          <div className="reader-card-top"><div><span className="chapter-eyebrow">ГЛАВА {String(chapterDetail.chapter_number).padStart(2, "0")}</span><h3>{chapterDetail.title || `Глава ${chapterDetail.chapter_number}`}</h3></div><div className="reader-actions"><span className="reading-time"><Icon name="clock" size={14} />{formatSize(chapterDetail.text.length)}</span></div></div>
                          <div className="chapter-content">{chapterDetail.text}</div>
                          <div className="chunk-toolbar">
                            <label>Фрагмент текста
                              <select value={selectedChunk} onChange={(event) => { setSelectedChunk(Number(event.target.value)); setAnalysis(null); }}>
                                {chapterDetail.chunks.map((chunk) => <option key={chunk.id} value={chunk.chunk_index}>Фрагмент {chunk.chunk_index + 1} · {formatNumber(chunk.token_count)} токенов</option>)}
                              </select>
                            </label>
                            <div className="chunk-buttons">
                              <button className="secondary-button" disabled={busy || !currentChunk} onClick={() => void analyzeCurrentChunk(false)}><Icon name="sparkles" size={16} />Предпросмотр</button>
                              <button className="primary-button compact-button" disabled={busy || !currentChunk} onClick={() => void analyzeCurrentChunk(true)}>{busy ? <span className="button-spinner" /> : <Icon name="check" size={16} />}Сохранить анализ</button>
                            </div>
                          </div>
                          {analysis && (
                            <div className="analysis-result">
                              <div className="result-heading"><span className="result-check"><Icon name="check" size={15} /></span><strong>{analysis.saved_to_database ? "Результат сохранён" : "Предпросмотр анализа"}</strong></div>
                              {analysis.usage && <p className="result-note">Токены: {formatNumber(analysis.usage.total_tokens)} всего · {formatNumber(analysis.usage.input_tokens)} входящих · {formatNumber(analysis.usage.output_tokens)} исходящих</p>}
                              {analysis.analysis?.characters?.length ? <div className="character-results">{analysis.analysis.characters.map((character, index) => <div className="character-result" key={`${character.name}-${index}`}><span className="character-initial">{character.name.slice(0, 1).toUpperCase()}</span><div><strong>{character.name}</strong><span>{character.description || `${character.character_type} · ${character.importance}`}</span></div>{character.aliases.length > 0 && <small>{character.aliases.join(", ")}</small>}</div>)}</div> : null}
                              {analysis.report && <pre className="report-json">{JSON.stringify(analysis.report, null, 2)}</pre>}
                              {!analysis.analysis?.characters?.length && !analysis.report && <p className="result-note">Анализ выполнен успешно.</p>}
                            </div>
                          )}
                        </>
                      ) : (
                        <div className="reader-empty"><Icon name="file" size={28} /><strong>Выберите главу</strong><span>Текст главы появится здесь</span></div>
                      )}
                    </article>
                  </div>
                </section>
              )}
            </>
          )}

          <footer className="page-footer"><span>STORYFOUNDRY</span><span>Создано для историй, которые остаются.</span><span>© 2026</span></footer>
        </div>
      </section>

      {modalOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) setModalOpen(false); }}>
          <section className="import-modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
            <div className="modal-header"><div><span className="section-kicker">ПОПОЛНИТЕ БИБЛИОТЕКУ</span><h2 id="modal-title">Добавить книгу</h2><p>Загрузите текст — мы подготовим его к анализу.</p></div><button className="icon-button modal-close" aria-label="Закрыть окно" disabled={busy} onClick={() => setModalOpen(false)}><Icon name="close" /></button></div>
            <form className="import-form" onSubmit={(event) => void submitImport(event)}>
              <label className="form-field">Проект
                <select name="project_id" defaultValue={projectId || "__new__"} onChange={(event) => setCreateNewProject(event.target.value === "__new__")}>
                  {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
                  <option value="__new__">＋ Создать новый проект</option>
                </select>
              </label>
              <div id="new-project-fields" hidden={!createNewProject} className="new-project-fields">
                <label className="form-field">Название проекта<input name="project_name" placeholder="Например, «Миры Достоевского»" required={createNewProject} /></label>
                <label className="form-field">Описание проекта <span className="optional">необязательно</span><textarea name="description" rows={2} placeholder="О чём ваша история?" /></label>
              </div>
              <div className="form-row">
                <label className="form-field">Название книги <span className="optional">необязательно</span><input name="title" placeholder="Определится по файлу" /></label>
                <label className="form-field">Автор <span className="optional">необязательно</span><input name="author" placeholder="Имя автора" /></label>
              </div>
              <label className="form-field">Язык текста <span className="optional">необязательно</span><input name="language" placeholder="ru, en…" /></label>
              <label className="upload-zone">
                <input name="file" type="file" accept=".txt,.epub,.pdf,.docx" required />
                <span className="upload-icon"><Icon name="upload" size={21} /></span>
                <strong>Выберите файл книги</strong>
                <span>TXT, EPUB, PDF или DOCX</span>
              </label>
              <div className="modal-footer"><span><Icon name="sparkles" size={15} />Анализ персонажей запустится автоматически</span><button className="primary-button" type="submit" disabled={busy}>{busy ? <><span className="button-spinner" />Загружаем…</> : <>Загрузить книгу<Icon name="arrow" size={16} /></>}</button></div>
            </form>
          </section>
        </div>
      )}
    </main>
  );
}
