# Шаблон протокола встречи (5-block MoM)

**Версия:** v1.0 (Task 5 of MVP v5 plan, 2026-05-28)
**Источник:** Tauri SaaS spec §7.9, embedded in `tasks/protocol_template.py`

Этот документ описывает шаблон, по которому VoxNote генерирует
`<history>/<run>/protocol.md` после клика «Извлечь задачи» в Extract dialog.

## Зачем 5 блоков

Стандарт Minutes of Meeting (MoM) — пять структурных секций, каждая из
которых отвечает на одну вопрос-категорию:

1. **Метаданные** — кто, когда, что за встреча. (Контекст для будущего читателя.)
2. **Повестка дня** — какие темы обсуждались. (Скелет встречи.)
3. **Ключевые тезисы и решения** — что именно сказали и о чём договорились. (Содержание.)
4. **План действий** — кто что делает к какому сроку. (Actionable выход.)
5. **Следующая встреча и материалы** — куда идём дальше. (Continuity.)

Этот разрез — индустриальный стандарт; протокол в 5 блоков читается одинаково
вне зависимости от типа встречи (Sprint Planning / 1-on-1 / Customer Call / …).

## Как заполняются блоки

| Блок | Источник содержания | Placeholder в шаблоне |
|---|---|---|
| 1. Метаданные | `meeting_type` + `participants` от LLM; `meeting_date` от UI (значение из формы) | `{meeting_type}` / `{meeting_date}` / `{participants}` |
| 2. Повестка дня | LLM извлекает из первых 5-10 минут транскрипта | `{agenda}` |
| 3. Ключевые тезисы и решения | LLM анализирует весь транскрипт, выделяет решения жирным (`**...**`) | `{theses_and_decisions}` |
| 4. План действий | LLM ищет фразы типа «Иван, сделай X к четвергу» → формат `- @Исполнитель: задача (срок)` | `{action_items}` |
| 5. Следующая встреча и материалы | **Не извлекается в v1.0** — статичная подсказка «добавьте вручную» (см. ниже) | (нет placeholder'а) |

Блок 5 оставлен статичным в v1.0 по двум причинам:

- LLM-извлечение «когда следующая встреча» добавляет отдельный API-вызов
  (стоимость + латентность) ради ~10% случаев, когда дата реально звучит
  в транскрипте.
- Spec §7.9 (Tauri) описывает Phase 2 `next_meeting` pass с frontmatter-
  полями `{date, topic, confidence}` — переносим эту работу туда, чтобы
  не делать половину сейчас.

## Структура `Placeholders` dataclass

`tasks/protocol_template.py` определяет frozen-dataclass с **6 полями**:

```python
@dataclass(frozen=True)
class Placeholders:
    meeting_type: str
    meeting_date: str
    participants: str
    agenda: str
    theses_and_decisions: str
    action_items: str
```

«5 блоков, 6 полей» — Метаданные распилена на 3 атомарных поля, чтобы
`meeting_date` шёл напрямую из UI (где пользователь обычно его уже знает),
без обращения к LLM. Остальные 5 полей — однозначное соответствие блокам.

## Контракт LLM

`tasks/protocol_generator.py` отправляет в OpenRouter:

- **System message** (~1.5 KB, кешируется через Anthropic prompt-cache) —
  инструкция в виде «верни ровно 5 H2-секций в указанном порядке».
- **User message** (динамический) — `meeting_date` + `speakers` + `lang_label`
  + сам транскрипт между `=== ТРАНСКРИПТ ===` маркерами.

LLM возвращает markdown вида:

```markdown
## meeting_type
Sprint Planning

## participants
Иван, Анна, ...

## agenda
- ...
- ...

## theses_and_decisions
**Решение:** ...

## action_items
- @Иван: ... (срок 2026-06-04)
```

Парсер (`parse_llm_response`) разбивает по regex `^## (\w+)\n(.*?)(?=\n##|\Z)`
и наполняет `Placeholders`. Если хоть один из 5 обязательных блоков
отсутствует — `ProtocolGenerationError` с диагностикой «найдено / не
хватает», чтобы пользователь мог попробовать другую модель.

## Параметры LLM

- `model`: выбирается в Extract dialog (пользовательский dropdown).
  Рекомендуем `anthropic/claude-sonnet-4.5` или `anthropic/claude-haiku-4.5`.
- `temperature`: **0.3** — чуть выше `tasks/extractor.py` default 0.2,
  потому что формулировка протокола выигрывает от лёгкой вариативности
  при сохранении верности транскрипту.
- `json_mode`: **False** — вывод markdown, не JSON.
- `timeout`: 60 сек (стандарт OpenRouterClient).

Типичная стоимость на 30-минутную встречу с Sonnet 4.5: ~$0.01-0.02
(зависит от плотности диалога).

## Регенерация

В v1.0 при недовольстве протоколом пользователь:

1. Нажимает «Извлечь задачи» ещё раз — пересчитывается полностью.
2. Опционально меняет модель в dropdown'е перед повтором.

Phase 2 (post-MVP) добавит per-block регенерацию через `Placeholders`
объект — например, «перегенерируй только action_items другой моделью».
Дизайн `ProtocolResult.placeholders` уже эту возможность поддерживает.

## Изменение шаблона

Если нужно отредактировать структуру:

1. Меняй `MOM_5_BLOCK_TEMPLATE` константу в `tasks/protocol_template.py`.
2. Если добавляешь/убираешь поля — также правишь `Placeholders` dataclass
   + system prompt в `_SYSTEM_PROMPT` (`tasks/protocol_generator.py`) +
   `_REQUIRED_BLOCKS` tuple.
3. Тест `test_template_declares_all_six_placeholders` ловит расхождение
   между dataclass-полями и `{name}`-плейсхолдерами в шаблоне.
4. Тест `test_template_has_five_block_structure` следит за наличием 5 H2
   секций (case-insensitive marker scan).
5. Любой add/remove поля в Placeholders → обязательно обнови этот документ.

Spec §7.9 описывает 10 type-specific seeded templates (Standup,
Customer Call, Sprint Retro, …) которые приедут в Phase 2 как файлы
`<vault>/.voxnote/protocol_templates/<Type>.md`. В v1.0 один
универсальный 5-block skeleton покрывает все типы встреч.
