# PA3 - obvezni TODO za oddajo

Legenda:

- `[x]` že implementirano ali prisotno;
- `[ ]` treba še dokončati ali preveriti pred oddajo;
- `[!]` trenutno neskladje z navodili.

## 1. Zahtevana oddajna struktura

- [x] Pripravljen je `pa3/report.pdf`.
- [x] Pripravljen je `pa3/README.md`.
- [x] Pripravljena je implementacija v `pa3/rag/`.
- [x] Pred oddajo mora biti končna struktura:

```text
pa3/
├── report.pdf
├── README.md
└── rag/
```

## 2. Obvezna funkcionalnost RAG sistema

### Query Embedding

- [x] Vprašanje se pretvori v embedding z modelom `sentence-transformers/LaBSE`.

### Document Retrieval iz PA2

- [x] Sistem uporablja dokumentne segmente iz PA2 baze.
- [x] Retrieval uporablja vektorsko podobnost nad `crawldb.page_segment_long`.
- [x] Po vector retrievalu se izvede reranking z `BAAI/bge-reranker-v2-m3`.
- [x] Pridobljeni segmenti so vidni za pregled: besedilo, URL in ocene/rangi.
- [x] Na delujoči PA2 bazi preveri, da retrieval dejansko vrne relevantne segmente.

### Prompt Construction

- [x] Način **With Context** združi vprašanje in retrieved segmente v prompt.
- [x] Način **Without Context** modelu poda samo vprašanje brez dokumentov.

### Answer Generation

- [x] Oba načina uporabljata Ollama model `gemma3:4b`.
- [x] Zaženi Ollamo in preveri model:

```powershell
ollama pull gemma3:4b
```

- [x] Preveri, da en query uspešno vrne:
  - retrieved kontekst,
  - odgovor z dokumenti,
  - odgovor brez dokumentov.

```powershell
python pa3/rag/rag_pipeline.py `
  --query "Kateri so simptomi visokega krvnega tlaka in zakaj je merjenje pomembno?" `
  --mode both
```

## 3. Obvezna evalvacija

- [x] Pripravljenih je najmanj `6` dobrih RAG vprašanj.
- [x] Pripravljena so najmanj `3` slaba, dvoumna ali zavajajoča RAG vprašanja.
- [x] Evaluacijska skripta za vsako vprašanje generira odgovor z dokumenti in brez dokumentov.
- [x] Zaženi evalvacijo vseh `9` vprašanj:

```powershell
python pa3/rag/evaluate.py
```

- [x] Preveri, da rezultat vsebuje za vsako vprašanje:
  - query,
  - retrieved segmente,
  - odgovor **With Context**,
  - odgovor **Without Context**,
  - komentar oziroma oceno relevantnosti in kakovosti.
- [x] Ročno oceni vseh `9` primerov.
- [x] Pri slabih primerih določi razlog napake, npr. nerelevanten retrieval, nepokritost korpusa, dvoumno vprašanje ali halucinacija modela.

## 4. Obvezna vsebina poročila

### Implementation Summary

- [x] Navedena sta embedding model `LaBSE` in Ollama LLM `gemma3:4b`.
- [x] Naveden je približen obseg LLM modela in razlog izbire.
- [x] Opisano je, kako so PA2 dokumenti strukturirani, segmentirani in shranjeni.
- [x] Opisano je povezovanje retrievala, rerankinga in generiranja.
- [x] Opisani so uporabljeni dodatni pristopi/knjižnice.

### System Evaluation Criteria

- [x] Opisana je kakovost priprave podatkov in izbira chunkinga.
- [x] Opisani so retrieval parametri in način presoje relevantnosti segmentov.
- [x] Opisano je, kako se ocenjuje kakovost odgovorov in vrste napak.

### Evaluation Results

- [x] Poročilo vsebuje zahtevane dejanske rezultate izvedene evalvacije.
- [x] Dodaj tabelo za vseh `9` vprašanj, ki vsebuje:
  - vprašanje,
  - odgovor z retrieved kontekstom,
  - odgovor brez retrieved konteksta,
  - komentar oziroma oceno.
- [x] V razpravi odgovori:
  - kdaj retrieval izboljša kakovost odgovora;
  - kdaj retrieval odpove in zakaj;
  - kako dokazi vplivajo na razložljivost, zaupanje in diagnostiko napak.
- [x] Po dopolnitvi rezultatov ponovno zgradi končni `pa3/report.pdf`.

## 5. README zahteve

- [x] README vsebuje kratek opis projekta.
- [x] README vsebuje navodila za namestitev in uporabo RAG sistema.
- [x] README vsebuje navodila za PA2 bazo in Ollamo.
- [x] README vsebuje ukaze za single-query zagon in evalvacijo.

## 6. Oddaja

- [ ] Vse zahtevane datoteke pushaj v isti zasebni GitHub repozitorij kot prejšnjo nalogo.
- [ ] Uporabniku `opbieps` dodaj najmanj `read` dostop do zasebnega repozitorija.
- [ ] Pripravi in oddaj `.txt` datoteko s povezavo do zasebnega GitHub repozitorija.
- [ ] Oddajo izvede samo en član skupine.

## Pred oddajo mora nujno veljati

- [x] Mapa je `pa3/` in vsebuje `report.pdf`, `README.md` ter `rag/`.
- [x] Ollama RAG pipeline uspešno deluje v obeh načinih.
- [x] Izvedenih in ročno analiziranih je vseh `6 + 3` vprašanj.
- [x] Poročilo vsebuje dejansko tabelo rezultatov in razpravo.
- [ ] GitHub dostop za `opbieps` ter `.txt` povezava sta urejena.
