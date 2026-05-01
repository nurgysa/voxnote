# Audio Transcriber

Десктоп-приложение для транскрипции и диаризации аудио на Windows. UI на
CustomTkinter, движок — `faster-whisper` (large-v3) + `pyannote 3.1`,
устройство выбирается отдельно для каждого этапа (GPU / CPU / Авто).
Оптимизировано под ограниченные ресурсы (тестировалось на ноутбучной
GTX 1650 Ti, 4 GB VRAM).

## Возможности

- Транскрипция: ru/en/auto, модели tiny → large-v3
- Диаризация (GPU или CPU): SPEAKER_XX автоматически или с указанием числа спикеров
- **Cloud (опционально):** транскрипция и диаризация через managed API (Deepgram, Gladia, AssemblyAI, Speechmatics, OpenAI Whisper). Переключатель в Настройках, не требует GPU.
- Voice library: enrollment голосов → SPEAKER_XX заменяется на реальные имена
- Экспорт: TXT, SRT, VTT
- Hotwords / словарь терминов для повышения точности
- Встроенный редактор и cutter аудио
- История прогонов с поиском

## Системные требования

| Компонент | Минимум | Рекомендуется |
|---|---|---|
| ОС | Windows 10 (64-bit) | Windows 11 |
| Python | 3.10.x | 3.10.11 |
| GPU | NVIDIA, 4 GB VRAM, CUDA Compute ≥ 7.5 (Turing+) | 6+ GB VRAM |
| Драйвер NVIDIA | ≥ 552.x (CUDA 12.6) | актуальный Studio Driver |
| Диск | 10 GB свободно (модели + кэши) | SSD |
| RAM | 8 GB | 16 GB |

**Транскрипция и диаризация работают и на CPU**, но обе значительно медленнее: транскрипция в 5-10× медленнее GPU, диаризация — в 10-20× (час аудио → ~30 минут на CPU vs ~1.5 мин на GPU). Устройство для каждого этапа выбирается отдельно в UI: «Авто» (GPU при наличии, иначе CPU) / «GPU (NVIDIA)» / «CPU». Если выбран «GPU», но видеокарты нет — приложение падает с понятным сообщением, а не молча сваливается на CPU.

## Установка

### 1. Python 3.10

Скачай с [python.org](https://www.python.org/downloads/release/python-31011/).
При установке — галочка **«Add Python to PATH»**.

### 2. ffmpeg

Скачай билд с [ffmpeg.org](https://ffmpeg.org/download.html#build-windows) (или
через [gyan.dev](https://www.gyan.dev/ffmpeg/builds/)). Распакуй и добавь
`bin/` в `PATH`. Проверь:

```cmd
ffmpeg -version
```

### 3. Зависимости

```bash
pip install -r requirements.txt
```

Версии в `requirements.txt` зафиксированы — воркараунды для
speechbrain/lightning/pyannote/cuDNN привязаны к конкретным комбинациям. Не
обновляйте без проверки на длинном файле с диаризацией.

`torch==2.11.0+cu126` тянется с indexes PyTorch — если pip ругается, добавь
явно:
```bash
pip install torch==2.11.0+cu126 torchaudio==2.11.0+cu126 --index-url https://download.pytorch.org/whl/cu126
```

## Hugging Face setup (для диаризации)

1. Создай токен (read-only достаточно): https://huggingface.co/settings/tokens
2. **Прими условия** на двух gated моделях (это обязательно — иначе 401):
   - https://huggingface.co/pyannote/speaker-diarization-3.1
   - https://huggingface.co/pyannote/segmentation-3.0
3. Передай токен одним из способов (порядок: первый непустой выигрывает):
   - Поле «HF Token» в UI → кнопка «Вставить» → токен сохранится в локальный `config.json`
   - Переменная окружения `HF_TOKEN`

`config.json` уже в `.gitignore` — токен не утечёт в git. Шаблон без секретов
лежит в `config.example.json`.

При первом запуске диаризации pyannote скачает веса (~700 MB) в
`~/.cache/huggingface/`. Whisper large-v3 (~3 GB) подгружается на первый
прогон транскрипции.

## Cloud (опционально, без GPU)

Если у тебя нет NVIDIA GPU или хочется быстрого результата на длинных
файлах — приложение умеет делегировать транскрипцию и диаризацию
managed-API провайдеру. Поддерживаются пять провайдеров; ключ для
каждого хранится отдельно, поэтому можно держать несколько одновременно
и переключаться dropdown'ом.

| Провайдер       | Цена (с диаризацией) | Диаризация | Где регистрироваться        |
|-----------------|----------------------|------------|------------------------------|
| Deepgram        | ~$0.43/час           | ✅          | https://console.deepgram.com |
| Gladia          | ~$0.61/час           | ✅          | https://app.gladia.io        |
| AssemblyAI      | ~$0.65/час           | ✅          | https://www.assemblyai.com   |
| Speechmatics    | ~$1.04/час           | ✅          | https://portal.speechmatics.com |
| OpenAI Whisper  | ~$0.36/час           | ❌          | https://platform.openai.com  |

Все, кроме OpenAI Whisper, возвращают метки спикеров. OpenAI Whisper
дешевле всех на чистую транскрипцию, но не диаризует — UI спросит
подтверждение, если включена «Диаризация», и продолжит без меток.

Шаги:

1. Создай аккаунт у выбранного провайдера и получи API-ключ.
2. В приложении: **Настройки → Облако → ☑ Использовать облако**.
3. Выбери провайдера в dropdown и вставь API-ключ.
4. Запусти транскрипцию обычным образом.

Когда облако включено, локальные настройки устройства (GPU/CPU)
игнорируются — задача целиком обрабатывается на серверах провайдера.
Аудио загружается на их сервера; не используй для конфиденциальных
записей.

Добавить ещё одного провайдера — одна реализация
[`providers/base.py`](providers/base.py) + одна запись в
[`providers/__init__.py`](providers/__init__.py).

## Запуск

```bash
python app.py
```

## Тесты

```bash
pip install -r requirements-dev.txt
pytest
```

Тесты — на pure-функциях, GPU/HF-токен не нужны.

## Troubleshooting

### `CUDA failed with error out of memory` при транскрипции
4 GB VRAM очень тесные. Закрой GPU-потребителей (Chrome с hardware acceleration,
видеоплееры, Telegram, OBS) и попробуй снова.

### `CUBLAS_STATUS_NOT_INITIALIZED` в диаризации
Симптом фрагментации VRAM. Что делать по порядку:
1. Обнови NVIDIA-драйвер до актуального Studio Driver (старый драйвер 2023 года
   часто даёт это сразу).
2. Перезапусти приложение (чистит зомби-CUDA-контексты).
3. Если повторяется — auto-retry с уменьшением batch уже встроен,
   следи за статусом `VRAM tight, retry batch=...`.

### `MemoryError: batch_size is probably too large`
Pyannote сам сообщает о VRAM-перегрузе. Auto-retry должен перехватить (batch
ladder 8 → 4 → 2). Если падает на batch=2 — значит реально не хватает VRAM,
нужна карта от 6 GB.

### `401 Unauthorized` от Hugging Face
Не приняты условия модели. Перейди по ссылкам в разделе «Hugging Face setup»
и нажми «Agree» на странице каждой модели.

### `ffmpeg: command not found`
Не в PATH. См. шаг 2 установки.

### `STATUS_DLL_INIT_FAILED` (Windows код 3221225794)
Конфликт CUDA DLL при импорте. В коде учтён правильный порядок импорта
(`ctranslate2` до `torch`), но если падает — проверь, нет ли двух разных
версий CUDA Toolkit в PATH.

### «GPU выбран, но CUDA недоступна»
Жёсткое поведение: если в UI выбран «GPU (NVIDIA)», но видеокарты не
видно — приложение падает с этим сообщением, не сваливаясь молча на CPU.
Решения: (а) переключи селектор на «CPU» — будет медленно, но работать;
(б) переключи на «Авто» — silent fallback на CPU; (в) проверь, что
NVIDIA-драйвер установлен и `nvidia-smi` работает.

## Лицензия

MIT — см. [LICENSE](LICENSE).

## Acknowledgments

Этот проект стоит на плечах гигантов:

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — оптимизированная
  обёртка над Whisper на ctranslate2 (MIT)
- [ctranslate2](https://github.com/OpenNMT/CTranslate2) — inference-движок
  (MIT)
- [pyannote.audio](https://github.com/pyannote/pyannote-audio) — диаризация
  спикеров (MIT-код, отдельные условия на веса моделей)
- [openai/whisper](https://github.com/openai/whisper) — оригинальная модель
  ASR (MIT)
- [customtkinter](https://github.com/TomSchimansky/CustomTkinter) — UI-toolkit
  (MIT)
