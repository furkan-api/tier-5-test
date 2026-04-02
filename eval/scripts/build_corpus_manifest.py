#!/usr/bin/env python3
"""
Build corpus_manifest.json from the markdown files in corpus/.

Parses each file's header to extract: court, daire, law_branch, court_level,
esas_no, karar_no, decision_date, topic_keywords.

Two format families:
  - Modern/Lexpera: structured headers with "Esas No.:", "Karar No.:", "Karar tarihi:"
  - Old/Database: "İçtihat Metni", "MAHKEMESİ:", "TARİHİ:", "NUMARASI:"
"""

import json
import os
import re
import sys
from pathlib import Path

MARKDOWNS_DIR = Path(__file__).resolve().parent.parent.parent / "corpus"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "corpus_manifest.json"

# Files to exclude
EXCLUDED = {
    "vddk-e-2023-1401-k-2025-744-t-8-10-2025-1 (1)": "duplicate of vddk-e-2023-1401-k-2025-744-t-8-10-2025-1",
    "Unknown": "AI tool output, not original court decision",
}


def read_header(filepath: Path, max_lines: int = 40) -> str:
    """Read first N lines of a file, stripping form feeds."""
    try:
        text = filepath.read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError):
        return ""
    lines = text.replace("\x0c", "").split("\n")[:max_lines]
    return "\n".join(lines)


def extract_modern_format(header: str) -> dict:
    """Extract metadata from Modern/Lexpera format files."""
    result = {}

    # Esas No
    m = re.search(r"Esas\s+No\.?:\s*(\d{4}/\d+)", header)
    if m:
        result["esas_no"] = m.group(1)

    # Karar No
    m = re.search(r"Karar\s+No\.?:\s*(\d{4}/\d+)", header)
    if m:
        result["karar_no"] = m.group(1)

    # Karar tarihi
    m = re.search(r"Karar\s+tarihi:\s*(\d{2}\.\d{2}\.\d{4})", header)
    if m:
        result["decision_date"] = m.group(1)

    # Court and daire from line 2-3 (e.g., "T.C. Yargıtay Başkanlığı - 1. Hukuk Dairesi")
    m = re.search(r"T\.C\.\s+Yargıtay\s+Başkanlığı\s*-\s*(.+)", header)
    if m:
        daire_text = m.group(1).strip()
        result["court"] = "Yargıtay"
        result["daire"] = daire_text

    m = re.search(r"T\.C\.\s+Danıştay\s+Başkanlığı\s*-\s*(.+)", header)
    if m:
        daire_text = m.group(1).strip()
        result["court"] = "Danıştay"
        result["daire"] = daire_text

    # BAM format: "Bursa BAM - 4. Ceza Dairesi" or "İstanbul BAM - 3. Hukuk Dairesi"
    m = re.search(r"(\S+(?:\s+\S+)?)\s+BAM\s*-\s*(.+)", header)
    if m and "court" not in result:
        result["court"] = "BAM"
        result["daire"] = f"{m.group(1)} BAM {m.group(2).strip()}"

    # BİM format: "İstanbul BİM - 6. Vergi Dava Dairesi"
    m = re.search(r"(\S+)\s+BİM\s*-\s*(.+)", header)
    if m and "court" not in result:
        result["court"] = "BİM"
        result["daire"] = f"{m.group(1)} BİM {m.group(2).strip()}"

    # AYM format: "T.C. Anayasa Mahkemesi" or mojibake "ANAYASA MAHKEMESĠ"
    if ("Anayasa Mahkemesi" in header or "ANAYASA MAHKEMESĠ" in header) and "court" not in result:
        result["court"] = "AYM"
        m = re.search(r"Anayasa Mahkemesi\s*-\s*(.+)", header)
        if m:
            result["daire"] = m.group(1).strip()
        else:
            result["daire"] = "Anayasa Mahkemesi"
        # AYM bireysel başvuru number from mojibake file
        m = re.search(r"Ba[ĢşŞ]vuru\s+Numarası:\s*(\d{4}/\d+)", header)
        if m and "esas_no" not in result:
            result["esas_no"] = m.group(1)

    # Multi-line T.C. YARGITAY format: "T.C.\n\nYARGITAY\n\n22. HUKUK DAİRESİ"
    if "court" not in result:
        m = re.search(r"YARGITAY\s+(\d+)\.\s+(HUKUK DAİRESİ|CEZA DAİRESİ)", header)
        if m:
            result["court"] = "Yargıtay"
            daire_name = "Hukuk Dairesi" if "HUKUK" in m.group(2) else "Ceza Dairesi"
            result["daire"] = f"{m.group(1)}. {daire_name}"
        # Also catch E. and K. in the multi-line format
        m = re.search(r"E\.\s+(\d{4}/\d+)", header)
        if m and "esas_no" not in result:
            result["esas_no"] = m.group(1)
        m = re.search(r"K\.\s+(\d{4}/\d+)", header)
        if m and "karar_no" not in result:
            result["karar_no"] = m.group(1)

    # İlk Derece courts from header text
    # e.g., "Ankara 24. İdare Mahkemesi", "Ankara 3. İş Mahkemesi",
    # "Bakırköy 2. Fikrî Ve Sınai Haklar Ceza Mahkemesi"
    if "court" not in result:
        m = re.search(
            r"(\w+(?:\s+\w+)?)\s+(\d+)\.\s+"
            r"(İdare Mahkemesi|Asliye Ticaret Mahkemesi|Asliye Hukuk Mahkemesi|"
            r"Aile Mahkemesi|İcra Hukuk Mahkemesi|Tüketici Mahkemesi|"
            r"Fikr[iî]\s+[Vv]e\s+Sın(?:ai|aî)\s+Haklar\s+Ceza\s+Mahkemesi|"
            r"Ağır Ceza Mahkemesi|Asliye Ceza Mahkemesi|"
            r"İş Mahkemesi|ACM)",
            header,
        )
        if m:
            result["court"] = "İlk Derece"
            result["daire"] = f"{m.group(1)} {m.group(2)}. {m.group(3)}"

    # Topic keywords from bullet-separated line (Lexpera format)
    # Pattern: " keyword1 • keyword2 • keyword3"
    m = re.search(r"[^\n]*?([\wçğıöşüÇĞİÖŞÜ\s]+(?:\s+•\s+[\wçğıöşüÇĞİÖŞÜ\s]+)+)", header)
    if m:
        kw_line = m.group(1).strip()
        keywords = [k.strip() for k in kw_line.split("•") if k.strip()]
        result["topic_keywords"] = keywords

    return result


def extract_old_format(header: str) -> dict:
    """Extract metadata from old database format files."""
    result = {}

    # Court/daire from first line: "2. Hukuk Dairesi 2014/8960 E. , 2014/20128 K."
    m = re.search(r"(\d+)\.\s+(Hukuk Dairesi|Ceza Dairesi|HUKUK DAİRESİ|CEZA DAİRESİ)", header)
    if m:
        result["court"] = "Yargıtay"
        daire_name = m.group(2)
        if "HUKUK" in daire_name.upper():
            daire_name = "Hukuk Dairesi"
        elif "CEZA" in daire_name.upper():
            daire_name = "Ceza Dairesi"
        result["daire"] = f"{m.group(1)}. {daire_name}"

    # HGK from header
    if "Hukuk Genel Kurulu" in header:
        result["court"] = "Yargıtay"
        result["daire"] = "Hukuk Genel Kurulu"

    # Danıştay from header: "Danıştay 9. Daire Başkanlığı"
    m = re.search(r"Danıştay\s+(\d+)\.\s+Daire\s+Başkanlığı", header)
    if m and "court" not in result:
        result["court"] = "Danıştay"
        result["daire"] = f"{m.group(1)}. Daire"

    # Esas/Karar: handle E. E: and E_ separators, also "Esas" keyword
    esas_m = re.search(r"(\d{4}[/_:]\d+)\s*E\b", header) or re.search(r"E[.:]\s*(\d{4}[/_]\d+)", header)
    karar_m = re.search(r"(\d{4}[/_:]\d+)\s*K\b", header) or re.search(r"K[.:]\s*(\d{4}[/_]\d+)", header)
    if esas_m:
        result["esas_no"] = esas_m.group(1).replace("_", "/").replace(":", "/")
    if karar_m:
        result["karar_no"] = karar_m.group(1).replace("_", "/").replace(":", "/")

    # Date from TARİHİ field
    m = re.search(r"TARİHİ\s*:\s*(\d{1,2}[./]\d{1,2}[./]\d{4})", header)
    if m:
        date_str = m.group(1).replace("/", ".")
        parts = date_str.split(".")
        result["decision_date"] = f"{parts[0].zfill(2)}.{parts[1].zfill(2)}.{parts[2]}"

    # Date from "T. DD.MM.YYYY" pattern in header
    if "decision_date" not in result:
        m = re.search(r"T\.\s+(\d{1,2}\.\d{1,2}\.\d{4})", header)
        if m:
            parts = m.group(1).split(".")
            result["decision_date"] = f"{parts[0].zfill(2)}.{parts[1].zfill(2)}.{parts[2]}"

    # Esas/Karar from NUMARASI field: "Esas no:2011/676 Karar no:2014/17"
    m = re.search(r"Esas\s+no\s*:\s*(\d{4}/\d+)", header, re.IGNORECASE)
    if m and "esas_no" not in result:
        result["esas_no"] = m.group(1)
    m = re.search(r"Karar\s+no\s*:\s*(\d{4}/\d+)", header, re.IGNORECASE)
    if m and "karar_no" not in result:
        result["karar_no"] = m.group(1)

    return result


def infer_from_first_line(first_line: str) -> dict:
    """Try to infer court info from the very first line (works for both formats)."""
    result = {}
    fl = first_line.strip()

    # "Yargıtay 1. HD., E. 2024/1977 K. 2025/5810 T. 9.12.2025"
    m = re.match(
        r"Yargıtay\s+(\d+)\.\s+(HD|CD|HGK|CGK)\.",
        fl,
    )
    if m:
        daire_map = {"HD": "Hukuk Dairesi", "CD": "Ceza Dairesi", "HGK": "Hukuk Genel Kurulu", "CGK": "Ceza Genel Kurulu"}
        result["court"] = "Yargıtay"
        num = m.group(1)
        abbr = m.group(2)
        result["daire"] = f"{num}. {daire_map.get(abbr, abbr)}"
        if abbr in ("HGK", "CGK"):
            result["daire"] = daire_map[abbr]

    # "Yargıtay HGK., E. ..."
    m = re.match(r"Yargıtay\s+(HGK|CGK|YİBBGK)\.", fl)
    if m and "court" not in result:
        result["court"] = "Yargıtay"
        name_map = {"HGK": "Hukuk Genel Kurulu", "CGK": "Ceza Genel Kurulu", "YİBBGK": "İçtihatları Birleştirme BGK"}
        result["daire"] = name_map.get(m.group(1), m.group(1))

    # "Danıştay 8. D., E. ..."
    m = re.match(r"Danıştay\s+(\d+)\.\s+D\.", fl)
    if m and "court" not in result:
        result["court"] = "Danıştay"
        result["daire"] = f"{m.group(1)}. Daire"

    # "Danıştay IBK., E. ..."
    if re.match(r"Danıştay\s+IBK\.", fl) and "court" not in result:
        result["court"] = "Danıştay"
        result["daire"] = "İçtihatları Birleştirme Kurulu"

    # "Danıştay İDDK., E. ..."
    m = re.match(r"Danıştay\s+(İDDK|VDDK)\.", fl)
    if m and "court" not in result:
        result["court"] = "Danıştay"
        name_map = {"İDDK": "İdari Dava Daireleri Kurulu", "VDDK": "Vergi Dava Daireleri Kurulu"}
        result["daire"] = name_map.get(m.group(1), m.group(1))

    # "Anayasa Mahkemesi 2. B., B. 2016/13036 ..."
    if "Anayasa Mahkemesi" in fl and "court" not in result:
        result["court"] = "AYM"
        result["daire"] = "Anayasa Mahkemesi"

    # "Ankara 24. İdare Mahkemesi, E. ..." or "Ankara 3. İş Mahkemesi, E. ..."
    m = re.match(r"(\w+)\s+(\d+)\.\s+(İdare Mahkemesi|İş Mahkemesi|Asliye Ticaret Mahkemesi)", fl)
    if m and "court" not in result:
        result["court"] = "İlk Derece"
        result["daire"] = f"{m.group(1)} {m.group(2)}. {m.group(3)}"

    # "Bakırköy 2. Fikrî Ve Sınai Haklar Ceza Mahkemesi, E. ..."
    m = re.search(r"(\w+)\s+(\d+)\.\s+(Fikr[iî]\s+[Vv]e\s+Sın(?:ai|aî)\s+Haklar\s+Ceza\s+Mahkemesi)", fl)
    if m and "court" not in result:
        result["court"] = "İlk Derece"
        result["daire"] = f"{m.group(1)} {m.group(2)}. {m.group(3)}"

    # "Danıştay 9. Daire Başkanlığı" (old format first line)
    m = re.search(r"Danıştay\s+(\d+)\.\s+Daire\s+Başkanlığı", fl)
    if m and "court" not in result:
        result["court"] = "Danıştay"
        result["daire"] = f"{m.group(1)}. Daire"

    # "Yargıtay 22. HD E: 2013/9431 K: 2013/16464" (colon separator)
    m = re.match(r"Yargıtay\s+(\d+)\.\s+(HD|CD)\s+E:", fl)
    if m and "court" not in result:
        daire_map = {"HD": "Hukuk Dairesi", "CD": "Ceza Dairesi"}
        result["court"] = "Yargıtay"
        result["daire"] = f"{m.group(1)}. {daire_map[m.group(2)]}"

    # "Yargıtay 2. Hukuk Dairesi 2023/7132 Esas ..." (full daire name in first line)
    m = re.match(r"Yargıtay\s+(\d+)\.\s+(Hukuk Dairesi|Ceza Dairesi)", fl)
    if m and "court" not in result:
        result["court"] = "Yargıtay"
        result["daire"] = f"{m.group(1)}. {m.group(2)}"

    # BAM first line: "Bursa BAM, 4. CD., E. ..."
    m = re.match(r"(\w+)\s+BAM,?\s+(\d+)\.\s+(CD|HD)\.", fl)
    if m and "court" not in result:
        result["court"] = "BAM"
        type_map = {"CD": "Ceza Dairesi", "HD": "Hukuk Dairesi"}
        result["daire"] = f"{m.group(1)} BAM {m.group(2)}. {type_map.get(m.group(3), m.group(3))}"

    # BİM first line: "İstanbul BİM, 6. VDD, E. ..."
    m = re.match(r"(\w+)\s+BİM,?\s+(\d+)\.\s+VDD", fl)
    if m and "court" not in result:
        result["court"] = "BİM"
        result["daire"] = f"{m.group(1)} BİM {m.group(2)}. Vergi Dava Dairesi"

    # Date from "T. DD.MM.YYYY" in first line
    m = re.search(r"T\.\s+(\d{1,2}\.\d{1,2}\.\d{4})", fl)
    if m:
        parts = m.group(1).split(".")
        result["decision_date"] = f"{parts[0].zfill(2)}.{parts[1].zfill(2)}.{parts[2]}"

    # Esas/Karar from first line: "E. 2024/1977", "E: 2013/9431", "2023/7132 Esas"
    m = re.search(r"E[.:]\s*(\d{4}/\d+)", fl)
    if m:
        result["esas_no"] = m.group(1)
    if "esas_no" not in result:
        m = re.search(r"(\d{4}/\d+)\s+Esas", fl)
        if m:
            result["esas_no"] = m.group(1)

    m = re.search(r"K[.:]\s*(\d{4}/\d+)", fl)
    if m:
        result["karar_no"] = m.group(1)
    if "karar_no" not in result:
        m = re.search(r"(\d{4}/\d+)\s+Karar", fl)
        if m:
            result["karar_no"] = m.group(1)

    # Also handle "B. 2016/13036" for AYM bireysel başvuru
    m = re.search(r"B\.\s+(\d{4}/\d+)", fl)
    if m and "esas_no" not in result:
        result["esas_no"] = m.group(1)

    return result


def infer_law_branch(court: str, daire: str) -> str:
    """Infer law_branch from court and daire."""
    if court == "AYM":
        return "anayasa"
    if court in ("Danıştay", "BİM"):
        return "idari"
    if court == "İlk Derece":
        if "İdare" in daire or "Vergi" in daire:
            return "idari"
        if "Ceza" in daire or "Ağır Ceza" in daire:
            return "ceza"
        return "hukuk"
    if court == "BAM":
        if "Ceza" in daire:
            return "ceza"
        return "hukuk"
    # Yargıtay
    if "Ceza" in daire or "CGK" in daire:
        return "ceza"
    return "hukuk"


def infer_court_level(court: str, daire: str) -> int:
    """Infer court_level: 1=İlk Derece, 2=BAM/BİM, 3=Daire, 4=Kurul/İBK."""
    if court == "İlk Derece":
        return 1
    if court in ("BAM", "BİM"):
        return 2
    if court == "AYM":
        return 4
    # Yargıtay
    if court == "Yargıtay":
        if any(
            k in daire
            for k in ("Genel Kurul", "İçtihatları Birleştirme", "İBK", "BGK")
        ):
            return 4
        return 3  # Daire
    # Danıştay
    if court == "Danıştay":
        if any(
            k in daire
            for k in ("Daireleri Kurulu", "İçtihatları Birleştirme", "İBK")
        ):
            return 4
        return 3  # Daire
    return 0


def infer_from_filename(filename: str) -> dict:
    """Fallback: extract court/daire/esas/karar/date from filename slug patterns."""
    stem = Path(filename).stem

    # Each entry: (regex, court, daire_template)
    # daire_template uses \1, \2 etc. for group references, applied after match.
    # All patterns share the same trailing structure: e-YYYY-N-k-YYYY-N-t-D-M-YYYY
    PATTERNS = [
        # Yargıtay daireleri
        (r"(\d+)-hukuk-dairesi-e-",      "Yargıtay",  "{0}. Hukuk Dairesi"),
        (r"(\d+)-ceza-dairesi-e-",        "Yargıtay",  "{0}. Ceza Dairesi"),
        # Yargıtay kurulları
        (r"hukuk-genel-kurulu-e-",        "Yargıtay",  "Hukuk Genel Kurulu"),
        (r"ictihatlari-birlestirme-hgk-e-", "Yargıtay", "İçtihatları Birleştirme HGK"),
        # Danıştay
        (r"(\d+)-d-e-",                   "Danıştay",  "{0}. Daire"),
        (r"(vddk)-e-",                    "Danıştay",  "Vergi Dava Daireleri Kurulu"),
        (r"(iddk)-e-",                    "Danıştay",  "İdari Dava Daireleri Kurulu"),
        (r"ibk-e-",                       "Danıştay",  "İçtihatları Birleştirme Kurulu"),
        # BAM: "bursa-bam4-cd-e-..."
        (r"(\w+)-bam(\d+)-(cd|hd)-e-",   "BAM",       None),  # special handling
        # BİM: "istanbul-bim6-vdd-e-..."
        (r"(\w+)-bim(\d+)-vdd-e-",       "BİM",       None),  # special handling
        # İlk Derece: "ankara-24-idare-mahkemesi-e-..."
        (r"(\w+)-(\d+)-idare-mahkemesi-e-", "İlk Derece", "{0} {1}. İdare Mahkemesi"),
        # İlk Derece generic: "bakirkoy-3-icra-hukuk-mahkemesi-e-..."
        (r"(\w+)-(\d+)-[\w-]+-mahkemesi-e-", "İlk Derece", None),
        # İlk Derece abbreviated: "ankr-5im-e-..."
        (r"[\w]+-(?:\d+[\w]*|[\w]+-[\w]+)-e-", "İlk Derece", None),
        # İstanbul Anadolu: "istanbul-anadolu-4-asliye-ticaret-..."
        (r"istanbul-anadolu-(\d+)-[\w-]+-e-", "İlk Derece", None),
    ]

    EKT = r"(\d+)-(\d+)-k-(\d+)-(\d+)-t-(\d+)-(\d+)-(\d+)"

    for prefix_re, court, daire_tmpl in PATTERNS:
        m = re.match(prefix_re + EKT, stem)
        if not m:
            continue

        groups = m.groups()
        # The last 7 groups are always: esas_y, esas_n, karar_y, karar_n, day, month, year
        esas_y, esas_n, karar_y, karar_n, day, month, year = groups[-7:]

        result = {
            "court": court,
            "esas_no": f"{esas_y}/{esas_n}",
            "karar_no": f"{karar_y}/{karar_n}",
            "decision_date": f"{day.zfill(2)}.{month.zfill(2)}.{year}",
        }

        # Build daire from template and prefix capture groups
        prefix_groups = groups[:-7]
        if daire_tmpl:
            result["daire"] = daire_tmpl.format(*(g.capitalize() if i == 0 and court == "İlk Derece" else g for i, g in enumerate(prefix_groups)))
        elif court == "BAM" and len(prefix_groups) == 3:
            type_map = {"cd": "Ceza Dairesi", "hd": "Hukuk Dairesi"}
            result["daire"] = f"{prefix_groups[0].capitalize()} BAM {prefix_groups[1]}. {type_map[prefix_groups[2]]}"
        elif court == "BİM" and len(prefix_groups) == 2:
            result["daire"] = f"{prefix_groups[0].capitalize()} BİM {prefix_groups[1]}. Vergi Dava Dairesi"

        return result

    return {}


def parse_file(filepath: Path) -> dict:
    """Parse a single markdown file and return metadata."""
    stem = filepath.stem
    filename = filepath.name

    doc = {
        "doc_id": stem,
        "filename": filename,
        "court": "",
        "daire": "",
        "law_branch": "",
        "court_level": 0,
        "esas_no": "",
        "karar_no": "",
        "decision_date": "",
        "topic_keywords": [],
        "excluded": stem in EXCLUDED,
        "exclude_reason": EXCLUDED.get(stem, ""),
    }

    if doc["excluded"]:
        return doc

    header = read_header(filepath)
    if not header.strip():
        doc["excluded"] = True
        doc["exclude_reason"] = "empty file"
        return doc

    lines = header.split("\n")
    first_line = lines[0].strip() if lines else ""

    # Layer 1: First line parsing (works for both formats)
    first_line_data = infer_from_first_line(first_line)

    # Layer 2: Modern format parsing (Esas No.:, Karar No.:, etc.)
    modern_data = extract_modern_format(header)

    # Layer 3: Old format parsing (İçtihat Metni, MAHKEMESİ, etc.)
    old_data = {}
    if "İçtihat Metni" in header or "MAHKEMESİ" in header[:500]:
        old_data = extract_old_format(header)

    # Layer 4: Filename fallback
    filename_data = infer_from_filename(filename)

    # Merge layers: first_line > modern > old > filename (priority order)
    for field in ["court", "daire", "esas_no", "karar_no", "decision_date"]:
        for source in [first_line_data, modern_data, old_data, filename_data]:
            if source.get(field) and not doc[field]:
                doc[field] = source[field]

    # Last resort for decision_date: try to find "T. DD.MM.YYYY" anywhere in filename
    if not doc["decision_date"]:
        m = re.search(r"T\.\s+(\d{1,2}\.\d{1,2}\.\d{4})", stem.replace("_", "/"))
        if m:
            parts = m.group(1).split(".")
            doc["decision_date"] = f"{parts[0].zfill(2)}.{parts[1].zfill(2)}.{parts[2]}"

    # Topic keywords only from modern format
    if modern_data.get("topic_keywords"):
        doc["topic_keywords"] = modern_data["topic_keywords"]

    # Infer law_branch and court_level
    if doc["court"]:
        doc["law_branch"] = infer_law_branch(doc["court"], doc["daire"])
        doc["court_level"] = infer_court_level(doc["court"], doc["daire"])

    return doc


def main():
    if not MARKDOWNS_DIR.exists():
        print(f"Error: {MARKDOWNS_DIR} not found")
        sys.exit(1)

    md_files = sorted(MARKDOWNS_DIR.glob("*.md"))
    print(f"Found {len(md_files)} markdown files")

    manifest = []
    missing_fields = []

    for filepath in md_files:
        doc = parse_file(filepath)
        manifest.append(doc)

        # Track quality
        if not doc["excluded"]:
            missing = []
            for field in ["court", "daire", "esas_no", "karar_no", "decision_date"]:
                if not doc.get(field):
                    missing.append(field)
            if missing:
                missing_fields.append((doc["doc_id"], missing))

    # Write output
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # Summary
    total = len(manifest)
    excluded = sum(1 for d in manifest if d["excluded"])
    usable = total - excluded

    print(f"\nManifest written to {OUTPUT_PATH}")
    print(f"Total: {total}, Usable: {usable}, Excluded: {excluded}")

    # Court distribution
    courts = {}
    for d in manifest:
        if not d["excluded"] and d["court"]:
            courts[d["court"]] = courts.get(d["court"], 0) + 1
    print("\nCourt distribution:")
    for court, count in sorted(courts.items(), key=lambda x: -x[1]):
        print(f"  {court}: {count}")

    # Branch distribution
    branches = {}
    for d in manifest:
        if not d["excluded"] and d["law_branch"]:
            branches[d["law_branch"]] = branches.get(d["law_branch"], 0) + 1
    print("\nBranch distribution:")
    for branch, count in sorted(branches.items(), key=lambda x: -x[1]):
        print(f"  {branch}: {count}")

    # Level distribution
    levels = {}
    for d in manifest:
        if not d["excluded"] and d["court_level"]:
            levels[d["court_level"]] = levels.get(d["court_level"], 0) + 1
    print("\nCourt level distribution:")
    for level, count in sorted(levels.items()):
        print(f"  Level {level}: {count}")

    # Missing fields
    if missing_fields:
        print(f"\n⚠ {len(missing_fields)} files with missing fields:")
        for doc_id, fields in missing_fields:
            print(f"  {doc_id}: missing {', '.join(fields)}")


if __name__ == "__main__":
    main()
