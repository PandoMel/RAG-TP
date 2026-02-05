# ARCHITECTURE.md
## Масштабируемая Docker RAG-система (Upload + NAS) с Hybrid Search (BM25+Vector), Rerank и OCR/MinerU

Дата: 2026-02-05

> Ключевая идея: **API — легкий**, вся тяжелая обработка (парсинг/OCR/эмбеддинги/индексация) — **только в worker** через очередь задач. NAS — **строго read-only**.

---

## 1) Цели и режимы работы

### 1.1 Цели
Система должна:
- отвечать на вопросы пользователя по содержимому документов;
- поддерживать поиск по:
  1) **загруженному файлу** (режим по умолчанию: “одноразовый”, с TTL);
  2) **файловому серверу NAS** (проиндексированный корпус);
- иметь **гибридный поиск** (BM25 + векторы) + **reranker**;
- иметь web-интерфейс в стиле чат-UI;
- отображать **очередь серверных задач** (чтобы пользователи видели нагрузку);
- иметь **/admin** (скрыт флагом) для настройки источников NAS и расписаний;
- быть безопасной и расширяемой;
- иметь API для интеграции с другими контейнерами (LLM, OCR, reranker).

### 1.2 Вкладки UI
**Вкладка 1 (по умолчанию): “Поиск по файлу”**
- Пользователь загружает файл (pdf/docx/xlsx/txt по whitelist)
- Файл обрабатывается и индексируется **временно** (TTL) в Postgres
- Пользователь задает вопросы; ответы содержат источники (цитаты) с кнопками **Открыть/Скачать**
- После TTL документ исчезает; при попытке открыть — “Файл не найден. Загрузите заново.”

**Вкладка 2: “Поиск по файловому серверу (NAS)”**
- Пользователь выбирает один или несколько **источников** (multi-select)
- Опционально ограничивает **подкаталогом** (например `Бухгалтерия/Отчеты`)
- Задает вопрос; retrieval идет по проиндексированному NAS корпусу

**Вкладка 3 (будущее): “Распознать документ”**
- Цель: получить максимально точный текст из PDF и **скачать результат**
- Это НЕ RAG: **не создаем векторный/BM25 индекс** (по умолчанию), не используем поиск
- Pipeline: MinerU/PaddleOCR → текст → LLM-постобработка + проверка качества → экспорт (md/txt/docx)
- Для “почти 100%” качества нужна валидация **по оригинальным изображениям страниц** (vision LLM / VLM).

---

## 2) Технологический стек

- **Backend API:** FastAPI (Python)
- **Worker/Queue:** Celery + Redis (рекомендуется) *или* RQ (допустимо). В ТЗ — очередь обязательна.
- **DB:** PostgreSQL
  - **pgvector** для embeddings
  - **ParadeDB pg_search** для BM25
- **Embeddings:** BGE-M3 (dense embeddings) — один сервис/реализация (в worker)
- **Reranker:** отдельный сервис `/v1/rerank` (другая модель, не BGE‑M3)
- **OCR:** PaddleOCR (отдельный контейнер), используется по необходимости
- **PDF layout/markdown parser:** MinerU (отдельный контейнер) — предпочтительно для сложных PDF
- **LLM:** отдельный контейнер `/v1/chat` (внешний или в compose), streaming — позже
- **UI:** web-чат (можно SSR/SPA; реализация на усмотрение, но UX фиксирован ТЗ)

Ограничения железа: Xeon CPU + **GTX 1060 6GB**  
→ важны лимиты, таймауты, каскадный подход и “GPU-lock” (см. ниже).

---

## 3) Контейнеры (Docker Compose)

### 3.1 Сервисы
- `api`
  - FastAPI + Web UI
  - принимает upload, создает job, отдает статусы, выполняет retrieval+LLM chat
  - отдаёт файлы `view/download` по `document_id` (только чтение)
  - `/admin` доступен только если `ADMIN_UI_ENABLED=true`

- `worker`
  - Celery worker (или аналог) — выполняет ingestion, индексацию, очистку TTL, NAS scan
  - парсинг/ocr/embeddings — только здесь
  - содержит GPU-lock для задач, если GPU используется

- `redis`
  - брокер/очередь задач

- `postgres`
  - хранение документов/чанков/jobs
  - расширения: `pgvector`, `pg_search`
  - индексы BM25 и vector

- `reranker`
  - REST `/v1/rerank`
  - cross-encoder модель (например семейство bge-reranker)
  - независимое масштабирование

- `ocr`
  - REST OCR (PaddleOCR)
  - используется worker’ом по необходимости

- `mineru`
  - REST (обертка) или CLI-сервис для PDF → markdown
  - используется worker’ом в каскаде

- `llm`
  - внешний или внутренний сервис, должен поддерживать `/v1/chat`
  - streaming поддержка планируется (SSE), но не обязательна для v1

### 3.2 NAS (SMB) доступ внутри контейнеров
Требование: “монтировать через SMB внутри контейнера”, чтобы переносимость была высокой.

- `worker` должен читать NAS (для индексации)
- `api` должен читать NAS (для “Открыть/Скачать”)

⚠️ В Docker для SMB mount внутри контейнера могут понадобиться доп. права (capabilities).  
В README/TASK должно быть явно указано: если mount внутри контейнера требует privileges — задокументировать и предложить альтернативу (mount на хосте).

---

## 4) Потоки данных

### 4.1 Upload (temp TTL) — “Поиск по файлу”
1. UI → `POST /v1/files/upload` (multipart)
2. API:
   - сохраняет файл в temp storage (локальный volume)
   - создает `documents(scope=temp, status=queued, expires_at=now+TTL)`
   - создает `jobs(type=ingest_document, status=queued)`
   - ставит задачу в очередь → worker
3. Worker выполняет ingestion (см. pipeline ниже) и пишет `job_steps`
4. UI опрашивает `GET /v1/jobs/{id}` и показывает прогресс + шаги
5. После `documents.status=ready` пользователь задает вопросы через чат (`/v1/chat`)
6. Очистка: периодический job удаляет temp документы после TTL

### 4.2 NAS indexing — “Поиск по файловому серверу”
1. Админ добавляет `sources` (корни) через `/admin` (или API)
2. Scheduler в worker по cron:
   - `scan_source_incremental(source_id)` часто
   - `scan_source_full_audit(source_id)` редко
3. Для каждого измененного/нового файла → `ingest_document(document_id)`

### 4.3 QA / Chat
1. UI → `POST /v1/chat` (query + filters)
2. API:
   - Hybrid retrieval: BM25 topK + vector topK + fusion (RRF)
   - Rerank topN через `/v1/rerank`
   - Формирует контекст + список источников (citations)
   - Вызывает LLM `/v1/chat` → ответ
3. Возвращает:
   - `answer`
   - `citations[]` (doc_id, title, path, page/sheet, snippet)
4. UI показывает ответ + список источников с кнопками `Открыть/Скачать`.

---

## 5) Ingestion pipeline (worker)

### 5.1 Общие шаги (jobs + job_steps)
Каждая ingestion job должна писать:
- `jobs.progress` 0..100
- `jobs.current_step`, `jobs.message`
- детализацию в `job_steps`

Рекомендуемые шаги:
1) `received` / `queued_for_worker`
2) `mount_check` (NAS) / `temp_storage_check`
3) `file_open_readonly`
4) `file_validate` (whitelist, size, limits)
5) `detect_doc_type`
6) `extract_text` (см. каскад PDF)
7) `normalize_text`
8) `chunking`
9) `embedding`
10) `db_write`
11) `index_update_bm25`
12) `index_update_vector`
13) `finalize` (documents.status=ready)
14) `cleanup_tmp`

Ошибки должны ставить `jobs.status=failed`, плюс `documents.status=failed` и `error_code`.

### 5.2 Каскадная обработка PDF (multi-approach под 1060 6GB)
Цель: качество без лишних затрат.

Алгоритм:
1) **Builtin text extraction** (PyMuPDF/pypdf) → `text_a`, `quality_a`
2) Если `quality_a >= QUALITY_THRESHOLD_OK` → принять
3) Иначе → **MinerU** (PDF→markdown с layout) → `text_b`, `quality_b`
4) Если `quality_b >= QUALITY_THRESHOLD_OK` → принять
5) Иначе → **PaddleOCR** (последний этап) → `text_c`, `quality_c`
   - предпочтительно OCR **только проблемных страниц** (где builtin пуст/плох)
6) Сохранить лучший результат + метаданные:
   - `parser_used: builtin|mineru|paddleocr|mixed`
   - `quality_score`
   - `warnings`
   - `ocr_pages_processed`

### 5.3 Оценка качества (эвристики)
Без LLM (быстро, надежно):
- доля букв/цифр (RU/EN)
- доля мусорных символов (например `U+FFFD`, “кракозябры”)
- количество слов/строк, средняя длина слова
- повторяемость строк/аномальная пунктуация
- “пустые страницы”

Опционально (по флагу):
- **LLM validation** по 2–5 фрагментам текста (по 500–1000 символов) → `ok/suspicious`.
  Это не “100% доказательство”, но сигнал “запускать следующий этап каскада”.

### 5.4 XLSX → Markdown
- Извлекать листы
- Порционно (по строкам) конвертировать таблицу в markdown block
- Метаданные chunk:
  - `sheet_name`, `row_start`, `row_end`

---

## 6) Hybrid Search + Rerank

### 6.1 Hybrid retrieval
- BM25 (pg_search) → topK
- Vector (pgvector) → topK
- Fusion: **RRF** по умолчанию
  - параметры: `RRF_K`, `BM25_TOP_K`, `VECTOR_TOP_K`, `FINAL_TOP_N`

### 6.2 Rerank
- Взять `FINAL_TOP_N` (например 30–80)
- Отправить в `/v1/rerank` (query + passages)
- Получить упорядоченный список
- В контекст LLM включить topM (например 6–12)

Reranker — отдельная модель, поэтому **BGE‑M3 не запускается дважды**.

---

## 7) Статусы и очередь задач (jobs)

### 7.1 Статусы job
`queued | running | success | failed | partial_success | canceled | skipped`

### 7.2 UI “Очередь” (левая колонка)
Требование: показать пользователям загруженность сервера.

В левой колонке UI отображать список задач (например последние 20):
- позиция (1–99)
- имя файла `name.ext` (если применимо)
- размер (MB)
- тип (`upload`, `nas_scan`, `audit`, `cleanup`)
- статус + текущий шаг
- прогресс (%)

Источник данных: таблицы `jobs` + `documents` (join по document_id через meta или отдельное поле).  
Позиция = сортировка по `created_at` среди `queued/running` + ограничение 1–99.

---

## 8) Admin (/admin)

### 8.1 Доступ
`/admin` доступен только если `ADMIN_UI_ENABLED=true` (флаг в config.py).  
Ссылка: `http://127.0.0.1:8000/admin` (пример).

### 8.2 Возможности
- CRUD источников `sources`:
  - name, base_path, include/exclude globs
- Настройка расписаний:
  - incremental cron
  - full audit cron
- Настройка лимитов прогонов:
  - max_files, max_mb, max_seconds, concurrency
- Ручной запуск:
  - “Run incremental now”
  - “Run full audit now”
- Просмотр очереди jobs (общий список)

---

## 9) Безопасность

### 9.1 NAS read-only
- NAS user: `search_ai` + пароль (по требованию пока хранить в `config.py`)
- Все операции — только чтение
- Запись в NAS запрещена на уровне прав и логики приложения

### 9.2 Защита от path traversal
Пользователь не передает “сырой путь”.  
`/view` и `/download` работают **только по document_id**.

При выдаче файла:
1) взять `relative_path` и `source.base_path` из БД
2) собрать абсолютный путь и `realpath`
3) проверить, что `abs_path` начинается с `realpath(base_path)`
4) открыть файл read-only и отдать

### 9.3 Вредоносные документы
Embeddings/BM25 сами по себе “код” не исполняют, но парсеры могут иметь уязвимости/DoS. Минимальная защита:
- whitelist форматов
- лимиты размера/страниц/чанков
- таймауты парсинга/OCR/job
- парсинг только в worker
- ресурсы контейнера worker ограничены

---

## 10) База данных (Postgres)

### 10.1 Расширения
- `pgvector`
- `pg_search` (ParadeDB)

### 10.2 Таблицы (минимально необходимое)
**sources**
- `id (uuid)`
- `name (text)`
- `base_path (text)`
- `enabled (bool)`
- `include_globs (text[])`
- `exclude_globs (text[])`
- `schedule_cron (text)`
- `audit_cron (text)`
- `limits_json (jsonb)`
- timestamps

**documents**
- `id (uuid)`
- `scope (temp|nas)`
- `source_id (uuid nullable)`
- `title (text)`
- `relative_path (text nullable)`
- `storage_path (text)`
- `ext, mime, size_bytes`
- `mtime (timestamptz nullable)`
- `checksum (text nullable)`
- `status (discovered|uploaded|queued|processing|ready|failed|expired|deleted)`
- `expires_at (timestamptz nullable)`
- `last_indexed_at`
- `last_error_code/message`
- `meta (jsonb)`

**chunks**
- `id (uuid)`
- `document_id (uuid fk)`
- `chunk_index (int)`
- `content (text)`
- `page_num (int nullable)`
- `sheet_name (text nullable)`
- `row_start,row_end (int nullable)`
- `metadata (jsonb)`
- `embedding (vector(d))`
- timestamps

**jobs**
- `id (uuid)`
- `type (ingest_document|scan_source|full_audit|cleanup_temp|reindex_document|recognize_only)`
- `status (queued|running|success|failed|partial_success|canceled|skipped)`
- `progress (int)`
- `current_step (text)`
- `message (text)`
- `error_code/message`
- `meta (jsonb)`
- timestamps

**job_steps**
- `id (uuid)`
- `job_id (uuid fk)`
- `step (text)`
- `status (text)`
- `progress (int)`
- timestamps
- `detail (jsonb)`

### 10.3 Индексы
- pgvector: индекс на `chunks.embedding` (HNSW/IVFFLAT)
- BM25: pg_search индекс на `chunks.content`
- btree:
  - `documents(scope, expires_at)` (TTL cleanup)
  - `documents(source_id, relative_path)`
  - `chunks(document_id, chunk_index)`

---

## 11) API (v1) — основные эндпоинты

### 11.1 Public config
- `GET /v1/config/public`
  - лимиты/whitelist/ttl для отображения в UI (без секретов)

### 11.2 Upload / temp
- `POST /v1/files/upload` → `{document_id, job_id}`
- `GET /v1/jobs/{job_id}` → `{status, progress, steps[]}`

### 11.3 Chat / QA
- `POST /v1/chat`
  - вход: `query`, `mode (temp|nas)`, `document_id` (для temp), `source_ids[]`, `subpath`
  - выход: `answer`, `citations[]`, `debug(optional)`
- (будущее) `POST /v1/chat/stream` (SSE)

### 11.4 Sources (для вкладки NAS)
- `GET /v1/sources` (только enabled, поля для UI)
- (опционально) `GET /v1/sources/{id}/subpaths` для автокомплита

### 11.5 File serving
- `GET /v1/documents/{id}/view` (PDF inline, новая вкладка)
- `GET /v1/documents/{id}/download` (attachment)

### 11.6 Admin
- `POST /v1/admin/sources` (create/update/delete)
- `POST /v1/admin/sources/{id}/run` (incremental now)
- `POST /v1/admin/sources/{id}/audit` (full audit now)
- `GET /v1/admin/jobs` (очередь)

---

## 12) Конфигурация (config.py)

Все параметры обязаны быть в config.py. Минимальный список:

### 12.1 Форматы и лимиты
- `ALLOWED_EXTENSIONS = {"pdf","docx","xlsx","txt"}`
- `MAX_UPLOAD_MB`
- `MAX_NAS_FILE_MB`
- `MAX_PAGES_PER_PDF`
- `MAX_CHUNKS_PER_DOC`
- `CHUNK_SIZE_CHARS`
- `CHUNK_OVERLAP_CHARS`
- `TEMP_UPLOAD_TTL_HOURS`

### 12.2 Таймауты
- `JOB_TIMEOUT_SECONDS`
- `PARSER_TIMEOUT_SECONDS`
- `OCR_PAGE_TIMEOUT_SECONDS`
- `OCR_TOTAL_TIMEOUT_SECONDS`

### 12.3 PDF каскад
- `PDF_PIPELINE_ORDER = ["builtin","mineru","paddleocr"]`
- `QUALITY_THRESHOLD_OK`
- `QUALITY_THRESHOLD_RUN_NEXT`
- `LLM_VALIDATION_ENABLED`
- `LLM_VALIDATION_SAMPLES`
- `LLM_VALIDATION_SAMPLE_CHARS`

### 12.4 Embeddings / GPU
- `EMBEDDING_MODEL = "bge-m3"`
- `EMBEDDING_DEVICE = "cpu|cuda"`
- `EMBEDDING_BATCH_SIZE`
- `EMBEDDING_MAX_TEXT_CHARS`
- `GPU_LOCK_ENABLED = True`
- `OCR_CONCURRENCY = 1`

### 12.5 Hybrid + Rerank
- `BM25_TOP_K`
- `VECTOR_TOP_K`
- `RRF_K`
- `FINAL_TOP_N`
- `RERANK_TOP_N`
- `CONTEXT_TOP_M`

### 12.6 NAS
- `NAS_SMB_SHARE`
- `NAS_MOUNT_POINT`
- `NAS_USERNAME = "search_ai"`
- `NAS_PASSWORD = "...(временно)..."`
- `NAS_MOUNT_READONLY = True`

### 12.7 Admin/UI/Logs
- `ADMIN_UI_ENABLED`
- `DEBUG_LOGS`
- `LOG_LEVEL` (например WARNING по умолчанию)
- `LOG_FORMAT` (рекомендуется JSON)

---

## 13) Будущие улучшения (не обязаны в v1)
- Streaming в UI (SSE)
- Автокомплит подкаталогов (подсказки по `directories`)
- Вкладка “Распознать документ” с vision-LLM для качества “почти 100%”
- Rerank fallback на CPU
- Персонализация “мои задачи” (после появления auth)
