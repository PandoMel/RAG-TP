# TASK.md
## Техническое задание для Codex: Docker RAG (Upload + NAS) с Hybrid Search, Rerank, OCR/MinerU и очередью задач

Дата: 2026-02-05

---

## A) Цели
Реализовать систему согласно `ARCHITECTURE.md`:
- UI: чат с вкладками:
  1) **Поиск по файлу** (по умолчанию, temp TTL)
  2) **Поиск по NAS**
  3) **Распознать документ** (распознавание pdf с возможностью скачать)
- Backend: FastAPI + очередь worker.
- DB: Postgres + `pgvector` + ParadeDB `pg_search`.
- Embeddings: BGE‑M3.
- Hybrid retrieval: BM25 + vector + RRF.
- Reranker: обязателен (`/v1/rerank`).
- OCR: PaddleOCR (контейнер).
- PDF parsing: MinerU (контейнер).
- Очередь задач отображается в UI (левая колонка).
- Админка `/admin` скрыта флагом.

---

## B) Что должно появиться в репозитории
В корне репо должны быть файлы:
- `ARCHITECTURE.md`
- `AGENTS.md`
- `TASK.md`
- `README.md`
- `docker-compose.yml`
- `.env.example` (если используется)
- миграции (alembic или sql)

Рекомендуемая структура (допускаются упрощения, но логика должна сохраниться):
```
/
  api/
    Dockerfile
    app/
      main.py
      config.py
      routers/
      services/
      clients/
      ui/
  worker/
    Dockerfile
    app/
      config.py
      tasks.py
      scheduler.py
      pipeline/
      parsers/
      ocr_client/
      mineru_client/
      embedding/
      retrieval/
  reranker/
    Dockerfile
    app/
      main.py
      config.py
  ocr/
    Dockerfile (или официальный образ + wrapper)
  mineru/
    Dockerfile (или wrapper)
  migrations/
    alembic/ (или sql)
  docker-compose.yml
  README.md
```

---

## C) Конфигурация (обязательно)
Все параметры вынести в `config.py` (в `api` и `worker`, можно общий модуль).

### C1) В config обязательно должны быть:
- Whitelist форматов: pdf/docx/xlsx/txt
- Лимиты (upload, NAS, pages, chunks)
- Таймауты (job/parser/ocr)
- TTL temp upload
- Параметры chunking
- Параметры embeddings (device/batch/max_chars)
- Параметры hybrid (bm25_top_k/vector_top_k/rrf_k/final_top_n)
- Параметры rerank (rerank_top_n/context_top_m)
- PDF pipeline order + quality thresholds + optional LLM validation
- GPU lock параметры
- NAS SMB config + creds (временно можно в config.py)
- Логи: DEBUG_LOGS и уровни
- Admin flag: ADMIN_UI_ENABLED
- В UI лимиты должны отображаться из `/v1/config/public`.

---

## D) Postgres и миграции
1) В миграциях включить расширения:
- `pgvector`
- `pg_search`

2) Создать таблицы:
- `sources`, `documents`, `chunks`, `jobs`, `job_steps`

3) Индексы:
- vector индекс на `chunks.embedding`
- pg_search индекс на `chunks.content`
- btree на фильтры (`documents(scope,expires_at)`, `documents(source_id,relative_path)` и т.п.)

---

## E) NAS mount (SMB) внутри контейнеров
- `worker` и `api` должны иметь доступ к `/mnt/nas`.
- Смонтировать SMB read-only по config.
- Нельзя логировать пароль.
- Если нужны дополнительные права контейнера — задокументировать в README + дать альтернативу (mount на хосте).

---

## F) Worker: ingestion pipeline
Реализовать задачи:
1) `ingest_uploaded_document(document_id)` (temp)
2) `scan_source_incremental(source_id)` (NAS)
3) `scan_source_full_audit(source_id)` (NAS)
4) `cleanup_expired_temp()` (TTL)

### F1) Подробные статусы
Каждая задача должна писать:
- `jobs` (status, progress, current_step, message)
- `job_steps` (список шагов и детальный прогресс)

### F2) PDF каскад (обязательно)
Реализовать:
- builtin extraction -> quality_score
- если плохо -> MinerU -> quality_score
- если плохо -> PaddleOCR (желательно bad-pages-only) -> quality_score

Сохранить в `documents.meta`:
- `parser_used`, `quality_score`, `warnings`, `ocr_pages_processed`

Quality thresholds и порядок pipeline — только из config.

### F3) XLSX
Конвертировать таблицы в markdown blocks, сохранять `sheet_name` и диапазон строк.

---

## G) Hybrid search + Rerank
- BM25 search (pg_search) topK
- Vector search (pgvector) topK
- Fusion: RRF
- Rerank: сервис `/v1/rerank` для topN
- Отдать LLM topM контекста

Возвращать `citations[]`:
- doc_id, title, relative_path, page_num/sheet, snippet

---

## H) API (v1)
Обязательные эндпоинты:
- `GET /v1/config/public`
- `POST /v1/files/upload`
- `GET /v1/jobs/{job_id}` (детально: шаги)
- `POST /v1/chat` (режим temp/nas + фильтры)
- `GET /v1/sources`
- `GET /v1/documents/{id}/view`
- `GET /v1/documents/{id}/download`

План на будущее:
- `POST /v1/chat/stream` (SSE)

---

## I) UI
Требования:
- Основной режим (по умолчанию): вкладка “Поиск по файлу”
- Вкладка “Поиск по NAS”
- вкладка “Распознать документ” (можно сделать placeholder + API контуры)

**Левая колонка “Очередь” с возможностью сворачивания**:
- позиция 1–99
- имя файла + расширение
- размер (MB)


**Источники в ответе**:
- кликабельные (кнопки “Открыть/Скачать”)
- показывать путь к документу

---

## J) Admin (/admin)
- `/admin` доступен только если `ADMIN_UI_ENABLED=true`.
- CRUD `sources`
- запуск incremental/full audit вручную
- настройка расписаний и лимитов
- список jobs

---

## K) Логи
- WARNING/ERROR/CRITICAL (INFO при DEBUG_LOGS)
- формат JSON предпочтителен
- секреты и большие тексты документов в логах запрещены

---

## L) Definition of Done
`docker compose up -d --build` поднимает систему и можно:
- загрузить файл → увидеть прогресс → задать вопрос → получить ответ + источники
- настроить NAS источник → запустить индексацию → искать по NAS
- открыть/скачать документ источника
- видеть очередь задач в UI
- админ управляет источниками и расписаниями
