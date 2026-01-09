#!/usr/bin/env python3
"""Find likely public datasets associated with a paper URL."""

from __future__ import annotations

import argparse
import json
import os
import re
import textwrap
import time
import urllib.parse
import importlib.util
from dataclasses import dataclass, asdict
from typing import Iterable, List

import requests
from bs4 import BeautifulSoup
from xml.etree import ElementTree

USER_AGENT = "data-finder/0.1 (+https://example.org)"
HEADERS = {"User-Agent": USER_AGENT}

ACCESSION_PATTERNS = {
    "GEO_SERIES": re.compile(r"\bGSE\d{3,}\b"),
    "GEO_SAMPLE": re.compile(r"\bGSM\d{3,}\b"),
    "SRA_PROJECT": re.compile(r"\bSRP\d{3,}\b"),
    "SRA_RUN": re.compile(r"\bSRR\d{3,}\b"),
    "BIOPROJECT": re.compile(r"\bPRJ[EN]A\d+\b"),
    "ARRAYEXPRESS": re.compile(r"\bE-(MTAB|GEOD)-\d+\b"),
}

CANDIDATE_FILE_EXTENSIONS = (
    ".h5ad",
    ".h5",
    ".loom",
    ".mtx.gz",
    ".mtx",
    ".csv",
    ".tsv",
    ".rds",
    ".tar.gz",
)


@dataclass
class Report:
    paper_url: str
    paper_text_excerpt: str
    accessions: dict[str, List[str]]
    geo_supplementary_files: dict[str, List[str]]
    candidate_files: List[str]
    notes: List[str]
    llm_ranking: List[str]


@dataclass
class LlmCandidate:
    url: str
    reason: str


def fetch_text(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "lxml")
    text = " ".join(soup.stripped_strings)
    return text


def extract_pubmed_id(url: str) -> str | None:
    match = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)/", url)
    return match.group(1) if match else None


def fetch_pubmed_xml(pmid: str) -> str:
    params = {
        "db": "pubmed",
        "id": pmid,
        "retmode": "xml",
    }
    response = requests.get(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
        params=params,
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def extract_accessions(text: str) -> dict[str, List[str]]:
    found: dict[str, List[str]] = {}
    for label, pattern in ACCESSION_PATTERNS.items():
        hits = sorted(set(pattern.findall(text)))
        if hits:
            found[label] = hits
    return found


def parse_pubmed_accessions(xml_text: str) -> dict[str, List[str]]:
    accessions: dict[str, List[str]] = {}
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return accessions

    for databank in root.findall(".//DataBank"):
        name = databank.findtext("DataBankName")
        for acc in databank.findall(".//AccessionNumber"):
            if name:
                accessions.setdefault(name.upper(), []).append(acc.text)

    return {key: sorted(set(values)) for key, values in accessions.items()}


def extract_candidate_files(text: str) -> List[str]:
    candidates = []
    for ext in CANDIDATE_FILE_EXTENSIONS:
        candidates.extend(re.findall(rf"https?://\S+?{re.escape(ext)}", text))
    return sorted(set(candidates))


def fetch_geo_supplementary(gse: str) -> List[str]:
    url = f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={gse}"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "lxml")
    links = [a["href"] for a in soup.select("a[href]")]
    supplementary = [
        urllib.parse.urljoin(url, link)
        for link in links
        if "ftp" in link or "supplementary" in link
    ]
    return sorted(set(supplementary))


def has_openai() -> bool:
    return importlib.util.find_spec("openai") is not None


def rank_candidates_llm(candidates: List[str], context: str) -> List[LlmCandidate]:
    if not candidates:
        return []
    if not has_openai():
        return []
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return []

    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    prompt = textwrap.dedent(
        """
        You are helping identify the most useful single-cell data files for analysis.
        Rank the URLs below by likelihood of being an analysis-ready single-cell dataset
        (h5ad, loom, processed matrix) for the paper context. Provide a short reason.

        Paper context:
        {context}

        URLs:
        {urls}

        Return JSON list of objects with keys: url, reason.
        """
    ).format(context=context[:2000], urls="\n".join(candidates))

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
    )
    try:
        data = json.loads(response.output_text)
    except json.JSONDecodeError:
        return []
    results = []
    for item in data:
        if isinstance(item, dict) and "url" in item:
            results.append(LlmCandidate(url=item["url"], reason=item.get("reason", "")))
    return results


def normalize_accessions(accessions: dict[str, List[str]]) -> dict[str, List[str]]:
    normalized: dict[str, List[str]] = {}
    for key, values in accessions.items():
        normalized[key.upper()] = sorted(set(values))
    return normalized


def main() -> None:
    parser = argparse.ArgumentParser(description="Find datasets linked to a paper URL")
    parser.add_argument("--paper-url", required=True, help="PubMed/PMC/DOI URL")
    parser.add_argument(
        "--output-dir",
        default="outputs",
        help="Directory to write JSON reports",
    )
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    text = fetch_text(args.paper_url)
    accessions = extract_accessions(text)

    pmid = extract_pubmed_id(args.paper_url)
    notes: List[str] = []
    if pmid:
        notes.append(f"Detected PubMed ID {pmid}")
        try:
            xml_text = fetch_pubmed_xml(pmid)
            pubmed_accessions = parse_pubmed_accessions(xml_text)
            for key, values in pubmed_accessions.items():
                accessions.setdefault(key, []).extend(values)
        except requests.RequestException as exc:
            notes.append(f"Failed to fetch PubMed XML: {exc}")

    accessions = normalize_accessions(accessions)

    geo_supplementary: dict[str, List[str]] = {}
    geo_series = accessions.get("GEO_SERIES", [])
    for gse in geo_series:
        time.sleep(0.2)
        try:
            geo_supplementary[gse] = fetch_geo_supplementary(gse)
        except requests.RequestException as exc:
            notes.append(f"Failed to fetch GEO {gse}: {exc}")

    candidate_files = extract_candidate_files(text)
    for files in geo_supplementary.values():
        for file_url in files:
            if any(file_url.lower().endswith(ext) for ext in CANDIDATE_FILE_EXTENSIONS):
                candidate_files.append(file_url)

    candidate_files = sorted(set(candidate_files))

    llm_ranking: List[LlmCandidate] = rank_candidates_llm(candidate_files, text)

    report = Report(
        paper_url=args.paper_url,
        paper_text_excerpt=text[:2000],
        accessions=accessions,
        geo_supplementary_files=geo_supplementary,
        candidate_files=candidate_files,
        notes=notes,
        llm_ranking=[asdict(item) for item in llm_ranking],
    )

    base_name = pmid or "paper"
    output_path = os.path.join(args.output_dir, f"{base_name}-report.json")
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(asdict(report), handle, indent=2)

    print("Report written to", output_path)
    print("Accessions found:", json.dumps(accessions, indent=2))
    if candidate_files:
        print("Candidate files:")
        for url in candidate_files[:10]:
            print("  -", url)
    if llm_ranking:
        print("LLM ranking:")
        for item in llm_ranking:
            print("  -", item.url, "::", item.reason)


if __name__ == "__main__":
    main()
