# Parser Rules — все защиты против известных багов

Этот документ описывает все правила парсера, накопленные через диалоги.
Каждое правило соответствует реальному найденному в БД багу.

## 1. Извлечение цены (`extract_price` + `_parse_amount`)

| # | Правило | Защищает от |
|---|---------|-------------|
| 1.1 | `_strip_phones` regex (диапазоны +971/+966/etc) | «551,000,000 AED» из «+971 55 109 0599» |
| 1.2 | `(\d)[,.]5\s*bedroom` pre-check | «2,5 bedroom» → 5 BR |
| 1.3 | Mixed European `(\d{1,3})\.(\d{3})\s+(\d{3})` collapse | «2.300 000 AED» |
| 1.4 | Broken mixed `(\d).(\d{3}),(\d{3})` collapse | «7.800,000» = 7.8M |
| 1.5 | `_parse_amount` lstrip `.,-*:` | «Price. 650k» → 650000 (не 650) |
| 1.6 | `\d+\s*aed/per\s*sq.\?ft/psf/sqm` strip | «1300aed per sqft» reference |
| 1.7 | `service\s+charge` strip | «Service charge 15,527 AED» |
| 1.8 | `rental\s+income\|annual\s+rental\|rented\s+at` strip | rental income взят как sale price |
| 1.9 | `market\s+price\|avg\s+price` strip | reference price ≠ actual price |
| 1.10 | Discount-in-parens strip | «AED 3M (130k discount)» |
| 1.11 | Cyrillic М/К → M/K conversion | «3,5М», «800К» |
| 1.12 | Service charges / DLD fees strip | «DLD 4% AED 120,000» (требует `fee` или `4%`) |
| 1.13 | `Reference No.: #34881961` / `Permit No.` / `RERA #` strip | parser брал ID как цену |
| 1.14 | `$XXX` dollar-conversion в скобках strip | «AED 1,420,000 (~$387K)» → 387K leak |
| 1.15 | «X on handover/transfer/completion» strip | down-payment не цена |
| 1.16 | «X to owner / X to developer» strip | сплит-платёж не цена |
| 1.17 | «X left posthandover» / «X remaining» strip | остаток не цена |
| 1.18 | «Pay X now» strip | первый взнос не цена |
| 1.19 | «X each month/monthly during N years» strip | installment не цена |
| 1.20 | «Rented at/till/until/for N AED/year» strip | rental income не sale price |
| 1.21 | `(N k/year)` / `(N yearly)` parenthesised rent strip | rent leak в скобках |
| 1.22 | `(OP N)` без K/M suffix strip | "OP 2,355" → ambiguous |
| 1.23 | Broken European «N.NNN.NN» → N,NNN,000 | «3.100.00 AED» = 3,100,000 |

## 2. Определение deal_type (`detect_deal_type`)

| # | Правило | Защищает от |
|---|---------|-------------|
| 2.1 | `prop_type in (plot, whole_building) → deal_type='sale'` | Plot/Building всегда продажа |
| 2.2 | Magnitude override: `price ≥ 500k + rent_score==0 → sale` | мульти-листинги где парсер угадывал rent |
| 2.3 | `validate_deal_type_by_price` против рыночных floor | rent < 20k → service charge, не аренда |
| 2.4 | `price ≥ 1.2M` → sale (если первый абзац НЕ «for rent») | multi-listing с rent income упоминанием |
| 2.5 | `\bN cheques\b` / `\bN chq\b` pattern → rent | «AED 330k | 3 cheques» классифицировался как sale |
| 2.6 | `\brent\s*:?\s*\d` extra rent signal | «Rent 155K» без других маркеров |
| 2.7 | `ready to move in` УДАЛЁН из sale_signals | это status не deal_type — рента тоже может быть ready |

## 3. Извлечение building (`detect_building` + `_extract_building_heuristic`)

### Паттерны извлечения (15 штук):
| # | Паттерн | Пример |
|---|---------|--------|
| 3.1 | DB exact match с landmark filter | «Burj Khalifa» (real) vs «Burj Khalifa view» |
| 3.2 | DB alias match | «Aycon City» → «Aykon City» canonical |
| 3.3 | `📍 X, <Emirate>` | «📍 Sea la vie, Abu Dhabi» |
| 3.4 | `🏢 Project: X` / `Project: X` | label-based |
| 3.5 | `🏡 X` standalone | emoji-led name |
| 3.6 | `Studio/N BR in X` | «STUDIO in DG1 Living» |
| 3.7 | `X, <KnownArea>` | «Nobles Tower, Business Bay» |
| 3.8 | `Unit: X` label | |
| 3.9 | `Location: X` not-area | building в location label |
| 3.10 | `📍X\n<property line>` | pin + next-line property |
| 3.11 | Markdown bold header | «**MULBERRY (Dubai Hills)**» |
| 3.12 | Area-header / building / property triplet | 3-line pattern |
| 3.13 | `X Tower/Residences/Bay/Heights/Views/Estates/Crescent/Plaza` (case-insensitive) | «Zada tower», «Marina Views 3» |
| 3.14 | First Title-Case line 2-7 words | «ELIE SAAB A VIE» |
| 3.15 | `X at <KnownArea>` / `X in <KnownArea>` | «Liv Marina at Dubai Marina» |
| 3.16 | `X by <Developer>` | «Galaxy by Binghatti» |
| 3.17 | NUMBER + Title + Suffix | «17 Icon Bay», «320 Riverside Crescent» |

### Landmark view filter (`_is_landmark_view_reference`):
- Окно 80 chars before/after building mention
- Pre-markers: `view/views/viewing/facing/near/from/overlooking/opposite/towards/stunning/close to/next to/distance from/walking distance/minutes from/minutes to/drive to/beside/adjacent to/right next to`
- Post-markers: `view/views/area/district/community/landmark`

### Стопворды (`_is_building_stopword`) — ВСЕ записаны:
- ✅ Directional: `To /From /Near /Opposite /Adjacent /Close to /Walking /Minutes /Drive /Around /Before /After /Beside /Overlooking /Facing /with view /view of`
- ✅ Descriptor + Type: `Spacious/Luxurious/Stunning/Modern/Brand New/Beautiful/Large/Huge/Massive/Premium/Exclusive/Elegant/Cozy/Bright/Sunny/Big/Rare/Unique/Distress/Hot/Prime/Cheap/Affordable/Best/Top/Excellent/Charming/Fully/Partially/Semi/Upgraded/Renovated/Vacant/Rented/Ready/Fitted/Furnished/Unfurnished` + `villa/apartment/townhouse/penthouse/studio/duplex/plot/office/unit/home/mansion/residence/deal/opportunity/investment/room`
- ✅ Type + for sale/rent: `office/retail/plot/villa/apartment/townhouse/penthouse/studio/duplex/unit/property/home/land/building/tower/hotel + for sale|rent|lease|exchange`
- ✅ Pure type alone: `villa/apartment/townhouse/penthouse/studio/duplex/plot/office/retail/property/unit/land/home/mansion/residence/tower/building/complex/hotel`
- ✅ Marketing: `for sale/for rent/for salle/hot deal/hot offer/distress deal/best deal/best price/new launch/new price/special offer/urgent sale/exclusive deal/investment opportunity/cash buyer/cash deal/payment plan/freehold/covered/for serious/huge terrace/huge balcony/spacious layout/prime business/prime tower/new tower`
- ✅ Phone/currency digits: `\+?\d{6,}|aed\s*\d|\$\s*\d|€\s*\d` → blocked
- ✅ Too long: `len(words) > 7` → blocked
- ✅ Questions / calls: `?/hello/hi/dear/dm/call/contact/please` → blocked
- ✅ Area-acronyms standalone: `JVC/JVT/JLT/JBR/DXB/DCH/DHE/DHA/DLRC/DSO` → blocked (это area-коды)
- ✅ Single-feature labels: `Covered/Spacious/Luxury/Huge Terrace/Huge Balcony/Brand New/Prime Tower/etc.` → blocked

### Post-parse guards (`parse_message`):
- ✅ `building == area` → NULL
- ✅ `prop_type == "plot" → building=NULL`
- ✅ Final `_is_building_stopword()` check на extracted building
- ✅ Hard-NULL noise words (`year/month/sale/rent/studio/apartment/villa/etc.`)
- ✅ Building начинается с `\d+\s*(?:bedroom|bed|br|bdr|bhk)` → NULL
- ✅ Building содержит `plot area/sqft area/bua/gfa` → NULL
- ✅ Strip leading `apartment in/studio in/villa in/...` префикс
- ✅ Strip leading emoji/punctuation
- ✅ Strip trailing ` - <Area>` если совпадает с area
- ✅ Strip trailing ` - PLOT` / ` PLOT`
- ✅ Building с `\n` → take first line only
- ✅ Strip prefix `Below Op/Op/Original Price/Distress/Urgent/Hot/Best/...`

## 4. Property type reclassification

| # | Правило | Защищает от |
|---|---------|-------------|
| 4.1 | Penthouse без `penthouse/pent house` → apartment/villa/whole_building/plot | mis-classified penthouse |
| 4.2 | Whole_building без keywords → plot/villa/office | mis-classified whole_building |
| 4.3 | Villa без `villa/mansion` → townhouse/apartment | text не содержит villa |
| 4.4 | Townhouse без `townhouse/town house` → villa/apartment | mis-classified TH |
| 4.5 | VILLA PLOT / VILLA LAND → plot | «VILLA PLOT» это земля |
| 4.6 | Studio с N BR mentions → multi-listing audit | studio_with_NBR |
| 4.7 | Apt с sqft > 6000 → penthouse | большой apt = penthouse |
| 4.8 | Apt с sqft > 5000 + «building for sale» → whole_building | classification |
| 4.9 | Apt в villa-community (Damac Lagoons/Hills/Tilal/Valley/Mudon/etc) + 3+ BR + plot dim → townhouse | community villas mis-classified as apartment |
| 4.10 | Plot / whole_building → bedrooms=NULL (земля/всё здание — не имеет BR) | «5 bedroom villa plot» = plot |
| 4.11 | First-block «Office N sqft» / «Premium retail» → office/retail | apartment с commercial-листингом |

## 4.5 Bedroom extraction (`extract_bedrooms`)

| # | Правило | Защищает от |
|---|---------|-------------|
| 4.5.1 | Разделитель `[ \t\-]*` (не только space/tab) | «1-bed», «1bd» не парсилось |
| 4.5.2 | Все варианты сокращений: bd/bdr/br/bed/beds/bdrs/brs/bds | пропуски при extraction |

## 5. Field sanity checks

| # | Правило | Защищает от |
|---|---------|-------------|
| 5.1 | Floor cap 0-165 (Burj Khalifa max) | floor=169 sq.m mis-parse |
| 5.2 | Floor `_not_sqm()` post-check | «Low floor 169 sq.m» |
| 5.3 | Bathrooms > 10 → NULL | impossible bath count |
| 5.4 | Bathrooms > 3× bedrooms → NULL | sqft взят как bath |
| 5.5 | Sqft > 50,000 → NULL | clearly broken |
| 5.6 | Sqft < BR * 250 → audit | impossible per BR |
| 5.7 | View length > 40 chars → NULL | description-as-view |
| 5.8 | View с цифрами `[0-9]{3,}` → NULL | sqft/price as view |

## 6. Validation floors (`_validate_listing_strict`)

| # | Правило | Result |
|---|---------|--------|
| 6.1 | Rent < 15,000 AED → `rent_absurd_low` | audit |
| 6.2 | Sale < 200,000 AED → `sale_absurd_low` | audit |
| 6.3 | Rent PSF < 8 AED/sqft → `rent_psf_absurd` | audit |
| 6.4 | Sale PSF < 150 AED/sqft → `sale_psf_absurd` | audit |
| 6.5 | Sale PSF > 8000 AED/sqft + не премиум building → audit | non-luxury overprice |
| 6.6 | Rent PSF > 500 AED/sqft → audit | mis-parse |
| 6.7 | Building too long (>100 chars) → NULL + audit | description-as-bld |
| 6.8 | Building с descriptor → `building_descriptor` audit | |
| 6.9 | Building has digits 4+ → `building_has_digits` audit | broker code |
| 6.10 | Building not in first listing block → audit | multi-listing bleed |

## 7. Multi-listing detection

- ✅ First listing block: split on 4+ consecutive newlines (`\n\s*\n\s*\n\s*\n`) OR separators
- ✅ 3+ distinct price mentions AND 2+ distinct sqft → audit `multi_listing_confirmed`
- ✅ 3+ different property types + 3+ different BR numbers → audit `mixed_types_multi`
- ✅ Plot-multi-listing (4+ «Plot size» / GFA / G+N) → audit `multi_plot_post`

## 8. Status & off-plan

- ✅ Status priority: `offplan > rented > vacant > ready`
- ✅ «Ready to sell/sign/deal» → NOT ready (это seller intent, не статус)
- ✅ «Ready to move in / completed / handed over» → status='ready'
- ✅ Off-plan markers: `off plan / under construction / handover Q[1-4] / handover \d{4}`
- ✅ Auto-correct: `is_off_plan=True + status='ready'` → status='offplan' (если handover в будущем)

## 9. Dedup (`upsert_listing` в `db_schema.py`)

- ✅ By `telegram_message_id` + `telegram_chat_id`
- ✅ By `listing_key` (property hash)
- ✅ By content signature: первые 250 chars (нормализованный whitespace)
- ✅ Phase deduplication в `get_projects()` для каталога (Verdana 2/4/5 → keep latest)

## 10. Spam / system message detection

Удаляются автоматически:
- ✅ `Please subscribe to @...`
- ✅ `Dear members of the group...`
- ✅ `This channel can't be displayed`
- ✅ `PropRequest` промо
- ✅ Buyer requests («Looking for / Urgent requirement / I'm a buyer»)
- ✅ Instagram URL-only messages
- ✅ Text < 60 chars without price/building/area

## 11. Frozen records protection

- ✅ `is_frozen=TRUE` → upsert не трогает запись (ни цену, ни building)
- Это защищает ручную очистку базы.

## Coverage stats (final)

```
Total visible: 3609 (после удаления mis-classified / spam / dupe)
  building:   80.3%  (было 38.7%, peak 87.5% до жёсткого audit)
  area:       78.7%  (было 80.1%)
  bedrooms:   92.4%  (было 88.4%)
  size_sqft:  86.3%
  price:      98.5%
  emirate:    96.5%
  status:     40.5%
  view:       42.8%
  furnishing: 26.2%
```

> Building% упал относительно пика 87.5% потому что Round 6-9 убрали ложные
> «buildings» вроде `Covered`, `Huge Terrace`, `Spacious Layout`, `1 Bedroom`,
> `Below Op X` — это правильнее иметь NULL, чем мусор.

## Bug ledger — что было поймано в production

| Bug | Count fixed | Rule |
|-----|-------------|------|
| Phone-as-price | 1+ | 1.1 |
| `Price. 650k` → 650 | многие | 1.5 |
| Mixed European format | многие | 1.3, 1.4 |
| Per-sqft as total price | id=19914 | 1.6 |
| Rental income as price | id=22658 | 1.8 |
| Floor 169 sqm bug | id=22572 | 5.1, 5.2 |
| `building == area` | 12+ | 3.post-guard |
| `Burj Khalifa view` false bld | 80+ | 3.1 landmark filter |
| `To/Near/Opposite` building | 6+ | stopword directional |
| `Spacious Villa` descriptor | 33+ | stopword descriptor |
| Multi-listing penthouse | 54 | 4.1 |
| Plot building | 23 | 3.PLOT guard |
| Exact text dups | 511 | 9 content signature |
| Field-sig dups | 82 | 9 listing_key |
| Phase dups in catalog | 23 | 9 phase dedup |
| TG spam messages | 372+ | 10 spam |

## Verification

```bash
python -u _verify_rules.py    # Запускает проверку всех правил
```

При деплое: парсер автоматически применяет правила к каждому новому сообщению.
БД защищена content-signature dedup от повторной вставки.
