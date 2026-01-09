# Data Finder Agent (single-cell paper ➜ datasets)

This experiment explores an "agent" that starts from a **paper URL** (e.g. PubMed/PMC) and tries to locate **public data** associated with that paper. The goal is to find *analysis-ready* datasets (ideally `.h5ad` or other single-cell formats) that you can load directly into Scanpy/AnnData without relying on pre-packaged R datasets.

## Goals

- Accept a paper link (PubMed/PMC/DOI landing page).
- Extract likely dataset accessions and data repositories (GEO/SRA/ArrayExpress/ENA, etc.).
- Enumerate candidate files (e.g., `.h5ad`, `.h5`, `.loom`, `.mtx.gz`).
- Produce a structured report with data sources and download URLs.
- Optionally use an LLM for "ranking" or summarizing candidates.

## Quick start

```bash
cd research/data-finder
python data_finder.py --paper-url https://pubmed.ncbi.nlm.nih.gov/27345837/
```

This writes a JSON report to `outputs/<pmid>-report.json` and prints a concise summary to stdout.

## Approach (current)

1. **Fetch paper text** from the provided URL (HTML).
2. **If PubMed**: use NCBI E-utilities to pull the XML metadata and parse `DataBankList` and accession numbers.
3. **Regex extraction** of common accessions: `GSE`, `GSM`, `SRP`, `SRR`, `PRJNA`, `PRJEB`, `E-MTAB`, etc.
4. **Repository probing**:
   - For each GEO `GSE`, scrape the GEO landing page for supplementary file links.
   - Look for potential single-cell formats (`.h5ad`, `.h5`, `.loom`, `.mtx.gz`, `.tar.gz`).
5. **Optional LLM refinement** (when `OPENAI_API_KEY` is set and the `openai` package is installed): rank the candidate files by likely usefulness.

## Outputs

Reports are stored under `outputs/` as JSON containing:

- `paper_url`
- `paper_text_excerpt`
- `accessions`
- `geo_supplementary_files`
- `candidate_files`
- `notes`

## Strategy experiments

This prototype supports two strategies:

1. **Deterministic/regex-first** (default): fast, transparent, and easy to debug.
2. **LLM-assisted ranking** (optional): uses a model to rank candidate files or suggest which accession looks most relevant.

You can compare these strategies by running with and without `OPENAI_API_KEY`.

## Dependencies

Install dependencies:

```bash
pip install -r requirements.txt
```

## Notes / Known limitations

- Many single-cell papers only deposit raw FASTQ or sparse matrices; true `.h5ad` may not be available.
- GEO supplemental files sometimes require manual inspection or follow-up (e.g., tarballs).
- Some journals host data via project-specific FTP/HTTP rather than standard repositories.

## Next ideas

- Add targeted repository adapters (e.g., HCA Data Portal, CZ CELLxGENE).
- Expand parsing for supplementary datasets embedded in PDFs.
- Add conversion steps (e.g., `mtx` ➜ `h5ad`) as a follow-on experiment.
