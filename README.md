# README.md
## Docker RAG система: Upload (temp TTL) + NAS (Hybrid BM25+Vector) + Rerank + OCR/MinerU

Дата: 2026-02-05

---

## 1) Что это
Это Docker-разворачиваемая RAG-система, которая:
- по умолчанию работает в режиме **“Поиск по файлу”** (upload → временная индексация с TTL → чат);
- поддерживает режим **“Поиск по NAS”** (проиндексированный файловый сервер);
- использует **гибридный поиск** (BM25 через ParadeDB `pg_search` + векторы `pgvector`) и **reranker**;
- при необходимости извлекает текст из PDF каскадом: builtin → MinerU → PaddleOCR;
- показывает пользователям **очередь задач** в UI;
- имеет админку `/admin` (скрыта флагом) для управления NAS источниками и расписаниями.

---

## 2) Что нужно для запуска
Минимально:
- Docker + Docker Compose (v2)
- Доступ к NAS по SMB/CIFS (если используете вкладку NAS)
- Достаточно места под Postgres (индексы могут быть большими)
- (Опционально) GPU для ускорения OCR/rerank/embeddings

Сервисы (контейнеры):
- `api` (FastAPI + UI)
- `worker` (очередь задач)
- `redis`
- `postgres` (pgvector + pg_search)
- `reranker`
- `ocr` (PaddleOCR)
- `mineru`
- `llm` (внешний или внутри compose)

LLM должен поддерживать:
- `POST /v1/chat`

Streaming (SSE) планируется, но для v1 не обязателен.

---

## 3) Быстрый старт

### 3.1 Подготовка конфигов
1) Скопируйте пример окружения (если используется):
```bash
cp .env.example .env
```

2) Настройте параметры (в `.env` и/или `config.py`):
- БД (POSTGRES_*)
- LLM URL (`LLM_BASE_URL`)
- OCR/MinerU/Reranker URL (если вынесены отдельно)
- Лимиты и таймауты
- NAS параметры (share, mount point, user/pass)

> По требованию проекта на старте NAS user/pass хранятся в `config.py`. Не публикуйте репозиторий с реальными паролями.

### 3.2 Запуск
```bash
docker compose up -d --build
```

Проверка:
```bash
docker compose ps
docker compose logs -f api
docker compose logs -f worker
```

### 3.3 UI и Admin
- UI: `http://127.0.0.1:8000/`
- Admin (если включен): `http://127.0.0.1:8000/admin`

---

## 4) Режимы UI

### 4.1 Поиск по файлу (default)
1) Загрузите файл (pdf/docx/xlsx/txt)
2) Дождитесь завершения обработки (status `ready`)
3) Задайте вопрос в чате
4) Получите ответ + источники (Открыть/Скачать)

TTL:
- Временные документы живут `TEMP_UPLOAD_TTL_HOURS`
- После TTL при открытии источника: “Файл не найден. Загрузите файл.”

### 4.2 Поиск по NAS
1) Включите `/admin` (см. config `ADMIN_UI_ENABLED=true`)
2) Добавьте NAS источники (sources), задайте base_path внутри `/mnt/nas`
3) Запустите индексацию (incremental / full audit)
4) Перейдите во вкладку “Поиск по NAS”, выберите источники и задайте вопрос

---

## 5) Очередь задач (UI)
Слева отображается колонка “Очередь”:
- позиция (1–99)
- имя файла (name.ext)
- размер (MB)
- статус/шаг
Это помогает пользователям понимать, есть ли задачи перед ними.

---

## 6) SMB mount внутри контейнера (важно)
Проект предполагает SMB mount **внутри** контейнеров `worker` и `api`.

В некоторых окружениях Docker для mount могут понадобиться дополнительные права контейнера.  
Если mount не работает:
- посмотрите логи `worker`
- проверьте доступность NAS/SMB
- рассмотрите альтернативу: смонтировать NAS на хосте и пробросить как volume

---

## 7) Команды обслуживания

Остановить:
```bash
docker compose down
```

Удалить volumes (осторожно: удалит БД):
```bash
docker compose down -v
```

Пересборка:
```bash
docker compose build --no-cache
docker compose up -d
```

Логи:
```bash
docker compose logs -f api
docker compose logs -f worker
docker compose logs -f postgres
```

---

## 8) Безопасность
- NAS read-only
- выдача файлов только по `document_id`
- защита от path traversal обязательна
- лимиты/таймауты обязательны
- тяжелые операции только в worker

---

## 9) Что дальше (roadmap)
- Streaming ответов (SSE): `/v1/chat/stream`
- Автокомплит подкаталогов
- Вкладка “Распознать документ” с vision-LLM для точности “почти 100%”
