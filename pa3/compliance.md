# PA3 compliance report

Namen: sledljiv pregled zahtev iz `.github/instructions/pa3.md` proti trenutni implementaciji.

Legenda:

- `✅ compliant` izvedeno skladno z zahtevo;
- `❌ missing` ni izvedeno ali ni preverljivo v repozitoriju;
- `⚠️ deviated` izvedeno drugače od navodil;
- `🔵 partial` delno izvedeno ali odvisno od zunanje oddaje.

## Requirement table

| ID | Requirement | ★ | Points | Status | Files |
|----|-------------|:-:|--------|:------:|-------|
| PA3-01 | Query embedding: vprašanja se pretvorijo v vektorske embeddinge. | ★ | n/a | ✅ compliant | `pa3/rag/retriever.py` |
| PA3-02 | Retrieval iz PA2: uporabi vektorsko podobnost nad PA2 korpusom. | ★ | n/a | ✅ compliant | `pa3/rag/retriever.py`, `pa2/extraction-db/crawldb_pa2.dump` |
| PA3-03 | Reranking: po vektorskem iskanju se zadetki rerankajo. | ★ | n/a | ✅ compliant | `pa3/rag/retriever.py`, `pa2/crawler/src/rerank_crossencoder.py` |
| PA3-04 | With Context prompt združi poizvedbo in pridobljene dokumentne chunke. | ★ | n/a | ✅ compliant | `pa3/rag/prompts.py` |
| PA3-05 | Without Context prompt uporabi samo vprašanje, brez zunanjih dokumentov. | ★ | n/a | ✅ compliant | `pa3/rag/prompts.py` |
| PA3-06 | Answer generation za oba načina uporablja Ollama-based LLM. | ★ | n/a | ✅ compliant | `pa3/rag/generator.py`, `pa3/rag/evaluate.py` |
| PA3-07 | Retrieved documents so inspectable: URL, tekst, distance, rank, rerank score. | ★ | n/a | ✅ compliant | `pa3/rag/rag_pipeline.py`, `pa3/rag/runs/20260530T022922Z_evaluation.json` |
| PA3-08 | Evalvacija vsebuje najmanj 6 dobrih RAG vprašanj. | ★ | n/a | ✅ compliant | `pa3/rag/queries.json`, `pa3/rag/runs/20260530T022922Z_evaluation.json` |
| PA3-09 | Evalvacija vsebuje najmanj 3 slabe, dvoumne ali zavajajoče RAG primere. | ★ | n/a | ✅ compliant | `pa3/rag/queries.json`, `pa3/rag/runs/20260530T022922Z_evaluation.json` |
| PA3-10 | Za vsak evalvacijski query sta primerjana With Context in Without Context odgovora. | ★ | n/a | ✅ compliant | `pa3/rag/evaluate.py`, `pa3/report/report.tex` |
| PA3-11 | Report opiše embedding model, Ollama LLM, obseg modela in izbiro modela. | ★ | n/a | ✅ compliant | `pa3/report/report.tex`, `pa3/report.pdf` |
| PA3-12 | Report opiše strukturo, chunking in shranjevanje PA2 dokumentov. | ★ | n/a | ✅ compliant | `pa3/report/report.tex`, `pa3/report.pdf` |
| PA3-13 | Report opiše integracijo retrievala, rerankinga in generiranja. | ★ | n/a | ✅ compliant | `pa3/report/report.tex`, `pa3/report.pdf` |
| PA3-14 | Report vsebuje evalvacijska merila za data processing, retrieval in answer quality. | ★ | n/a | ✅ compliant | `pa3/report/report.tex`, `pa3/report.pdf` |
| PA3-15 | Report vsebuje tabelo 9 queryjev z RAG odgovorom, LLM-only odgovorom in komentarjem. | ★ | n/a | ✅ compliant | `pa3/report/report.tex`, `pa3/report.pdf` |
| PA3-16 | Report razpravlja, kdaj retrieval izboljša odgovor, kdaj odpove in kako pomaga razložljivosti. | ★ | n/a | ✅ compliant | `pa3/report/report.tex`, `pa3/report.pdf` |
| PA3-17 | README vsebuje kratek opis, setup in uporabo RAG sistema. | ★ | n/a | ✅ compliant | `pa3/README.md` |
| PA3-18 | Oddajna struktura vsebuje report PDF, README in `rag/` implementacijo. | ★ | n/a | ⚠️ deviated | `pa3/report.pdf`, `pa3/README.md`, `pa3/rag/` |
| PA3-19 | `report.pdf` je zgrajen iz posodobljenega report source. | ★ | n/a | ✅ compliant | `pa3/report/report.tex`, `pa3/report.pdf` |
| PA3-20 | GitHub repozitorij ima dodanega uporabnika `opbieps` z vsaj read dostopom. | ★ | n/a | ❌ missing | external |
| PA3-21 | Oddan je `.txt` z linkom do zasebnega GitHub repozitorija. | ★ | n/a | ❌ missing | external |

## Deviation table

| ID | Requirement | Actual Implementation | Reasoning | Severity |
|----|-------------|----------------------|-----------|----------|
| D1 | Navodila zahtevajo `/PA3/report.pdf`, `/PA3/README.md`, `/PA3/rag/`. | Repozitorij uporablja obstoječo lowercase strukturo `pa3/`, skladno z `pa1/` in `pa2/`. | Preimenovanje bi bilo širše in nepotrebno tveganje tik pred oddajo; vse zahtevane vsebine so prisotne v `pa3/`. | Low |

## Evidence summary

- Local DB restore verified: `36,816` rows in `crawldb.page_segment_long` with non-null embeddings.
- Ollama model verified: `gemma3:4b` present locally.
- Smoke test passed in both modes for blood-pressure query.
- Full evaluation run saved in `pa3/rag/runs/20260530T022922Z_evaluation.json`.
- Manual scores completed for all 9 queries.
- Final report source updated and PDF rebuilt.

## Remaining non-repo tasks

- Add `opbieps` to the private GitHub repository with at least read access.
- Prepare and submit the required `.txt` file containing the private GitHub repository link.
- Only one group member should perform the final submission.
