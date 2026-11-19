import io
from dataclasses import dataclass

from bs4 import BeautifulSoup
from docx import Document
from ebooklib import epub, ITEM_DOCUMENT
from pypdf import PdfReader


SUPPORTED_FORMATS = {".epub", ".pdf", ".docx", ".txt"}


@dataclass
class ExtractedBook:
    text: str
    title: str | None = None
    author: str | None = None
    language: str | None = None


def extract_txt(data: bytes) -> str:
    encodings = ("utf-8-sig", "utf-8", "cp1251", "latin-1")

    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue

    raise ValueError("Не удалось определить кодировку TXT-файла")


def extract_pdf(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))

    if reader.is_encrypted:
        raise ValueError("PDF защищён паролем. Защищённые файлы пока не поддерживаются.")

    pages = []

    for page in reader.pages:
        page_text = page.extract_text() or ""
        if page_text.strip():
            pages.append(page_text)

    text = "\n\n".join(pages)

    if not text.strip():
        raise ValueError(
            "В PDF не найден текст. Возможно, это сканированные страницы. "
            "Для таких файлов потребуется OCR."
        )

    return text


def extract_docx(data: bytes) -> str:
    document = Document(io.BytesIO(data))
    paragraphs = []

    for paragraph in document.paragraphs:
        value = paragraph.text.strip()

        if value:
            paragraphs.append(value)

    text = "\n\n".join(paragraphs)

    if not text.strip():
        raise ValueError("В DOCX-файле не найден текст")

    return text


def extract_epub(data: bytes) -> ExtractedBook:
    book = epub.read_epub(io.BytesIO(data))

    title = None
    author = None
    language = None

    titles = book.get_metadata("DC", "title")
    authors = book.get_metadata("DC", "creator")
    languages = book.get_metadata("DC", "language")

    if titles:
        title = titles[0][0]

    if authors:
        author = authors[0][0]

    if languages:
        language = languages[0][0]

    sections = []

    for item in book.get_items():
        if item.get_type() != ITEM_DOCUMENT:
            continue

        soup = BeautifulSoup(item.get_content(), "html.parser")

        for element in soup(["script", "style", "nav"]):
            element.decompose()

        section_text = soup.get_text(separator="\n", strip=True)

        if section_text:
            sections.append(section_text)

    text = "\n\n".join(sections)

    if not text.strip():
        raise ValueError("В EPUB-файле не найден текст")

    return ExtractedBook(
        text=text,
        title=title,
        author=author,
        language=language,
    )


def extract_book(filename: str, data: bytes) -> ExtractedBook:
    extension = filename.lower().rsplit(".", 1)[-1]
    extension = f".{extension}"

    if extension not in SUPPORTED_FORMATS:
        raise ValueError(
            f"Формат {extension} не поддерживается. "
            "Поддерживаются EPUB, PDF, DOCX и TXT."
        )

    if extension == ".txt":
        text = extract_txt(data)

    elif extension == ".pdf":
        text = extract_pdf(data)

    elif extension == ".docx":
        text = extract_docx(data)

    elif extension == ".epub":
        return extract_epub(data)

    else:
        raise ValueError("Неизвестный формат книги")

    if not text.strip():
        raise ValueError("Файл не содержит текста")

    return ExtractedBook(text=text)