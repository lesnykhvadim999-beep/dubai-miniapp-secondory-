# Active Learning Loop (parser_v2)

Каждая правка админа в `@vadim_admin_bot` (✏️ → ✅ Сохранить) — это
бесплатный размеченный пример. Этот модуль складывает их в таблицу
`parser_examples` и подсасывает 3 лучших в few-shot prompt parser_v2 при
следующем парсинге похожих листингов.

## Файлы

| Файл                          | Роль                                                   |
|-------------------------------|--------------------------------------------------------|
| `db_schema.py`                | `parser_examples` table + индексы                      |
| `resale_bot.py` (admin save)  | INSERT INTO parser_examples при правке                 |
| `active_learning.py`          | `pick_few_shot_examples`, `format_few_shot_prompt`, `stats` |
| `parser_v2.py` (extract_block)| опциональная инъекция few-shot                         |
| `_active_learning_audit.py`   | CLI: stats / list / sample / impact / enable / disable / pick |
| `ACTIVE_LEARNING.md`          | этот файл                                              |

## Когда включать

**ACTIVE_LEARNING_ENABLED=1** — НЕ ставить, пока не накоплено **100+
проверенных примеров** в `parser_examples` и админ вручную не одобрил
их (used_in_prompt=TRUE). Проверка:

```
py -3 _active_learning_audit.py impact
```

Команда покажет, сколько активных примеров, средний размер источника и
оценочный overhead на каждый EXTRACT-вызов (≈200 токенов/пример × 3 =
~600 токенов).

## Workflow

1. Парсер v2 пометил листинг как low-conf → попадает в `review_queue`.
2. Админ открывает «На проверке» в @vadim_admin_bot, тыкает ✏️ Здание /
   Район / Цена / Спальни, вводит правильное значение, жмёт ✅ Сохранить.
3. `_save_parser_example` (resale_bot.py) пишет в `parser_examples`:
   `source_text` = `listings.original_text`, `correct_output` = текущие
   поля + правки, `llm_original_output` = текущие поля до правки,
   `example_type` = apartment | villa | commercial | multi_unit.
   `used_in_prompt = FALSE` по умолчанию.
4. Раз в неделю админ просматривает новые примеры:
   ```
   py -3 _active_learning_audit.py list --days 7
   py -3 _active_learning_audit.py sample 10
   ```
   Хорошие включает в allow-list:
   ```
   py -3 _active_learning_audit.py enable 42 43 47 51
   ```
5. Когда наберётся 100+ active → ставим `ACTIVE_LEARNING_ENABLED=1` в
   Railway env, перезапускаем worker.
6. `pick_few_shot_examples(text, n=3)` фильтрует по типу (apartment /
   villa / commercial) и similar length, берёт top-30, диверсифицирует
   по building + price-bucket, отдаёт топ-3.

## Маркировка вредных примеров

Если в проде после включения видишь, что парсер «затвердевает» на
неправильном паттерне — найди виновный пример и отключи:

```
py -3 _active_learning_audit.py disable 123
```

`used_in_prompt=FALSE` гарантирует, что пример больше никогда не
подмешивается в prompt. Удалять строку не нужно — оставляем как
исторический случай для bug knowledge base.

## Риски

* **Prompt drift** — если разрешить много похожих примеров (все из
  Dubai Marina), парсер начнёт пихать «Dubai Marina» даже там, где её
  нет. Защита: `_diversify` режет дубликаты по building и price-bucket;
  допустимо максимум 2 примера в одном bucket.
* **Token cost** — 3 примера × ~200 токенов = ~600 токенов на каждый
  EXTRACT-вызов. При 10K листингов/день это ~6M токенов/день extra.
  Считаем заранее через `impact`.
* **Ground-truth quality** — админ тоже ошибается. Если правка
  выглядит сомнительной (например, building изменили на пустую строку),
  отключай через disable.
* **Каскад** — неправильный пример → парсер выдаёт ту же ошибку на
  новых листингах → они попадают в review_queue → админ их «правит»,
  закрепляя ошибку. Защита: weekly audit + sample-проверки.

## Лимиты (hard-coded)

* `MAX_FEWSHOT = 3` — максимум примеров в одном prompt
* `MAX_SRC_LEN = 600` chars — кап на source_text в prompt
* `MAX_OUT_LEN = 400` chars — кап на JSON correct_output
* SQL pull = top 30, отсортированных по `ABS(LEN_diff) + created_at DESC`

## Quick smoke test

```
py -3 active_learning.py
py -3 _active_learning_audit.py stats
py -3 _active_learning_audit.py pick "Dubai Marina 2BR for sale"
```
