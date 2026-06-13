# VoxNote

[![CI](https://github.com/nurgysa/voxnote/actions/workflows/tests.yml/badge.svg)](https://github.com/nurgysa/voxnote/actions/workflows/tests.yml)
[![Release](https://img.shields.io/github/v/release/nurgysa/voxnote)](https://github.com/nurgysa/voxnote/releases/latest)
[![License: MIT](https://img.shields.io/github/license/nurgysa/voxnote)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078d4)](docs/CLIENT_SETUP.md)

Десктоп-приложение для Windows: транскрипция аудио + диаризация спикеров
через managed cloud-API, с извлечением задач и генерацией протокола встречи
через LLM. UI на CustomTkinter. **GPU не нужен** — всё распознавание идёт
по HTTPS к облачным провайдерам.

> **EN:** Windows desktop app for cloud speech-to-text + speaker diarization
> (AssemblyAI / Deepgram / Gladia / Speechmatics) with LLM task extraction to
> Linear / Trello / Glide and meeting-protocol generation. Built for
> Kazakh + Russian + English code-switching meetings. The UI and docs are
> currently Russian-only. Grab the ready-to-run `.exe` from
> [Releases](https://github.com/nurgysa/voxnote/releases/latest).

> **Cloud-only с 2026-05-28.** Локальный стек (faster-whisper / pyannote /
> CUDA / torch) удалён из кодовой базы. Если вы искали GPU-версию — она в
> истории git до коммита рип-аута, но больше не поддерживается.

## Скачать

Готовое приложение (Windows 10/11, Python не нужен):
**[Releases → VoxNote-vX.Y.Z.zip](https://github.com/nurgysa/voxnote/releases/latest)** (~147 МБ).
Распакуйте в папку под вашим пользователем и запустите `VoxNote.exe` —
первый запуск по шагам: [`docs/CLIENT_SETUP.md`](docs/CLIENT_SETUP.md).

Остальной README — для разработки из исходников.

## Место в экосистеме Mini-AGI

VoxNote — часть **Mini-AGI**, персональной AI-операционной системы для
управления знаниями, задачами, проектами, документами и цифровыми агентами
из единого рабочего контура. В этой системе VoxNote отвечает за **голосовой,
аудио- и транскрипционный ввод** и спроектирован как Hermes-native приложение
и сервис.

Центральный слой — **Hermes Desktop**: оркестратор, который управляет
инструментами, skills, memory, gateway, cron-задачами и агентными процессами.
VoxNote подключается к нему через MCP (inbound) и webhook `audio.transcribed`
(outbound) — детали в разделе [Hermes Agent integration](#hermes-agent-integration).

| Компонент | Роль в Mini-AGI |
|---|---|
| **Hermes Desktop** | Оркестратор: инструменты, skills, memory, gateway, cron, агентные процессы |
| **VoxNote** | Голосовой / аудио / транскрипционный ввод (это приложение) |
| **Telegram** | Быстрый канал захвата идей, задач, голосовых заметок и запросов с телефона |
| **Obsidian** | Человекочитаемая база знаний и источник правды в Markdown |
| **GBrain** | Семантическая память, поиск, связи между заметками, синтез знаний |
| **GitHub** | Версионность, ревью, история изменений, безопасные checkpoints |
| **Google Drive** | Облачное хранение, синхронизация файлов, документный слой |
| **Linear** | Human-facing доска задач |
| **Hermes Kanban** | Внутренняя очередь для агентного исполнения |
| **Codex** | Основной исполнительный AI-агент для coding-, файловых и Obsidian-workflows |

Mini-AGI — не закрытый набор инструментов: система пополняется новыми
сервисами, приложениями, агентами, интеграциями и бизнес-вертикалями, а стек
эволюционирует по мере появления новых задач и практических сценариев.

## Возможности

- **Транскрипция (cloud):** 4 провайдера — AssemblyAI, Deepgram, Gladia,
  Speechmatics. Переключаются в Настройках, ключ у каждого свой.
- **Диаризация спикеров:** `Speaker A/B/...` от провайдера; имена
  подставляются вручную или через directory-grounding.
- **Code-switching KZ + RU + EN:** AssemblyAI Universal обрабатывает
  переключение языков внутри одной записи нативно.
- **Извлечение задач (LLM → таск-трекер):** через OpenRouter; отправка в
  Linear / Trello / Glide (Protocol-based backends в `tasks/backends/`).
- **Протокол встречи** (`protocol.md`, 5-block MoM) — генерируется LLM.
- **Grounding документами:** прикреплённые PDF/DOCX/PPTX/XLSX → Markdown
  (Microsoft markitdown) попадают в контекст LLM.
- **Запись с микрофона**, встроенный **Audio Cutter** (обрезка/превью).
- **История встреч** с поиском; раскладка по проектам на диске.
- **Резервное копирование в Google Drive** (текст встреч, ключи
  редактируются перед загрузкой).
- **Headless CLI + MCP-сервер** для агентских CLI (см. [`AGENTS.md`](AGENTS.md)).
- **Экспорт:** TXT / SRT / VTT / Markdown.

## Системные требования

| Компонент | Требование |
|---|---|
| ОС | Windows 10 (64-bit) или Windows 11 |
| Python (для разработки) | 3.12.x |
| ffmpeg | в `PATH` (для разработки) — в `.exe`-бандле он встроен |
| Сеть | обязательна — приложение использует cloud-API, **offline не работает** |
| GPU | **не нужен** |

## Установка (для разработки)

### 1. Python 3.12

Скачайте с [python.org](https://www.python.org/downloads/). При установке —
галочка **«Add Python to PATH»**.

### 2. ffmpeg

Скачайте билд с [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) («release
essentials»), распакуйте, добавьте `bin/` в `PATH`. Проверка: `ffmpeg -version`.

(В готовом `.exe` ffmpeg уже вшит в `vendor/ffmpeg/` — отдельно ставить не надо.)

### 3. Зависимости

```bash
pip install -r requirements.txt
```

Версии в `requirements.txt` **зафиксированы жёстко** — пины load-bearing на
Windows (CustomTkinter / soundfile / sounddevice / google-auth). Не обновляйте
без smoke-теста на чистой Win10 + Win11.

## API-ключи

Всё распознавание и LLM-работа — через cloud. Ключи вводятся в **Настройках**
(хранятся в `~/.voxnote/config.json`, в git не попадают):

| Сервис | Зачем | Где взять |
|---|---|---|
| **AssemblyAI** | транскрипция + диаризация (обязательно) | <https://www.assemblyai.com> |
| **OpenRouter** | извлечение задач + протокол (обязательно) | <https://openrouter.ai/keys> |
| Linear / Trello / Glide | отправка задач (опционально) | в Настройках каждого |

Шаблон без секретов — [`config.example.json`](config.example.json).

## Cloud-провайдеры транскрипции

| Провайдер | Цена (с диаризацией) | Казахский | Регистрация |
|---|---|---|---|
| AssemblyAI | ~$0.17/час | ✅ (Universal) | <https://www.assemblyai.com> |
| Deepgram | ~$0.43/час | ❌ (RU+EN) | <https://console.deepgram.com> |
| Gladia | ~$0.61/час | ✅ | <https://app.gladia.io> |
| Speechmatics | ~$1.04/час | ✅ | <https://portal.speechmatics.com> |

Аудио загружается на сервера провайдера; **не используйте для
конфиденциальных записей**, для которых это запрещено.

Добавить провайдера — одна реализация [`providers/base.py`](providers/base.py)
+ запись в [`providers/__init__.py`](providers/__init__.py).

## Запуск

```bash
python app.py
```

## Тесты

```bash
pip install -r requirements-dev.txt
pytest
python -m ruff check .
```

Тесты — на pure-функциях; GPU, API-ключи и сеть не нужны.

## Сборка .exe

```powershell
.\scripts\build_exe.ps1                       # PyInstaller onedir в dist/
python scripts\package_release.py --version X.Y.Z   # упаковка + проверки
```

`package_release.py` пакует через Python `zipfile` (forward-slash arcnames),
проверяет отсутствие секретов/состояния в бандле, наличие markitdown и
GPLv3-лицензии ffmpeg, целостность архива. См. [`docs/CLIENT_SETUP.md`](docs/CLIENT_SETUP.md).

## Troubleshooting

| Симптом | Решение |
|---|---|
| `401 Unauthorized` при транскрипции | Неверный/истёкший ключ провайдера — перепроверьте в Настройках |
| `Insufficient credits` | Закончился баланс AssemblyAI / OpenRouter — пополните |
| `ffmpeg: command not found` (dev) | ffmpeg не в `PATH` — см. шаг 2 |
| Пустой/сломанный транскрипт на казахском | Переключите провайдера на Gladia / Speechmatics, или уберите фоновый шум |
| `.exe` не запускается / Defender блокирует | Добавьте папку приложения в exclusions Windows Defender |

Полный гид для конечных пользователей — [`docs/CLIENT_SETUP.md`](docs/CLIENT_SETUP.md).

## Hermes Agent integration

Приложение поддерживает **два режима** работы с [Hermes Agent](https://github.com/nurgisa/hermes):

| Режим | Направление | Описание |
|---|---|---|
| **MCP (inbound)** | Hermes → App | Hermes вызывает MCP-сервер: `transcribe_audio`, `extract_tasks` и др. |
| **Webhook (outbound)** | App → Hermes | Приложение отправляет событие `audio.transcribed` в Hermes после успешной транскрипции |

### Outbound webhook (по умолчанию отключён)

После завершения транскрипции приложение отправляет POST-запрос на адрес Hermes
с JSON-пейлоадом `audio.transcribed` (текст транскрипта, провайдер, язык,
speaker-сегменты, путь к истории встречи). Запрос подписан HMAC-SHA256
(`X-Webhook-Signature`); Hermes валидирует подпись на своей стороне.

Доставка — **best-effort**: если Hermes недоступен, транскрипция всё равно
считается успешной.

**Конфигурация** (`~/.voxnote/config.json` или переменные окружения):

| Ключ config.json | Переменная окружения | По умолчанию | Описание |
|---|---|---|---|
| `hermes_webhook_enabled` | `VOXNOTE_HERMES_WEBHOOK_ENABLED` | `false` | Включить отправку |
| `hermes_webhook_url` | `VOXNOTE_HERMES_WEBHOOK_URL` | `http://localhost:8644/webhooks/audio-transcribed` | URL endpoint Hermes |
| `hermes_webhook_secret` | `VOXNOTE_HERMES_WEBHOOK_SECRET` | `""` | Shared secret для HMAC |
| `hermes_webhook_timeout_seconds` | `VOXNOTE_HERMES_WEBHOOK_TIMEOUT_SECONDS` | `10` | Таймаут запроса (сек) |
| `hermes_webhook_routing_hint` | `VOXNOTE_HERMES_WEBHOOK_ROUTING_HINT` | `obsidian_inbox` | Хинт маршрутизации |

> Переменная окружения с пустым значением (`=""`) игнорируется — берётся
> значение из config.json. Env-переменные с непустым значением перекрывают
> config.json.

Шаблон ключей — [`config.example.json`](config.example.json). Не коммитьте
реальный секрет — используйте переменные окружения.

Для настройки Hermes-стороны (маршрут `audio-transcribed`, HMAC-ключ,
`hermes webhook subscribe`) — см. [`AGENTS.md`](AGENTS.md).

## Архитектура

Карта модулей, runtime-модель и JSON-контракты — в
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Инварианты и конвенции для
AI-агентов — в [`CLAUDE.md`](CLAUDE.md).

## Лицензия

MIT — см. [LICENSE](LICENSE). Сторонние компоненты (включая GPLv3-сборку
ffmpeg, вызываемую как отдельный процесс) — см.
[`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md).

## Acknowledgments

- [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) — UI-toolkit (MIT)
- [FFmpeg](https://ffmpeg.org/) — аудио-обработка (GPLv3-сборка, вызывается как процесс)
- [markitdown](https://github.com/microsoft/markitdown) — документы → Markdown (MIT)
- AssemblyAI / Deepgram / Gladia / Speechmatics — cloud STT API
- [OpenRouter](https://openrouter.ai/) — LLM-роутинг для задач и протокола
