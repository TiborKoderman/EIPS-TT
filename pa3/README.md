# PA3 - Retrieval-Augmented Generation nad PA2 korpusom

## Namen

PA3 razširi preverjeno rešitev iz PA2 z generiranjem odgovorov prek lokalnega
modela Ollama. Sistem primerja dva načina:

- **With Context (RAG):** vprašanje se najprej poišče v PA2 vektorskem indeksu,
  zadetki se rerankajo in se kot dokazila vključijo v prompt.
- **Without Context (LLM-only):** isti model odgovori le na vprašanje, brez
  dostopa do dokumentov.

Implementacija namenoma ne spreminja PA2 segmentacije ali embeddingov:
uporablja `crawldb.page_segment_long`, `sentence-transformers/LaBSE`,
cosine razdaljo v `pgvector` in reranker `BAAI/bge-reranker-v2-m3`.

## Struktura

```text
pa3/
├── README.md
├── report.pdf
├── report/
│   └── report.tex
└── rag/
    ├── evaluate.py
    ├── generator.py
    ├── prompts.py
    ├── queries.json
    ├── rag_pipeline.py
    ├── retriever.py
    └── runs/
```

## Zahteve

- Python 3.10+ z odvisnostmi iz korenskega `requirements.txt`
- PostgreSQL z razširitvijo `pgvector` in obnovljenim PA2 dumpom
- lokalni [Ollama](https://ollama.com/) strežnik
- model `gemma3:4b`

Na Windows ustvarite sveže okolje, če je obstoječi `.venv` nastal na drugem
operacijskem sistemu:

```powershell
python -m venv .venv-pa3
.\.venv-pa3\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## PA2 baza

PA3 pričakuje že izračunane PA2 segmente in embeddinge. Bazo je mogoče
obnoviti iz obstoječega dumpa:

```powershell
docker compose up -d db
pg_restore -h localhost -p 5432 -U postgres -d crawldb `
  --no-owner --no-acl pa2/extraction-db/crawldb_pa2.dump
```

Preverjanje prisotnosti dolgih embeddingov:

```powershell
docker compose exec db psql -U postgres -d crawldb `
  -c "SELECT COUNT(*) FROM crawldb.page_segment_long WHERE embedding IS NOT NULL;"
```

Če je namesto dumpa na voljo svež PA1 korpus, se PA2 pipeline izvede po
navodilih v `pa2/README.md`; PA3 sama ne segmentira in ne vektorizira vsebin.

## Ollama

Namestite Ollama, zaženite lokalni servis in prenesite isti model za vse
evalvacijske zagone:

```powershell
ollama pull gemma3:4b
ollama list
```

Uporabljen je `gemma3:4b`, približno 4B-parametrski model, ker je dovolj
kompakten za lokalno izvajanje ter podpira večjezične odgovore, vključno s
slovenščino. Če stroj modela ne zmore, lahko CLI prejme drug model z
`--model`, vendar morajo biti vsi rezultati v poročilu ustvarjeni z istim
modelom.

## Enkratno vprašanje

Oba načina v enem zagonu:

```powershell
python pa3/rag/rag_pipeline.py `
  --query "Kateri so simptomi visokega krvnega tlaka in zakaj je merjenje pomembno?" `
  --mode both
```

Zapis sledljivega rezultata v JSON:

```powershell
python pa3/rag/rag_pipeline.py `
  --query "Kateri znaki lahko kažejo na pomanjkanje vitamina B12?" `
  --mode both `
  --json-output pa3/rag/runs/single_b12.json
```

Privzeti retrieval parametri so:

| Parameter | Vrednost |
| --- | --- |
| Segmenti | `page_segment_long` |
| Embedding model | `sentence-transformers/LaBSE` |
| Metrika | cosine distance |
| Kandidati pred rerankingom | `20` |
| Kontekst za generiranje | `5` segmentov |
| Reranker | `BAAI/bge-reranker-v2-m3` |
| `ivfflat.probes` | `10` |

Vsak izpisani dokaz vsebuje URL, besedilo segmenta, cosine razdaljo,
rerank score ter rang pred in po rerankingu.

## Evalvacija

`queries.json` vsebuje šest vprašanj z vnaprej potrjeno pokritostjo korpusa
in tri diagnostične neuspešne primere iz PA2. Celoten poskus izvedete z:

```powershell
python pa3/rag/evaluate.py
```

Rezultat se shrani v:

```text
pa3/rag/runs/<timestamp>_evaluation.json
```

Za vsako vprašanje JSON vsebuje pridobljene dokaze, odgovor z dokumenti,
odgovor brez dokumentov, parametre eksperimenta ter rubriko
`manual_evaluation`. Po zagonu ročno vnesite:

- `retrieval_relevance_0_to_2`: `0` nerelevantno, `1` delno, `2` relevantno;
- `rag_answer_quality_0_to_2` in `llm_only_answer_quality_0_to_2`;
- komentar o groundedness in transparency;
- tip napake: `irrelevant retrieval`, `insufficient corpus coverage`,
  `ambiguous query` ali `generation hallucination`.

Končni evalvacijski zagon za oddajo je shranjen v:

```text
pa3/rag/runs/20260530T022922Z_evaluation.json
```

V tem zagonu je obnovljena PA2 baza vsebovala `36,816` dolgih segmentov z
embeddingi. Vseh devet primerov ima izpolnjene ročne ocene in komentarje.

Za preverjanje pričakovanega neuspeha zunaj domene:

```powershell
python pa3/rag/rag_pipeline.py `
  --query "Kakšne so pravne posledice neplačila prometne kazni v Sloveniji?" `
  --mode both
```

Pri tem primeru mora odgovor RAG jasno povedati, da pridobljeni medicinski
viri ne zadostujejo za pravni odgovor.

## Poročilo

LaTeX vir poročila je v `pa3/report/report.tex`, obvezni PDF artefakt pa v
`pa3/report.pdf`. PDF se ponovno zgradi z:

```powershell
latexmk -pdf -output-directory=pa3/report pa3/report/report.tex
Copy-Item pa3/report/report.pdf pa3/report.pdf -Force
```

Poročilo vsebuje dejanske rezultate končnega evalvacijskega zagona,
primerjalno tabelo za vseh devet vprašanj ter razpravo o uspešnih in
neuspešnih primerih retrievala.

## Omejitev uporabe

Sistem je izdelan za eksperimentalno analizo RAG in razložljivosti.
Generirani odgovori o zdravju niso zdravstveni nasvet in ne nadomeščajo
pregleda ali posveta z usposobljenim zdravstvenim strokovnjakom.
