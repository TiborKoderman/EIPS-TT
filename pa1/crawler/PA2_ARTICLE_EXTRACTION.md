# PA2: Article-Only ekstrakcija (MedOverNet)

Ta dokument opisuje, kaj je bilo narejeno v trenutnem tasku za PA2, kako se to požene, kaj pomeni vsak CLI flag in kako interpretirati output.

## 1) Kaj je bilo narejeno

V tem tasku je bil implementiran **article-only extraction pipeline** za domeno:

- `https://medover.zurnal24.si/`

Fokus je samo na člankih (ne forumi).

### Dodane/posodobljene datoteke

- `pa1/crawler/src/core/article_extractor.py`
  - glavna ekstrakcijska logika za članke
  - URL-level filter (hitro izloči očitne ne-article strani)
  - izbor najboljšega content root elementa z več selektorji + scoring
  - boilerplate cleanup (header/menu/footer/share/widget/login/forum promo itd.)
  - link-density cleanup (teaser/related/navigation bloki)
  - ekstrakcija:
    - `title`
    - `author` (opcijsko)
    - `published_at` (opcijsko)
    - `section_headings` (opcijsko)
    - `body_paragraphs`
    - `cleaned_content` (plain text)
  - rezultat vsebuje tudi:
    - `is_article`
    - `reason` (zakaj je bil URL sprejet ali zavrnjen)

- `pa1/crawler/src/article_extraction_cli.py`
  - single-page CLI za hiter ročni QA
  - omogoča test z URL-jem ali lokalnim HTML file-om
  - izpis JSON rezultata + opcijski izpis `cleaned_content`

- `pa1/crawler/src/article_extraction_validate.py`
  - batch validacija nad seznamom URL-jev iz CSV
  - izračun summary metrik (fetch uspeh, št. člankov, povprečne dolžine, labeled accuracy)
  - izhod:
    - full JSON report
    - opcijski CSV report

- `pa1/tmp/extraction_sample/validation_urls.sample.csv`
  - sample input za validacijo (mix article + non-article URL-jev)

- generirani artefakti (iz testnega runa):
  - `pa1/tmp/extraction_sample/article_01.json`
  - `pa1/tmp/extraction_sample/validation_report.json`
  - `pa1/tmp/extraction_sample/validation_report.csv`

## 2) Zakaj je implementacija taka

- Uporabljen je **večstopenjski pristop** (URL gate + content scoring + cleanup), ker je bolj robusten kot en sam CSS/XPath selector.
- Ekstrakcija vrača `is_article` in `reason`, da je decision audit-friendly in razložljiv v poročilu.
- `cleaned_content` je pripravljen za naslednjo fazo (segmentacija/chunking, embeddingi, retrieval).
- Regex se uporablja minimalno in le za manjša cleanup opravila (npr. whitespace normalizacija), ne za glavno strukturo strani.

## 3) Kako pognati (single-page QA)

### 3.1 URL test

```powershell
python pa1/crawler/src/article_extraction_cli.py --url "https://medover.zurnal24.si/ti-dve-pogosti-navadi-podvojita-tveganje-za-srcno-in-mozgansko-kap/" --user-agent "fri-wier-EIPS-TT" --print-cleaned
```

### 3.2 Shrani rezultat v JSON

```powershell
python pa1/crawler/src/article_extraction_cli.py --url "https://medover.zurnal24.si/ti-dve-pogosti-navadi-podvojita-tveganje-za-srcno-in-mozgansko-kap/" --output-json "pa1/tmp/extraction_sample/article_01.json"
```

### 3.3 Lokalni HTML file test

```powershell
python pa1/crawler/src/article_extraction_cli.py --html-file "C:\pot\do\lokalne_strani.html" --print-cleaned
```

## 4) CLI flagi za `article_extraction_cli.py`

- `--url <URL>`
  - URL strani, ki jo želiš ekstrahirati.
  - Uporabi za real-time test na live strani.

- `--html-file <PATH>`
  - Pot do lokalne HTML datoteke.
  - Uporabi za offline test ali reproducibilen debug.

- `--output-json <PATH>`
  - Shrani extraction rezultat kot JSON.
  - Če ne podaš, se JSON izpiše v terminal.

- `--print-cleaned`
  - Poleg JSON izpiše še `cleaned_content` v terminal.
  - Uporabno za ročni quality check.

- `--timeout <SECONDS>`
  - HTTP timeout za URL fetch.
  - Default: `20.0`

- `--user-agent <STRING>`
  - User-Agent header za HTTP fetch.
  - Default: `fri-wier-EIPS-TT`

## 5) Kako pognati batch validacijo

```powershell
python pa1/crawler/src/article_extraction_validate.py --input pa1/tmp/extraction_sample/validation_urls.sample.csv --output-json pa1/tmp/extraction_sample/validation_report.json --output-csv pa1/tmp/extraction_sample/validation_report.csv --user-agent "fri-wier-EIPS-TT"
```

## 6) CLI flagi za `article_extraction_validate.py`

- `--input <PATH>`
  - CSV datoteka z glavo:
    - `url`
    - `expected_is_article` (opcijsko; `true/false`)
  - Primer:
    ```csv
    url,expected_is_article
    https://medover.zurnal24.si/...,true
    https://medover.zurnal24.si/forum/,false
    ```

- `--output-json <PATH>`
  - Shrani full report:
    - `summary`
    - `results` (per-URL detajli)

- `--output-csv <PATH>` (opcijsko)
  - Shrani tabelarični per-URL report za ročni pregled (Excel/Sheets).

- `--user-agent <STRING>`
  - User-Agent za batch HTTP fetch.

- `--timeout <SECONDS>`
  - HTTP timeout za posamezen request.
  - Default: `20.0`

## 7) Kaj dobiš v outputu

### JSON (single ali batch)

Ključna polja:

- `is_article`: `true/false`
- `reason`: razlaga odločitve
- `title`, `author`, `published_at`
- `section_headings`
- `body_paragraphs`
- `cleaned_content`
- `paragraph_count`
- `cleaned_content_chars`

### Batch summary

- `total_urls`
- `fetch_ok`
- `fetch_failed`
- `predicted_articles`
- `predicted_non_articles`
- `avg_cleaned_content_chars_for_articles`
- `avg_paragraph_count_for_articles`
- `labeled_samples`
- `labeled_matches`
- `labeled_accuracy` (če so labels podani)

## 8) Znane omejitve in opombe

- Če je v okolju blokiran network/socket, bodo URL fetch-i padli (npr. WinError 10013) in batch bo vrnil `fetch_failed`.
- Placeholder URL-ji tipa `https://medover.zurnal24.si/...` niso veljavni URL-ji.
- Za pravilno validacijo uporabi realne članke in nekaj kontrolnih ne-article URL-jev.

## 9) Priporočen workflow

1. Najprej preveri 1–2 članka z `article_extraction_cli.py`.
2. Nato poženi batch validacijo na labeled sample CSV.
3. Preglej `reason` in `cleaned_content` pri edge primerih.
4. Šele potem integriraj v DB pipeline (`cleaned_content` write), ko ste zadovoljni s stabilnostjo.

