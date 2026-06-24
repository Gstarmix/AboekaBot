import os
import re
import glob
import json
import time
import shutil
import asyncio
import logging
import subprocess
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, List, Dict
import hashlib
import anthropic
import discord
from discord.ext import commands, tasks
import webhook_log
ISTIC_GUILD_ID = 1466806132998672466
COURS_ROOT = r"C:\Users\Gstar\OneDrive\Documents\COURS"
AUDIO_ROOT = r"C:\Users\Gstar\Music\Enregistrement"
COURS_TEMP = os.path.join(COURS_ROOT, "_temp_latex")
PUBLISHED_JSON = os.path.join(COURS_ROOT, "_published.json")
ABSENCES_JSON = os.path.join(COURS_ROOT, "_absences.json")
TITRES_THREADS_YAML = os.path.join(COURS_ROOT, "_titres_threads.yaml")
BOTGSTAR_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DISCORD_PUBLISHED_JSON = os.path.join(BOTGSTAR_ROOT, "datas", "discord_published.json")
DISCORD_PERSO_PUBLISHED_JSON = os.path.join(
    BOTGSTAR_ROOT, "datas", "discord_perso_published.json"
)
CORRECTION_FORUM_PREFIX = "corrections"
PERSO_CATEGORY_NAME = "🔒 PERSONNEL"
PERSO_FORUM_PREFIX = "perso-"
PERSO_BACKFILL_SLEEP_SECONDS = 15
HORS_SUJETS_FORUM_NAME = "hors-sujets"
INBOX_FORUM_PREFIX = "inbox-"
def inbox_forum_name(matiere: str) -> str:
    return f"{INBOX_FORUM_PREFIX}{matiere.lower()}"
PUBLISH_QUEUE_DIR = os.path.join(COURS_ROOT, "_publish_queue")
PUBLISH_QUEUE_DONE = os.path.join(PUBLISH_QUEUE_DIR, "_done")
def perso_forum_name(matiere: str) -> str:
    return f"{PERSO_FORUM_PREFIX}{matiere.lower()}"
def correction_forum_name(matiere: str) -> str:
    return f"{CORRECTION_FORUM_PREFIX}-{matiere.lower()}"
LOG_COLOR_INFO    = 0x3498DB
LOG_COLOR_OK      = 0x2ECC71
LOG_COLOR_WARN    = 0xF39C12
LOG_COLOR_ERROR   = 0xE74C3C
LOG_COLOR_DEFAULT = 0x607D8B
PDFLATEX = r"C:\Users\Gstar\AppData\Local\Programs\MiKTeX\miktex\bin\x64\pdflatex.exe"
LATEX_TIMEOUT = 120
TYPE_MAP = {"cm": "CM", "td": "TD", "tp": "TP"}
MATIERE_MAP = {
    "an1": "AN1",
    "en1": "EN1",
    "prg2": "PRG2",
    "psi": "PSI",
    "ise": "ISE",
}
CHANNEL_SUFFIXES = {
    "audio": "audio",
    "transcription": "transcription",
    "resume": "résumé",
}
DISCORD_FILE_LIMIT = 25 * 1024 * 1024
API_KEY = os.getenv("ANTHROPIC_API_KEY")
SUMMARY_MODEL = "claude-sonnet-4-20250514"
SUMMARY_MAX_TOKENS = 8192
COST_INPUT_PER_1M = 3.0
COST_OUTPUT_PER_1M = 15.0
USD_TO_EUR = 0.92
LOG_CHANNEL_ID = int(os.environ.get("LOG_COURS_CHANNEL_ID", "0") or 0) or 1518182713117380739
LOG_WEBHOOK = os.environ.get("LOG_WEBHOOK_COURS")
if LOG_CHANNEL_ID == 1493760267300110466:
    LOG_CHANNEL_ID = 1518182713117380739
ADMIN_ROLE_ID = 1493905604241129592
WATCHER_CORRECTIONS_INTERVAL_SECONDS = 60
WATCHER_DAILY_RECAP_HOUR_UTC = 22
WATCHER_DAILY_RECAP_MINUTE = 59
EMBED_COLORS = {
    "AN1":  0x003366,
    "EN1":  0x1A1A6E,
    "PRG2": 0x1B5E20,
    "PSI":  0x4A148C,
    "ISE":  0xB71C1C,
}
CORRECTION_EMBED_COLORS = {
    "AN1":  0x003366,
    "EN1":  0x1A1A6E,
    "PRG2": 0x1B5E20,
    "PSI":  0x4A148C,
    "ISE":  0x607D8B,
}
log = logging.getLogger("cours_pipeline")
LATEX_PREAMBLE = r"""\documentclass[a4paper,11pt]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage[french]{babel}
\usepackage{geometry}
\geometry{hmargin=2.5cm,vmargin=2.5cm}
\usepackage{amsmath, amssymb, mathrsfs, stmaryrd}
\usepackage{siunitx}
\usepackage{minted}
\usemintedstyle{friendly}
\setminted{breaklines=true, fontsize=\small, frame=lines}
\usepackage{tikz}
\usepackage{circuitikz}
\usetikzlibrary{arrows, shapes, positioning, calc, automata, shadows}
\usepackage[most]{tcolorbox}
\usepackage{hyperref}
\hypersetup{colorlinks=true, linkcolor=blue}
\newtcolorbox{examalert}{colback=red!5!white, colframe=red!75!black, title=\textbf{$\triangle$ INFO EXAMEN}, fonttitle=\bfseries, drop shadow}
\newtcolorbox{concept}[1][]{colback=blue!5!white, colframe=blue!75!black, title=\textbf{#1}, fonttitle=\bfseries}
\newtcolorbox{qrbox}[1][]{colback=gray!15!white, colframe=gray!60!black, title=\textbf{Q : #1}, fonttitle=\bfseries\color{black}, attach title to upper, after title={\par\vspace{2mm}}, coltitle=black}
"""
def build_audio_path(type_code: str, matiere: str, num: str, date: str) -> Optional[str]:
    candidates = [
        f"{type_code}{num}_{matiere}_{date}.m4a",
        f"{type_code}{num} {matiere} {date}.m4a",
    ]
    for filename in candidates:
        path = os.path.join(AUDIO_ROOT, filename)
        if os.path.isfile(path):
            return path
    return None
def find_transcription(type_code: str, matiere: str, num: str, date: str) -> Optional[str]:
    base_name = f"{type_code}{num}_{matiere}_{date}"
    type_dir = os.path.join(COURS_ROOT, matiere, type_code)
    if os.path.isdir(type_dir):
        for root, _, files in os.walk(type_dir):
            for f in files:
                if not f.endswith(".txt"):
                    continue
                f_lower = f.lower()
                if base_name.lower() in f_lower:
                    return os.path.join(root, f)
                if "transcriptions" in root.lower():
                    if date in f and matiere.lower() in f_lower:
                        return os.path.join(root, f)
    inbox = os.path.join(COURS_ROOT, "_INBOX")
    if os.path.isdir(inbox):
        for f in os.listdir(inbox):
            if f.endswith(".txt") and base_name.lower() in f.lower():
                return os.path.join(inbox, f)
    return None
def format_date(date_str: str) -> str:
    if len(date_str) == 4:
        day = date_str[:2]
        month = date_str[2:]
        return f"{day}/{month}/2026"
    return date_str
def _session_key(type_code: str, matiere: str, num: str, date: str) -> str:
    return f"{type_code}{num}_{matiere}_{date}"
def load_published() -> dict:
    if not os.path.isfile(PUBLISHED_JSON):
        return {}
    try:
        with open(PUBLISHED_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}
def save_published(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(PUBLISHED_JSON), exist_ok=True)
        with open(PUBLISHED_JSON, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
    except OSError as e:
        log.warning(f"Impossible d'écrire {PUBLISHED_JSON}: {e}")
def mark_published(key: str, step: str) -> None:
    data = load_published()
    entry = data.get(key, {})
    entry[step] = True
    entry["timestamp"] = datetime.utcnow().isoformat(timespec="seconds")
    data[key] = entry
    save_published(data)
def pdf_rel_key(pdf_path: str) -> str:
    try:
        rel = os.path.relpath(pdf_path, COURS_ROOT)
    except ValueError:
        rel = pdf_path
    return rel.replace("\\", "/")
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
def thread_key(matiere: str, type_code: str, num: str,
               annee: Optional[str] = None) -> str:
    m = matiere.upper()
    t = type_code.lower() if type_code.lower() == "quiz" else type_code.upper()
    if annee:
        return f"{m}__{t}__{num}__{annee}"
    return f"{m}__{t}__{num}"
def load_discord_published_v2() -> dict:
    empty = {"schema_version": 2, "threads": {}}
    if not os.path.isfile(DISCORD_PUBLISHED_JSON):
        return empty
    try:
        with open(DISCORD_PUBLISHED_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return empty
    if not isinstance(data, dict):
        return empty
    if not data:
        return empty
    if data.get("schema_version") != 2:
        log.warning(
            "discord_published.json : schéma non v2 détecté. "
            "Traité comme vide. Purge manuelle recommandée si non voulu."
        )
        return empty
    if "threads" not in data or not isinstance(data["threads"], dict):
        data["threads"] = {}
    return data
def save_discord_published_v2(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(DISCORD_PUBLISHED_JSON), exist_ok=True)
        tmp = DISCORD_PUBLISHED_JSON + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, DISCORD_PUBLISHED_JSON)
    except OSError as e:
        log.warning(f"Impossible d'écrire {DISCORD_PUBLISHED_JSON}: {e}")
def load_discord_perso_published() -> dict:
    empty = {"schema_version": 1, "threads": {}}
    if not os.path.isfile(DISCORD_PERSO_PUBLISHED_JSON):
        return empty
    try:
        with open(DISCORD_PERSO_PUBLISHED_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return empty
    if not isinstance(data, dict) or not data:
        return empty
    if data.get("schema_version") != 1:
        log.warning(
            "discord_perso_published.json : schéma non v1, traité comme vide."
        )
        return empty
    if "threads" not in data or not isinstance(data["threads"], dict):
        data["threads"] = {}
    return data
def save_discord_perso_published(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(DISCORD_PERSO_PUBLISHED_JSON), exist_ok=True)
        tmp = DISCORD_PERSO_PUBLISHED_JSON + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, DISCORD_PERSO_PUBLISHED_JSON)
    except OSError as e:
        log.warning(f"Impossible d'écrire {DISCORD_PERSO_PUBLISHED_JSON}: {e}")
def load_titres_threads() -> dict:
    if not os.path.isfile(TITRES_THREADS_YAML):
        return {}
    try:
        import yaml as _yaml
        with open(TITRES_THREADS_YAML, "r", encoding="utf-8") as f:
            data = _yaml.safe_load(f)
        if not isinstance(data, dict):
            return {}
        return {
            str(k): str(v).strip()
            for k, v in data.items()
            if isinstance(v, str) and v.strip() and v.strip() != "???"
        }
    except Exception as e:
        log.warning(f"load_titres_threads : échec lecture {TITRES_THREADS_YAML}: {e}")
        return {}
_SUPER_MAP = {"0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
              "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
              "+": "⁺", "-": "⁻", "(": "⁽", ")": "⁾"}
_SUB_MAP = {"0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄",
            "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉"}
_LATEX_REPLACEMENTS = {
    r"\\displaystyle\s*": "", r"\\left": "", r"\\right": "",
    r"\\,": " ", r"\\!": "", r"\\;": " ", r"\\quad\s*": " ", r"\\qquad\s*": " ",
    r"\\infty": "∞",
    r"\\to\b": "→", r"\\rightarrow\b": "→", r"\\longrightarrow\b": "→",
    r"\\implies\b": "⇒", r"\\iff\b": "⇔",
    r"\\leq\b": "≤", r"\\geq\b": "≥", r"\\le\b": "≤", r"\\ge\b": "≥",
    r"\\neq\b": "≠", r"\\times\b": "×", r"\\cdot\b": "·",
    r"\\pm\b": "±", r"\\forall\b": "∀", r"\\exists\b": "∃",
    r"\\in\b": "∈", r"\\notin\b": "∉",
    r"\\cup\b": "∪", r"\\cap\b": "∩", r"\\emptyset\b": "∅",
    r"\\sum\b": "Σ", r"\\prod\b": "∏", r"\\int\b": "∫",
    r"\\alpha\b": "α", r"\\beta\b": "β", r"\\gamma\b": "γ", r"\\delta\b": "δ",
    r"\\epsilon\b": "ε", r"\\varepsilon\b": "ε",
    r"\\theta\b": "θ", r"\\lambda\b": "λ", r"\\mu\b": "μ", r"\\pi\b": "π",
    r"\\sigma\b": "σ", r"\\phi\b": "φ", r"\\varphi\b": "φ", r"\\omega\b": "ω",
    r"\\Gamma\b": "Γ", r"\\Delta\b": "Δ", r"\\Omega\b": "Ω",
    r"\\mathbb\s*\{R\}": "ℝ", r"\\mathbb\s*\{N\}": "ℕ",
    r"\\mathbb\s*\{Z\}": "ℤ", r"\\mathbb\s*\{Q\}": "ℚ",
    r"\\mathbb\s*\{C\}": "ℂ",
    r"\\mathrm\s*\{([^{}]*)\}": r"\1",
    r"\\text\s*\{([^{}]*)\}": r"\1",
    r"\\mathbf\s*\{([^{}]*)\}": r"\1",
    r"\\operatorname\s*\{([^{}]*)\}": r"\1",
    r"\\sin\b": "sin", r"\\cos\b": "cos", r"\\tan\b": "tan",
    r"\\log\b": "log", r"\\ln\b": "ln", r"\\exp\b": "exp",
    r"\\max\b": "max", r"\\min\b": "min",
    r"\\sup\b": "sup", r"\\inf\b": "inf",
    r"\\searrow\b": "↘", r"\\nearrow\b": "↗",
}
def _convert_math(expr: str) -> str:
    s = expr
    def repl_frac(m: re.Match) -> str:
        a, b = m.group(1).strip(), m.group(2).strip()
        a_out = a if re.match(r"^[a-zA-Z0-9]+$", a) else f"({a})"
        b_out = b if re.match(r"^[a-zA-Z0-9]+$", b) else f"({b})"
        return f"{a_out}/{b_out}"
    for _ in range(3):
        s = re.sub(r"\\d?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", repl_frac, s)
    def repl_sqrt(m: re.Match) -> str:
        x = m.group(1).strip()
        return f"√{x}" if re.match(r"^[a-zA-Z0-9]+$", x) else f"√({x})"
    s = re.sub(r"\\sqrt\s*\{([^{}]*)\}", repl_sqrt, s)
    s = re.sub(r"\^\s*\{([^{}]*)\}", r"^(\1)", s)
    s = re.sub(r"\^([0-9+\-])",
               lambda m: _SUPER_MAP.get(m.group(1), "^" + m.group(1)), s)
    s = re.sub(r"_\s*\{([^{}]*)\}", r"_(\1)", s)
    s = re.sub(r"_([0-9])",
               lambda m: _SUB_MAP.get(m.group(1), "_" + m.group(1)), s)
    for pat, rep in _LATEX_REPLACEMENTS.items():
        s = re.sub(pat, rep, s)
    s = re.sub(r"\s+", " ", s).strip()
    return s
def latex_to_readable(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\$\$(.+?)\$\$", lambda m: _convert_math(m.group(1)), text,
                  flags=re.DOTALL)
    text = re.sub(r"\$([^$]+)\$", lambda m: _convert_math(m.group(1)), text)
    return text
def truncate_smart(text: str, limit: int = 400) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in (". ", "! ", "? ", "; ", ": ", " "):
        idx = cut.rfind(sep)
        if idx >= limit * 0.5:
            return cut[:idx + len(sep.rstrip())].rstrip() + "…"
    return cut.rstrip() + "…"
_YAML_FENCE_RE = re.compile(r"^```(?:yaml)?\s*\n(.*?)\n```", re.DOTALL | re.MULTILINE)
_YAML_DASH_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_SECTION_METHODE_RE = re.compile(
    r"^##\s+M[ée]thode(?:\s+du\s+professeur)?\s*\n(.*?)(?=^#{1,6}\s+|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
_SECTION_RETENIR_RE = re.compile(
    r"^##\s+[ÀA]\s+retenir\s*\n(.*?)(?=^#{1,6}\s+|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
_SECTION_INDIC_RE = re.compile(
    r"^##\s+Indicateur\s+de\s+source\s*\n(.*?)(?=^#{1,6}\s+|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
_SOURCE_LABEL_RE = re.compile(
    r"SOURCE\s*:\s*([A-Z_ÉÈÊÀÂÎÏÔÛŒ]+)", re.IGNORECASE,
)
def _extract_yaml_block(text: str) -> dict:
    try:
        import yaml as _yaml
    except ImportError:
        _yaml = None
    block_text = None
    m = _YAML_FENCE_RE.search(text)
    if m:
        block_text = m.group(1)
    else:
        m2 = _YAML_DASH_RE.search(text)
        if m2:
            block_text = m2.group(1)
    if not block_text:
        return {}
    if _yaml is None:
        out: dict = {}
        for line in block_text.splitlines():
            if ":" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition(":")
                out[k.strip()] = v.strip().strip('"').strip("'")
        return out
    try:
        data = _yaml.safe_load(block_text)
        return data if isinstance(data, dict) else {}
    except _yaml.YAMLError:
        return {}
def _clean_section_body(body: str) -> str:
    body = body.strip()
    if body.startswith("```") and body.rstrip().endswith("```"):
        body = re.sub(r"^```[^\n]*\n?", "", body)
        body = re.sub(r"\n?```\s*$", "", body)
    return body.strip()
def parse_tache_md(tache_path: str) -> dict:
    with open(tache_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    meta = _extract_yaml_block(text)
    matiere = str(meta.get("matiere") or "").upper() or None
    td_num = meta.get("td_numero") or meta.get("td_num") or meta.get("numero")
    try:
        td_numero = int(td_num) if td_num is not None else None
    except (TypeError, ValueError):
        td_numero = None
    exercice = meta.get("exercice")
    try:
        exercice_i = int(exercice) if exercice is not None else None
    except (TypeError, ValueError):
        exercice_i = None
    titre_td = (meta.get("titre_td") or meta.get("titre") or "").strip() or None
    source_trans = (meta.get("source_transcription") or "").strip() or None
    type_raw = str(meta.get("type") or "").lower()
    type_code: Optional[str] = None
    for t in ("td", "tp", "cc"):
        if t in type_raw:
            type_code = t.upper()
            break
    if type_code is None:
        fname = os.path.basename(tache_path).upper()
        for t in ("TD", "TP", "CC"):
            if f"_{t}" in fname:
                type_code = t
                break
    m_meth = _SECTION_METHODE_RE.search(text)
    methode_raw = _clean_section_body(m_meth.group(1)) if m_meth else None
    m_ret = _SECTION_RETENIR_RE.search(text)
    retenir_raw = _clean_section_body(m_ret.group(1)) if m_ret else None
    m_ind = _SECTION_INDIC_RE.search(text)
    indicateur = None
    if m_ind:
        body = _clean_section_body(m_ind.group(1))
        label = _SOURCE_LABEL_RE.search(body)
        if label:
            indicateur = label.group(1).strip().upper()
        else:
            for line in body.splitlines():
                if line.strip():
                    indicateur = line.strip()[:40]
                    break
    methode = (
        truncate_smart(latex_to_readable(methode_raw), 400)
        if methode_raw else None
    )
    a_retenir = (
        truncate_smart(latex_to_readable(retenir_raw), 400)
        if retenir_raw else None
    )
    return {
        "matiere": matiere,
        "type": type_code,
        "td_numero": td_numero,
        "exercice": exercice_i,
        "titre_td": titre_td,
        "source_transcription": source_trans,
        "methode": methode,
        "a_retenir": a_retenir,
        "indicateur_source": indicateur,
    }
def resolve_correction_pdf(matiere: str, type_code: str, num: str, exo: str,
                           annee: Optional[str] = None
                           ) -> Tuple[Optional[str], List[str]]:
    candidates: List[str] = []
    exo_str = str(exo)
    is_whole = (exo_str == "0")
    def _add(*parts: str) -> None:
        candidates.append(os.path.join(COURS_ROOT, *parts))
    if type_code == "TD":
        if is_whole:
            _add(matiere, "TD", f"TD{num}", "corrections",
                 f"correction_TD{num}_{matiere}.pdf")
        else:
            _add(matiere, "TD", f"TD{num}", "corrections",
                 f"correction_TD{num}_ex{exo_str}_{matiere}.pdf")
    elif type_code == "TP":
        if is_whole:
            _add(matiere, "TP", f"TP{num}", "corrections",
                 f"correction_TP{num}_{matiere}.pdf")
        else:
            _add(matiere, "TP", f"TP{num}", "corrections",
                 f"correction_TP{num}_ex{exo_str}_{matiere}.pdf")
    elif type_code == "CC":
        candidate_dirs: List[str] = []
        cc_root = os.path.join(COURS_ROOT, matiere, "CC")
        if os.path.isdir(cc_root):
            flat = os.path.join(cc_root, "corrections")
            if os.path.isdir(flat):
                candidate_dirs.append(flat)
            for entry in sorted(os.listdir(cc_root), reverse=True):
                sub_cc = os.path.join(cc_root, entry, f"CC{num}", "corrections")
                if os.path.isdir(sub_cc):
                    candidate_dirs.append(sub_cc)
        for d in candidate_dirs:
            try:
                files = sorted(os.listdir(d), reverse=True)
            except OSError:
                continue
            for fn in files:
                if not fn.lower().endswith(".pdf"):
                    continue
                if f"CC{num}" not in fn:
                    continue
                if matiere.upper() not in fn.upper():
                    continue
                if annee and f"_{annee}_" not in fn:
                    continue
                has_ex = bool(re.search(r"_ex(\d+)_", fn, re.IGNORECASE))
                if is_whole and has_ex:
                    continue
                if not is_whole and f"ex{exo_str}" not in fn.lower():
                    continue
                candidates.append(os.path.join(d, fn))
    for path in candidates:
        if os.path.isfile(path):
            return path, candidates
    return None, candidates
_CORRECTION_FILENAME_RE = re.compile(
    r"^correction_(TD|TP|CC|quiz)(\d+)"
    r"(?:_(\d{4}-\d{2,4}))?"
    r"(?:_ex(\d+))?"
    r"_[A-Z0-9]+\.pdf$",
    re.IGNORECASE,
)
def parse_correction_filename(pdf_path: str) -> Optional[dict]:
    fname = os.path.basename(pdf_path)
    m = _CORRECTION_FILENAME_RE.match(fname)
    if not m:
        return None
    raw_type = m.group(1)
    type_code = raw_type.lower() if raw_type.lower() == "quiz" else raw_type.upper()
    return {
        "type_code": type_code,
        "num": m.group(2),
        "annee": m.group(3),
        "exo": m.group(4) if m.group(4) else "0",
    }
_ENONCE_FILENAME_RE = re.compile(
    r"^enonce_(TD|TP|CC|quiz)(\d+)"
    r"(?:_(\d{4}-\d{2,4}))?"
    r"_[A-Z0-9]+\.pdf$",
    re.IGNORECASE,
)
def parse_enonce_filename(pdf_path: str) -> Optional[dict]:
    fname = os.path.basename(pdf_path)
    m = _ENONCE_FILENAME_RE.match(fname)
    if not m:
        return None
    raw_type = m.group(1)
    type_code = raw_type.lower() if raw_type.lower() == "quiz" else raw_type.upper()
    return {
        "type_code": type_code,
        "num": m.group(2),
        "annee": m.group(3),
    }
_TACHE_NAME_RE = re.compile(
    r"^TACHE_(?P<mat>[A-Z0-9]+)_"
    r"(?P<type>TD|TP|CC|CM|quiz)(?P<num>\d+)"
    r"(?:_(?P<annee>\d{4}-\d{2,4}))?"
    r"(?:_ex(?P<exo>\d+))?"
    r"\.md$",
    re.IGNORECASE,
)
_PERSO_SCRIPT_NAME_RE = re.compile(
    r"^(?P<prefix>SCRIPT|script_oral|script_imprimable|slides|project)_"
    r"(?P<mat>[A-Z0-9]+)_"
    r"(?P<type>TD|TP|CC|CM|quiz)(?P<num>\d+)"
    r"(?:_(?P<annee>\d{4}-\d{2,4}))?"
    r"(?:_ex(?P<exo>\d+))?"
    r".*$",
    re.IGNORECASE,
)
_PATH_TYPE_DIR_RE = re.compile(r"^(?P<type>TD|TP|CC|CM)(?P<num>\d+)$",
                               re.IGNORECASE)
_PATH_ANNEE_RE = re.compile(r"^\d{4}-\d{2,4}$")
_SLIDE_PNG_RE = re.compile(r"^slide_\d+\.png$", re.IGNORECASE)
_BACKUP_SUFFIX_RE = re.compile(
    r"(_old\.|_v\d+\.|_brouillon\.|_draft\.|\.bak(?:_[a-z]+)?$|\.bak$)",
    re.IGNORECASE,
)
_PERSO_VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}
_PERSO_VIDEO_DIR_HINTS = {"videos", "enregistrements", "exports", "replay"}
_PERSO_RECORDING_DIR_RE = re.compile(r"^recording", re.IGNORECASE)
_PERSO_SCRIPT_DIR_HINTS = {"scripts_oraux", "oral", "feynman", "narration"}
_PERSO_SLIDE_DIR_HINTS = {"slides", "beamer", "presentation"}
_PERSO_EXCLUDE_DIRS = {
    "_INBOX", "_A_VALIDER", "_A_TRIER", "_archives", "_scripts",
    "_prompts_claude_ai", "_temp_latex", "dumps", "_contextes_reprise",
    "_moodle", "__pycache__", ".git", ".idea", ".vscode", "node_modules",
    "depends",
    "audio", "audio_segments",
    "_composants",
    "transcriptions",
    "corrections",
}
_PERSO_EXCLUDE_DIR_SUFFIXES = ("_files",)
def _perso_path_excluded(rel_path: str) -> bool:
    parts = rel_path.split(os.sep)
    for p in parts:
        if p in _PERSO_EXCLUDE_DIRS:
            return True
        if any(p.endswith(s) for s in _PERSO_EXCLUDE_DIR_SUFFIXES):
            return True
    return False
def _perso_infer_type_num_annee(parts: list, name: str
                                 ) -> Optional[Tuple[str, str, Optional[str]]]:
    for pat in (_TACHE_NAME_RE, _PERSO_SCRIPT_NAME_RE):
        m = pat.match(name)
        if m:
            t = m.group("type")
            t = t.lower() if t.lower() == "quiz" else t.upper()
            num = m.group("num")
            annee = m.groupdict().get("annee")
            if t == "CC" and not annee:
                for p in parts:
                    if _PATH_ANNEE_RE.match(p):
                        annee = p
                        break
            return t, num, annee
    for p in parts:
        m = _PATH_TYPE_DIR_RE.match(p)
        if m:
            t = m.group("type").upper()
            num = m.group("num")
            annee = None
            if t == "CC":
                for q in parts:
                    if _PATH_ANNEE_RE.match(q):
                        annee = q
                        break
            return t, num, annee
    return None
def _perso_extract_exo(name: str) -> Optional[str]:
    m = re.search(r"_ex(\d+)", name, re.IGNORECASE)
    return m.group(1) if m else None
def _perso_make_post_key(kind: str, exo: Optional[str], ext: str = "") -> str:
    base = f"{kind}:ex{exo}" if exo and exo != "0" else f"{kind}:global"
    if kind == "script" and ext:
        return f"{base}:{ext.lstrip('.').lower()}"
    return base
def _perso_classify_file(rel_path: str, name: str
                         ) -> Optional[Tuple[str, Optional[str]]]:
    parts = rel_path.split(os.sep)
    parent_dirs = set(parts[:-1])
    name_lower = name.lower()
    ext = os.path.splitext(name_lower)[1]
    if _BACKUP_SUFFIX_RE.search(name_lower):
        return None
    if ext == ".pdf" and (
        name_lower == "enonce.pdf"
        or name_lower.startswith("enonce_")
        or name_lower.startswith("correction_")
    ):
        return None
    if ext == ".tex" and (
        "corrections" in parent_dirs or name_lower.startswith("correction_")
    ):
        return None
    if ext == ".png" and "scripts_oraux" in parent_dirs and _SLIDE_PNG_RE.match(name):
        return None
    if ext == ".md" and name_lower.startswith("tache_"):
        return "tache", _perso_extract_exo(name)
    if ext in (".md", ".txt", ".json"):
        in_script_dir = any(d in parent_dirs for d in _PERSO_SCRIPT_DIR_HINTS)
        is_script_named = name_lower.startswith(
            ("script_", "oral_", "narration_", "project_")
        )
        if (in_script_dir or is_script_named) and not name_lower.startswith("slides_"):
            return "script", _perso_extract_exo(name)
    if ext == ".pdf" and name_lower.startswith("script_imprimable_"):
        return "script_print", _perso_extract_exo(name)
    if ext == ".pdf":
        if (any(d in parent_dirs for d in _PERSO_SLIDE_DIR_HINTS)
                or "scripts_oraux" in parent_dirs
                or name_lower.startswith(("slides_", "beamer_", "pres_",
                                          "presentation_", "diapo_"))):
            return "slides", _perso_extract_exo(name)
        return None
    if ext == ".tex":
        if (any(d in parent_dirs for d in _PERSO_SLIDE_DIR_HINTS)
                or "scripts_oraux" in parent_dirs
                or name_lower.startswith(("slides_", "beamer_", "presentation_"))):
            return "slides_src", _perso_extract_exo(name)
        return None
    if ext in _PERSO_VIDEO_EXTS:
        if (any(d in parent_dirs for d in _PERSO_VIDEO_DIR_HINTS)
                or any(_PERSO_RECORDING_DIR_RE.match(d) for d in parent_dirs)):
            return "video", _perso_extract_exo(name)
        return "video", _perso_extract_exo(name)
    return None
def list_perso_material(matiere: str) -> List[dict]:
    out: List[dict] = []
    base = os.path.join(COURS_ROOT, matiere)
    if not os.path.isdir(base):
        return out
    for root, dirs, files in os.walk(base):
        rel_root = os.path.relpath(root, COURS_ROOT)
        if _perso_path_excluded(rel_root):
            dirs[:] = []
            continue
        dirs[:] = [
            d for d in dirs
            if d not in _PERSO_EXCLUDE_DIRS
            and not any(d.endswith(s) for s in _PERSO_EXCLUDE_DIR_SUFFIXES)
        ]
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, COURS_ROOT)
            if _perso_path_excluded(rel):
                continue
            cls = _perso_classify_file(rel, f)
            if cls is None:
                continue
            kind, exo = cls
            parts = rel.split(os.sep)
            tn = _perso_infer_type_num_annee(parts, f)
            if tn is None:
                continue
            type_code, num, annee = tn
            tkey = thread_key(matiere, type_code, num, annee)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            out.append({
                "kind": kind,
                "thread_key": tkey,
                "matiere": matiere,
                "type_code": type_code,
                "num": num,
                "annee": annee,
                "post_key": _perso_make_post_key(
                    kind, exo, os.path.splitext(f)[1]
                ),
                "file_path": full,
                "rel_key": rel.replace("\\", "/"),
                "size_bytes": size,
                "ext": os.path.splitext(f)[1].lower(),
                "exo": exo,
            })
    return out
def resolve_tache_md(matiere: str, type_code: str, num: str, exo: str
                     ) -> Optional[str]:
    exo_str = str(exo)
    if type_code in ("TD", "TP"):
        base = os.path.join(COURS_ROOT, matiere, type_code, f"{type_code}{num}")
        if exo_str == "0":
            candidates = [
                os.path.join(base, f"TACHE_{matiere}_{type_code}{num}.md"),
                os.path.join(base, f"TACHE_{matiere}_{type_code}{num}_ex0.md"),
            ]
        else:
            candidates = [
                os.path.join(base, f"TACHE_{matiere}_{type_code}{num}_ex{exo_str}.md"),
            ]
        for c in candidates:
            if os.path.isfile(c):
                return c
    elif type_code == "CC":
        cc_root = os.path.join(COURS_ROOT, matiere, "CC")
        if os.path.isdir(cc_root):
            pattern = f"TACHE_{matiere}_CC{num}"
            for root, _, files in os.walk(cc_root):
                for fn in files:
                    if not fn.startswith(pattern) or not fn.endswith(".md"):
                        continue
                    if exo_str == "0":
                        if "_ex" in fn:
                            continue
                    else:
                        if f"_ex{exo_str}." not in fn:
                            continue
                    return os.path.join(root, fn)
    return None
def find_enonce_pdf(folder: str, type_code: Optional[str] = None,
                    num: Optional[str] = None, matiere: Optional[str] = None,
                    annee: Optional[str] = None) -> Optional[str]:
    if not os.path.isdir(folder):
        return None
    if not (type_code and num and matiere):
        old = os.path.join(folder, "enonce.pdf")
        if os.path.isfile(old):
            return old
        try:
            for fn in sorted(os.listdir(folder)):
                low = fn.lower()
                if low.startswith("enonce") and low.endswith(".pdf"):
                    return os.path.join(folder, fn)
        except OSError:
            pass
        return None
    if annee:
        exact = os.path.join(
            folder, f"enonce_{type_code}{num}_{annee}_{matiere}.pdf"
        )
    else:
        exact = os.path.join(
            folder, f"enonce_{type_code}{num}_{matiere}.pdf"
        )
    if os.path.isfile(exact):
        return exact
    prefix = f"enonce_{type_code}{num}"
    try:
        for fn in sorted(os.listdir(folder)):
            low = fn.lower()
            if not low.endswith(".pdf"):
                continue
            if not fn.startswith(prefix):
                continue
            if matiere.upper() not in fn.upper():
                continue
            if annee and f"_{annee}_" not in fn:
                continue
            after = fn[len(prefix):]
            if after and not re.match(r"^[A-Za-z0-9]", after):
                return os.path.join(folder, fn)
    except OSError:
        pass
    old = os.path.join(folder, "enonce.pdf")
    if os.path.isfile(old):
        return old
    return None
def has_enonce(folder: str, type_code: Optional[str] = None,
               num: Optional[str] = None, matiere: Optional[str] = None,
               annee: Optional[str] = None) -> bool:
    return find_enonce_pdf(folder, type_code, num, matiere, annee) is not None
def resolve_enonce_pdf(matiere: str, type_code: str, num: str,
                       annee: Optional[str] = None) -> Optional[str]:
    t_upper = type_code.upper() if type_code.lower() != "quiz" else "quiz"
    if t_upper in ("TD", "TP"):
        folder = os.path.join(
            COURS_ROOT, matiere, t_upper, f"{t_upper}{num}"
        )
        return find_enonce_pdf(folder, t_upper, num, matiere)
    if t_upper == "CC":
        if annee:
            subdir = os.path.join(
                COURS_ROOT, matiere, "CC", annee, f"CC{num}"
            )
            if os.path.isdir(subdir):
                found = find_enonce_pdf(subdir, "CC", num, matiere, annee)
                if found:
                    return found
        flat = os.path.join(COURS_ROOT, matiere, "CC")
        return find_enonce_pdf(flat, "CC", num, matiere, annee)
    if t_upper == "quiz":
        flat = os.path.join(COURS_ROOT, matiere, "CC")
        target = os.path.join(flat, f"enonce_quiz{num}_{matiere}.pdf")
        if os.path.isfile(target):
            return target
        return None
    return None
def short_titre_td(titre_td: Optional[str]) -> str:
    if not titre_td:
        return ""
    t = titre_td.strip()
    for sep in ("—", "–", ":", "-"):
        if sep in t:
            head, _, tail = t.partition(sep)
            if tail.strip() and re.search(r"^(feuille|td|tp|cc)\b", head, re.IGNORECASE):
                return tail.strip()
    return t
def build_correction_embed(parsed: dict) -> discord.Embed:
    matiere = parsed.get("matiere") or ""
    color = CORRECTION_EMBED_COLORS.get(matiere, LOG_COLOR_DEFAULT)
    embed = discord.Embed(color=color)
    methode = parsed.get("methode")
    if methode:
        embed.add_field(name="📘 Méthode", value=methode, inline=False)
    retenir = parsed.get("a_retenir")
    if retenir:
        embed.add_field(name="🎯 À retenir", value=retenir, inline=False)
    indicateur = parsed.get("indicateur_source")
    source_trans = parsed.get("source_transcription")
    footer_parts: List[str] = []
    if indicateur:
        footer_parts.append(f"Source : {indicateur}")
    if source_trans:
        footer_parts.append(source_trans)
    if footer_parts:
        embed.set_footer(text=" · ".join(footer_parts))
    return embed
def build_td_thread_title(type_code: str, num: str,
                          annee: Optional[str],
                          titre_td: Optional[str]) -> str:
    short = short_titre_td(titre_td)
    t_upper = type_code.upper() if type_code.lower() != "quiz" else "Quiz"
    if annee and t_upper in ("CC", "Quiz"):
        prefix = f"[{t_upper}{num} {annee}]"
    else:
        prefix = f"[{t_upper}{num}]"
    title = f"{prefix} {short}".strip() if short else prefix
    if len(title) > 100:
        title = title[:97].rstrip() + "…"
    return title
TAG_LABELS_TYPE = {
    "TD":   ("TD",   None),
    "TP":   ("TP",   None),
    "CC":   ("CC",   None),
    "CM":   ("CM",   None),
    "quiz": ("Quiz", None),
}
TAG_LABELS_STATE = {
    "enonce_only":         ("Énoncé seul",           "📄"),
    "corrections_present": ("Corrections présentes", "✍️"),
    "missing_enonce":      ("Énoncé manquant",       "📄"),
}
PERSO_TAG_LABELS_TYPE = TAG_LABELS_TYPE
PERSO_TAG_LABELS_MATERIEL = {
    "tache":  ("TACHE",       "📋"),
    "script": ("Script oral", "📝"),
    "slides": ("Slides",      "📊"),
    "video":  ("Vidéo",       "🎬"),
}
def get_forum_tag(forum: discord.ForumChannel, label: str) -> Optional[discord.ForumTag]:
    for tag in forum.available_tags:
        if tag.name == label:
            return tag
    return None
async def apply_thread_tags(thread: discord.Thread,
                            type_code: str, state: str) -> List[str]:
    forum = thread.parent
    if not isinstance(forum, discord.ForumChannel):
        return []
    type_key = type_code.lower() if type_code.lower() == "quiz" else type_code.upper()
    type_tuple = TAG_LABELS_TYPE.get(type_key)
    type_name = type_tuple[0] if type_tuple else None
    state_tuple = TAG_LABELS_STATE.get(state)
    state_name = state_tuple[0] if state_tuple else None
    tags_to_apply: List[discord.ForumTag] = []
    labels_applied: List[str] = []
    if type_name:
        t = get_forum_tag(forum, type_name)
        if t:
            tags_to_apply.append(t)
            labels_applied.append(type_name)
    if state_name:
        t = get_forum_tag(forum, state_name)
        if t:
            tags_to_apply.append(t)
            labels_applied.append(state_name)
    if tags_to_apply:
        try:
            await thread.edit(applied_tags=tags_to_apply)
        except discord.HTTPException as e:
            log.warning(f"apply_thread_tags: échec edit thread {thread.id}: {e}")
    return labels_applied
_TYPE_FROM_TITLE_RE = re.compile(r"\[(TD|TP|CC|Quiz)\s*\d*\]", re.IGNORECASE)
_TYPE_FALLBACK_RE = re.compile(r"\b(TD|TP|CC|Quiz)\d+(?!\d)", re.IGNORECASE)
def infer_type_code_from_title(title: str) -> Optional[str]:
    if not title:
        return None
    m = _TYPE_FROM_TITLE_RE.search(title)
    if not m:
        m = _TYPE_FALLBACK_RE.search(title)
    if not m:
        return None
    code = m.group(1)
    return code.lower() if code.lower() == "quiz" else code.upper()
def infer_perso_materiel_kinds(files: List[Dict[str, str]]) -> set:
    out = set()
    if not files:
        return out
    for entry in files:
        k = (entry.get("kind", "") or "").strip().lower()
        if k in {"tache", "script", "script_print", "slides", "slides_src", "video"}:
            out.add(k)
    return out
_TITLE_NUM_PART = r"(?:\d+|_?[A-Za-z][\w]*)"
_TITLE_FULL_A = re.compile(
    rf"\[(TD|TP|CC|Quiz)\s*({_TITLE_NUM_PART})\s+(\d{{4}}-\d{{2,4}})\]",
    re.IGNORECASE,
)
_TITLE_FULL_B = re.compile(
    rf"\[(TD|TP|CC|Quiz)\s*({_TITLE_NUM_PART})\][^()]*\((\d{{4}}-\d{{2,4}})\)",
    re.IGNORECASE,
)
_TITLE_FULL_C = re.compile(
    rf"\[(TD|TP|CC|Quiz)\s*({_TITLE_NUM_PART})\]",
    re.IGNORECASE,
)
_TITLE_FULL_D = re.compile(
    r"\[(TD|TP|CC|Quiz)\]\s+([A-Za-z][\w]+)",
    re.IGNORECASE,
)
_TITLE_FULL_E = re.compile(
    r"\b(TD|TP|CC|Quiz)(\d+)(?!\d)",
    re.IGNORECASE,
)
def parse_thread_title_full(title: str) -> Optional[Tuple[str, str, Optional[str]]]:
    if not title:
        return None
    for rx in (_TITLE_FULL_A, _TITLE_FULL_B, _TITLE_FULL_C):
        m = rx.search(title)
        if m:
            raw_type = m.group(1)
            type_code = raw_type.lower() if raw_type.lower() == "quiz" else raw_type.upper()
            num = m.group(2).lstrip("_")
            annee = m.group(3) if rx is not _TITLE_FULL_C else None
            return (type_code, num, annee)
    m = _TITLE_FULL_D.search(title)
    if m:
        raw_type = m.group(1)
        type_code = raw_type.lower() if raw_type.lower() == "quiz" else raw_type.upper()
        return (type_code, m.group(2).upper(), None)
    m = _TITLE_FULL_E.search(title)
    if m:
        raw_type = m.group(1)
        type_code = raw_type.lower() if raw_type.lower() == "quiz" else raw_type.upper()
        return (type_code, m.group(2), None)
    return None
def _absence_key(type_code: str, matiere: str, num: str) -> str:
    return f"{type_code}{num}_{matiere}"
def load_absences() -> dict:
    if not os.path.isfile(ABSENCES_JSON):
        return {}
    try:
        with open(ABSENCES_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        abs_ = data.get("absences", {}) if isinstance(data, dict) else {}
        return abs_ if isinstance(abs_, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}
def save_absences(absences: dict) -> None:
    try:
        os.makedirs(os.path.dirname(ABSENCES_JSON), exist_ok=True)
        existing: dict = {}
        if os.path.isfile(ABSENCES_JSON):
            try:
                with open(ABSENCES_JSON, "r", encoding="utf-8") as f:
                    existing = json.load(f) or {}
            except (json.JSONDecodeError, OSError):
                existing = {}
        existing.setdefault(
            "_comment",
            "Séances marquées comme absentes. Format clé : TYPE_NUM_MATIERE (ex: CM11_AN1). La date est optionnelle.",
        )
        existing["absences"] = absences
        with open(ABSENCES_JSON, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False, sort_keys=False)
    except OSError as e:
        log.warning(f"Impossible d'écrire {ABSENCES_JSON}: {e}")
def mark_absent(type_code: str, matiere: str, num: str,
                date: Optional[str] = None, raison: str = "absent",
                posted_discord: bool = False) -> str:
    key = _absence_key(type_code, matiere, num)
    absences = load_absences()
    entry = absences.get(key, {})
    entry["raison"] = raison
    entry["date"] = date
    entry["posted_discord"] = bool(entry.get("posted_discord")) or bool(posted_discord)
    entry["timestamp"] = datetime.utcnow().isoformat(timespec="seconds")
    absences[key] = entry
    save_absences(absences)
    return key
_AUDIO_PATTERN = re.compile(
    r"^(CM|TD|TP)(\d+)[_ ]([A-Z]+\d*)[_ ](\d{4})\.m4a$",
    re.IGNORECASE,
)
_TXT_PATTERN = re.compile(
    r"^(CM|TD|TP)(\d+)[_ ]([A-Z]+\d*)[_ ](\d{4})\.txt$",
    re.IGNORECASE,
)
def scan_available(matiere_filter: Optional[str] = None) -> List[Dict]:
    target_matiere: Optional[str] = None
    if matiere_filter:
        mf = matiere_filter.lower()
        if mf in MATIERE_MAP:
            target_matiere = MATIERE_MAP[mf]
        elif matiere_filter.upper() in MATIERE_MAP.values():
            target_matiere = matiere_filter.upper()
        else:
            target_matiere = matiere_filter.upper()
    sessions: Dict[str, Dict] = {}
    if os.path.isdir(AUDIO_ROOT):
        for f in os.listdir(AUDIO_ROOT):
            m = _AUDIO_PATTERN.match(f)
            if not m:
                continue
            type_code = m.group(1).upper()
            num = m.group(2)
            matiere = m.group(3).upper()
            date = m.group(4)
            key = f"{type_code}{num}_{matiere}_{date}"
            sessions.setdefault(key, {
                "type": type_code, "matiere": matiere, "num": num, "date": date,
                "has_audio": False, "has_transcript": False,
            })
            sessions[key]["has_audio"] = True
    if os.path.isdir(COURS_ROOT):
        for matiere in MATIERE_MAP.values():
            matiere_dir = os.path.join(COURS_ROOT, matiere)
            if not os.path.isdir(matiere_dir):
                continue
            for type_code in TYPE_MAP.values():
                type_dir = os.path.join(matiere_dir, type_code)
                if not os.path.isdir(type_dir):
                    continue
                for root, _, files in os.walk(type_dir):
                    for fn in files:
                        if not fn.lower().endswith(".txt"):
                            continue
                        m = _TXT_PATTERN.match(fn)
                        if not m:
                            continue
                        t = m.group(1).upper()
                        n = m.group(2)
                        mat = m.group(3).upper()
                        d = m.group(4)
                        key = f"{t}{n}_{mat}_{d}"
                        sessions.setdefault(key, {
                            "type": t, "matiere": mat, "num": n, "date": d,
                            "has_audio": False, "has_transcript": False,
                        })
                        sessions[key]["has_transcript"] = True
    published = load_published()
    absences = load_absences()
    result: List[Dict] = []
    for key, info in sessions.items():
        if target_matiere and info["matiere"] != target_matiere:
            continue
        abs_key = _absence_key(info["type"], info["matiere"], info["num"])
        if abs_key in absences:
            continue
        pub = published.get(key, {})
        audio_done = bool(pub.get("audio"))
        trans_done = bool(pub.get("transcription"))
        resume_done = bool(pub.get("resume"))
        pending_audio = info["has_audio"] and not audio_done
        pending_trans = info["has_transcript"] and not trans_done
        pending_resume = info["has_transcript"] and not resume_done
        if not (pending_audio or pending_trans or pending_resume):
            continue
        info["published"] = pub
        result.append(info)
    result.sort(key=lambda x: (x["matiere"], x["type"], int(x["num"])))
    return result
def call_claude_code(prompt_text: str, timeout: int = 600) -> str:
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    proc = subprocess.run(
        ["claude", "--print"],
        input=prompt_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"claude CLI exit {proc.returncode}: "
            f"{(proc.stderr or proc.stdout).strip()[:500]}"
        )
    return (proc.stdout or "").strip()
def call_claude_api(prompt_text: str) -> Tuple[str, int, int]:
    if not API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY absente : impossible d'appeler l'API")
    client = anthropic.Anthropic(api_key=API_KEY)
    response = client.messages.create(
        model=SUMMARY_MODEL,
        max_tokens=SUMMARY_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt_text}],
    )
    text = ""
    for block in response.content:
        if getattr(block, "type", None) == "text":
            text += block.text
    return text.strip(), response.usage.input_tokens, response.usage.output_tokens
def build_latex_prompt(transcription_path: str, type_code: str, matiere: str,
                       num: str, date_str: str) -> str:
    with open(transcription_path, "r", encoding="utf-8", errors="replace") as f:
        txt = f.read().strip()
    if not txt:
        txt = "[Transcription vide ou illisible]"
    formatted_date = format_date(date_str)
    session_label = f"{type_code}{num} {matiere} | {formatted_date}"
    source_filename = os.path.basename(transcription_path)
    return (
        f"Tu es un assistant spécialisé dans la mise en forme de notes de cours universitaires en LaTeX.\n"
        f"Contexte : L1 ISTIC Rennes, matière {matiere}, séance {session_label}.\n"
        f"Fichier source : {source_filename}\n\n"
        f"TÂCHE : à partir de la transcription ci-dessous, produire un document LaTeX "
        f"COMPLET et compilable avec pdflatex (MiKTeX).\n\n"
        f"PRÉAMBULE OBLIGATOIRE (recopier strictement, ne rien ajouter/retirer) :\n"
        f"{'-' * 60}\n"
        f"{LATEX_PREAMBLE}"
        f"{'-' * 60}\n\n"
        f"Après le préambule, ajoute :\n"
        f"  \\title{{{type_code}{num} {matiere} : séance du {formatted_date}}}\n"
        f"  \\author{{Notes automatiques : Pipeline COURS}}\n"
        f"  \\date{{{formatted_date}}}\n"
        f"  \\begin{{document}}\n"
        f"  \\maketitle\n"
        f"  \\tableofcontents\n"
        f"  ... contenu structuré ...\n"
        f"  \\end{{document}}\n\n"
        f"STRUCTURE DU CONTENU :\n"
        f"1. Sections claires (\\section) reflétant le plan du cours.\n"
        f"2. Sous-sections (\\subsection) pour les points précis.\n"
        f"3. Encadrés colorés :\n"
        f"   - \\begin{{concept}}[Titre]...\\end{{concept}} pour définitions/théorèmes.\n"
        f"   - \\begin{{examalert}}...\\end{{examalert}} pour ce qui tombe à l'exam.\n"
        f"4. Formules en \\begin{{align*}}...\\end{{align*}} (pas de $$...$$).\n"
        f"5. Code source en \\begin{{minted}}{{<langage>}}...\\end{{minted}}.\n"
        f"6. En FIN de document, une section \"Questions \\& Réponses (FAQ)\" avec des \\begin{{qrbox}}[question]...\\end{{qrbox}} pour 3 à 6 questions typiques que l'étudiant pourrait se poser.\n\n"
        f"RÈGLES DE SÉCURITÉ LATEX :\n"
        f"- Échapper & → \\&, _ → \\_, % → \\%, # → \\#, $ → \\$ dans le texte courant et les titres.\n"
        f"- JAMAIS d'émojis (aucun caractère Unicode hors latin : pas de ⚠️ ni 📌 ni →, utilise $\\rightarrow$).\n"
        f"- Pas de caractères exotiques qui casseraient le compilateur : remplacer « ... » par ``...''.\n"
        f"- Les guillemets doubles : ``texte'' (deux backticks + deux apostrophes).\n"
        f"- Les accents : utiliser directement é è à (utf8 est chargé), PAS \\'{{e}}.\n\n"
        f"FORMAT DE RÉPONSE :\n"
        f"- Réponds UNIQUEMENT avec le code LaTeX brut.\n"
        f"- PAS de ```latex, PAS de ```, PAS de préambule conversationnel, PAS d'explication.\n"
        f"- Ta réponse doit commencer EXACTEMENT par \\documentclass et finir par \\end{{document}}.\n\n"
        f"TRANSCRIPTION À TRAITER\n"
        f"{'=' * 60}\n"
        f"{txt}"
    )
def strip_latex_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        first_nl = t.find("\n")
        if first_nl != -1:
            t = t[first_nl + 1:]
    if t.endswith("```"):
        t = t[:-3].rstrip()
    idx = t.find("\\documentclass")
    if idx > 0:
        t = t[idx:]
    end_marker = "\\end{document}"
    idx_end = t.rfind(end_marker)
    if idx_end != -1:
        t = t[:idx_end + len(end_marker)]
    return t.strip()
def compile_latex(tex_content: str, output_dir: str, filename: str) -> Optional[str]:
    os.makedirs(COURS_TEMP, exist_ok=True)
    base = os.path.splitext(filename)[0]
    tex_path = os.path.join(COURS_TEMP, base + ".tex")
    pdf_path = os.path.join(COURS_TEMP, base + ".pdf")
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write(tex_content)
    pdflatex_bin = PDFLATEX if os.path.isfile(PDFLATEX) else "pdflatex"
    try:
        for pass_num in (1, 2):
            proc = subprocess.run(
                [pdflatex_bin, "-shell-escape", "-interaction=nonstopmode",
                 "-output-directory", COURS_TEMP, tex_path],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=LATEX_TIMEOUT,
                cwd=COURS_TEMP,
            )
            if not os.path.isfile(pdf_path):
                log.warning(f"pdflatex passe {pass_num} : PDF absent : stderr: {proc.stderr[:500]}")
                if pass_num == 1 and proc.returncode != 0:
                    break
    except subprocess.TimeoutExpired:
        log.error(f"pdflatex timeout ({LATEX_TIMEOUT}s) sur {base}")
        return None
    except Exception as e:
        log.error(f"pdflatex exception : {e}")
        return None
    for ext in (".aux", ".log", ".out", ".toc", ".pyg", ".fls", ".fdb_latexmk", ".synctex.gz"):
        aux = os.path.join(COURS_TEMP, base + ext)
        if os.path.isfile(aux):
            try:
                os.remove(aux)
            except OSError:
                pass
    minted_dir = os.path.join(COURS_TEMP, "_minted-" + base)
    if os.path.isdir(minted_dir):
        shutil.rmtree(minted_dir, ignore_errors=True)
    return pdf_path if os.path.isfile(pdf_path) else None
def find_channel(guild: discord.Guild, type_lower: str, media: str, matiere_lower: str) -> Optional[discord.TextChannel]:
    target = f"{type_lower}-{media}-{matiere_lower}"
    for channel in guild.text_channels:
        name = channel.name.lower()
        if target in name:
            return channel
    return None
def find_matiere_category(guild: discord.Guild, matiere: str) -> Optional[discord.CategoryChannel]:
    needle = matiere.upper()
    for cat in guild.categories:
        if needle in cat.name.upper():
            return cat
    return None
def find_correction_forum(guild: discord.Guild, matiere: str) -> Optional[discord.ForumChannel]:
    category = find_matiere_category(guild, matiere)
    if category is None:
        return None
    expected = correction_forum_name(matiere)
    for ch in category.channels:
        if isinstance(ch, discord.ForumChannel) and expected in ch.name.lower():
            return ch
    return None
def find_perso_category(guild: discord.Guild) -> Optional[discord.CategoryChannel]:
    needle = "PERSONNEL"
    for cat in guild.categories:
        if needle in cat.name.upper():
            return cat
    return None
def find_perso_forum(guild: discord.Guild,
                     matiere: str) -> Optional[discord.ForumChannel]:
    cat = find_perso_category(guild)
    if cat is None:
        return None
    expected = perso_forum_name(matiere)
    for ch in cat.channels:
        if isinstance(ch, discord.ForumChannel) and expected in ch.name.lower():
            return ch
    return None
def find_hors_sujets_forum(
    guild: discord.Guild,
) -> Optional[discord.ForumChannel]:
    cat = find_perso_category(guild)
    if cat is None:
        return None
    for ch in cat.channels:
        if (isinstance(ch, discord.ForumChannel)
                and HORS_SUJETS_FORUM_NAME in ch.name.lower()):
            return ch
    return None
def find_inbox_forum(
    guild: discord.Guild,
    matiere: str,
) -> Optional[discord.ForumChannel]:
    cat = find_perso_category(guild)
    if cat is None:
        return None
    expected = inbox_forum_name(matiere)
    for ch in cat.channels:
        if isinstance(ch, discord.ForumChannel) and expected in ch.name.lower():
            return ch
    return None
async def generate_and_post_latex_summary(ctx, resume_channel, transcript_path: str,
                                          type_code: str, matiere: str, num: str,
                                          date: str, session_label: str,
                                          log_fn=None) -> bool:
    async def _maybe_log(msg: str, color: int = LOG_COLOR_DEFAULT):
        if log_fn:
            try:
                await log_fn(msg, color=color)
            except TypeError:
                await log_fn(msg)
    transcript_size = os.path.getsize(transcript_path)
    source_filename = os.path.basename(transcript_path)
    await _maybe_log(
        f"🤖 Génération LaTeX via Claude Code CLI subscription "
        f"(source : `{source_filename}`, {transcript_size} chars)",
        color=LOG_COLOR_INFO,
    )
    prompt = build_latex_prompt(transcript_path, type_code, matiere, num, date)
    loop = asyncio.get_event_loop()
    api_start = time.monotonic()
    raw = await loop.run_in_executor(None, call_claude_code, prompt)
    api_elapsed = time.monotonic() - api_start
    if not raw:
        raise RuntimeError("Réponse vide de Claude Code CLI")
    await _maybe_log(
        f"🤖 Claude Code CLI : {len(raw)} chars en {api_elapsed:.1f}s "
        f"(subscription, 0 €)",
        color=LOG_COLOR_INFO,
    )
    tex_content = strip_latex_fences(raw)
    if not tex_content.startswith("\\documentclass"):
        raise RuntimeError("Réponse Claude Code sans \\documentclass")
    base_filename = f"{type_code}{num}_{matiere}_{date}"
    archive_dir = os.path.join(COURS_ROOT, matiere, type_code)
    os.makedirs(archive_dir, exist_ok=True)
    archive_tex = os.path.join(archive_dir, base_filename + ".tex")
    with open(archive_tex, "w", encoding="utf-8") as f:
        f.write(tex_content)
    await ctx.send(f"📐 Compilation LaTeX en cours...")
    await _maybe_log("📐 Compilation pdflatex...", color=LOG_COLOR_INFO)
    pdf_path = await loop.run_in_executor(None, compile_latex, tex_content, COURS_TEMP, base_filename)
    if pdf_path and os.path.isfile(pdf_path):
        size_mb = os.path.getsize(pdf_path) / (1024 * 1024)
        if os.path.getsize(pdf_path) > DISCORD_FILE_LIMIT:
            await resume_channel.send(
                f"📌 **{session_label}** : PDF trop lourd ({size_mb:.1f} Mo) pour Discord."
            )
            await ctx.send(f"⚠️ PDF généré ({size_mb:.1f} Mo) mais trop gros pour Discord.")
            await _maybe_log(
                f"⚠️ PDF trop lourd ({size_mb:.2f} Mo) pour Discord.",
                color=LOG_COLOR_WARN,
            )
            return False
        file = discord.File(pdf_path, filename=base_filename + ".pdf")
        await resume_channel.send(f"📌 **{session_label}**", file=file)
        await ctx.send(f"✅ Résumé PDF posté ({size_mb:.2f} Mo).")
        await _maybe_log(
            f"✅ PDF résumé posté dans <#{resume_channel.id}> ({size_mb:.2f} Mo)",
            color=LOG_COLOR_OK,
        )
        return True
    else:
        file = discord.File(archive_tex, filename=base_filename + ".tex")
        await resume_channel.send(
            f"📌 **{session_label}** : compilation LaTeX échouée, "
            f"fichier `.tex` joint (à compiler sur Overleaf).",
            file=file,
        )
        await ctx.send("⚠️ Compilation échouée : `.tex` posté à la place.")
        await _maybe_log(
            f"⚠️ Compilation échouée → `.tex` posté dans <#{resume_channel.id}>",
            color=LOG_COLOR_WARN,
        )
        return False
class _HeadlessCtx:
    __slots__ = ("bot", "guild", "author", "channel", "_sent")
    def __init__(self, bot):
        self.bot = bot
        self.guild = None
        self.author = None
        self.channel = None
        self._sent: List[str] = []
    async def send(self, content=None, *args, **kwargs):
        if content:
            self._sent.append(str(content)[:200])
        return None
    async def reply(self, content=None, *args, **kwargs):
        return await self.send(content, *args, **kwargs)
class CoursPipeline(commands.Cog, name="Cours"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._api_ok = bool(API_KEY)
        self._startup_scan_done = False
        self._inbox_last_sizes: Dict[str, int] = {}
        self._inbox_watcher_logged = False
        self._publish_queue_logged = False
        self.corrections_watcher_task: Optional[asyncio.Task] = None
        self.corrections_watcher_running: bool = False
        self.corrections_today_count: Dict[str, Dict[str, int]] = {}
        self.corrections_today_date: Optional[str] = None
        self._last_recap_date: Optional[str] = None
        log.info("Pipeline résumé : claude CLI subscription (Phase L)")
        if not self._api_ok:
            log.info("ANTHROPIC_API_KEY absente : OK car CLI subscription utilisé")
        self._inbox_watcher.start()
        self._publish_queue_watcher.start()
    def cog_unload(self):
        self._inbox_watcher.cancel()
        self._publish_queue_watcher.cancel()
        if (self.corrections_watcher_task
                and not self.corrections_watcher_task.done()):
            self.corrections_watcher_task.cancel()
    @commands.Cog.listener()
    async def on_ready(self):
        try:
            await self._self_heal_hors_sujets_forum()
        except Exception:
            log.exception("self_heal_hors_sujets_forum a crashé (non-bloquant)")
        if self.corrections_watcher_running:
            return
        if (self.corrections_watcher_task
                and not self.corrections_watcher_task.done()):
            return
        log.info("Auto-start du corrections watcher (boot ou reconnect)")
        self.corrections_watcher_task = asyncio.create_task(
            self._corrections_watcher_loop()
        )
        try:
            await self._log(
                "🚀 Corrections watcher auto-démarré",
                color=LOG_COLOR_OK, title="Watcher",
            )
        except Exception:
            pass
    async def _self_heal_hors_sujets_forum(self) -> None:
        guild = self._get_guild()
        if guild is None:
            return
        if find_hors_sujets_forum(guild) is not None:
            return
        category = find_perso_category(guild)
        if category is None:
            try:
                await self._log(
                    "⚠️ Catégorie `🔒 PERSONNEL` introuvable. "
                    "Lance `!cours setup-perso` pour créer l'infra.",
                    color=LOG_COLOR_WARN, title="Self-heal Phase L",
                )
            except Exception:
                pass
            return
        try:
            forum = await category.create_forum(
                name=HORS_SUJETS_FORUM_NAME,
                reason="Self-heal Phase L : forum hors-sujets",
                topic="Contenu hors-cours (mémos, brainstorms, etc., privé)",
            )
            await self._log(
                f"✅ Forum `{HORS_SUJETS_FORUM_NAME}` auto-créé sous "
                f"`{PERSO_CATEGORY_NAME}` → <#{forum.id}>",
                color=LOG_COLOR_OK, title="Self-heal Phase L",
            )
        except discord.Forbidden:
            try:
                await self._log(
                    "❌ Permission refusée pour créer le forum `hors-sujets`.",
                    color=LOG_COLOR_ERROR, title="Self-heal Phase L",
                )
            except Exception:
                pass
        except discord.HTTPException as e:
            try:
                await self._log(
                    f"❌ Erreur création forum `hors-sujets` : {str(e)[:200]}",
                    color=LOG_COLOR_ERROR, title="Self-heal Phase L",
                )
            except Exception:
                pass
    def _get_guild(self) -> Optional[discord.Guild]:
        return self.bot.get_guild(ISTIC_GUILD_ID)
    async def cog_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            return False
        role = ctx.guild.get_role(ADMIN_ROLE_ID)
        if role is None:
            return False
        return role in ctx.author.roles
    async def _log(self, msg: str, color: int = LOG_COLOR_DEFAULT,
                   title: Optional[str] = None,
                   fields: Optional[List[Tuple[str, str, bool]]] = None):
        ch = self.bot.get_channel(LOG_CHANNEL_ID)
        if not ch and not LOG_WEBHOOK:
            return
        try:
            embed = discord.Embed(description=msg, color=color)
            if title:
                embed.title = title
            if fields:
                for name, value, inline in fields:
                    embed.add_field(name=name, value=value, inline=inline)
            embed.set_footer(text="Pipeline COURS")
            embed.timestamp = datetime.utcnow()
            await webhook_log.send_log(LOG_WEBHOOK, ch, embed=embed, username="Pipeline COURS")
        except Exception:
            pass
    def _validate_args(self, type_str: str, matiere_str: str, num: str, date: str) -> Tuple[bool, str, str, str]:
        type_lower = type_str.lower()
        matiere_lower = matiere_str.lower()
        if type_lower not in TYPE_MAP:
            return False, "", "", f"Type invalide `{type_str}`. Valeurs acceptées : {', '.join(TYPE_MAP.keys())}"
        if matiere_lower not in MATIERE_MAP:
            return False, "", "", f"Matière invalide `{matiere_str}`. Valeurs acceptées : {', '.join(MATIERE_MAP.keys())}"
        if not num.isdigit():
            return False, "", "", f"Numéro invalide `{num}`. Doit être un entier (ex: 7)"
        if not re.match(r"^\d{4}$", date):
            return False, "", "", f"Date invalide `{date}`. Format attendu : JJMM (ex: 1602)"
        type_code = TYPE_MAP[type_lower]
        matiere = MATIERE_MAP[matiere_lower]
        return True, type_code, matiere, ""
    @commands.group(name="cours", invoke_without_command=True)
    async def cours(self, ctx: commands.Context):
        await ctx.send(
            "**Commandes disponibles :**\n"
            "`!cours publish <type> <matiere> <num> <date>` : Publication complète\n"
            "`!cours publish correction <matiere> <type> <num> <exo>` : Publie une correction PDF (quiz accepté)\n"
            "`!cours publish enonce <matiere> <type> <num> [annee]` : Publie un énoncé seul (crée le thread si absent)\n"
            "`!cours backfill <matiere>` : Rattrape le stock existant (1 thread par TD/TP/CC)\n"
            "`!cours backfill-enonces <matiere>` : Rattrape les énoncés manquants pour la matière\n"
            "`!cours republish-correction <matiere> <type> <num> <exo> [annee]` : Force republication (MD5 inchangé)\n"
            "`!cours purge-thread <matiere> <type> <num> [annee]` : Réinitialise l'entrée JSON d'un thread\n"
            "`!cours republish <type> <matiere> <num> <date>` : Re-poster le résumé seul\n"
            "`!cours scan [matiere]` : Liste les séances non publiées\n"
            "`!cours auto [matiere]` : Publie toutes les séances non publiées (confirmation)\n"
            "`!cours absent <type> <matière> <num> [date] [raison]` : Marque une séance absente\n"
            "`!cours absences` : Liste les absences enregistrées\n"
            "`!cours sync-absences` : Scanne l'historique Discord pour détecter les absences\n"
            "`!cours setup-channels` : Normalise les emojis des salons (🎧📝📌📋)\n"
            "`!cours setup-forums` : Crée les 5 forums correction (un par matière)\n"
            "`!cours setup-tags` : Crée les 7 tags forum nécessaires (type + état)\n"
            "`!cours setup-perso` : Crée la catégorie 🔒 PERSONNEL et les 5 forums privés (admin only)\n"
            "`!cours setup-tags-perso` : Crée les 8 tags forum perso (type + matériel)\n"
            "`!cours publish-perso <mat> <type> <num> [annee]` : Publie tout le matériel perso d'un TD/TP/CC\n"
            "`!cours backfill-perso <mat>` : Rattrape tout le matériel perso d'une matière\n"
            "`!cours purge-perso <mat> <type> <num> [annee]` : Vide l'entrée tracking perso (sans toucher Discord)\n"
            "`!cours inbox` : Force un scan immédiat du dossier _INBOX\n"
            "`!cours watcher <start|stop|status>` : Contrôle le watcher auto-publication des corrections\n"
            "`!cours rapport [matiere] [--deep]` : Inventaire + analyse IA optionnelle\n"
            "`!cours status` : Fichiers en attente\n"
            "`!cours missing` : Séances sans audio"
        )
    @cours.command(name="publish")
    async def publish(self, ctx: commands.Context, *args: str):
        if args and args[0].lower() == "correction":
            rest = args[1:]
            if len(rest) != 4:
                await ctx.send(
                    "❌ Usage : `!cours publish correction <matiere> <type> <num> <exo>`\n"
                    "Ex : `!cours publish correction an1 td 4 5`"
                )
                return
            await self._publish_correction_cmd(ctx, *rest)
            return
        if args and args[0].lower() == "enonce":
            rest = args[1:]
            if len(rest) not in (3, 4):
                await ctx.send(
                    "❌ Usage : `!cours publish enonce <matiere> <type> <num> [annee]`\n"
                    "Ex : `!cours publish enonce an1 td 2`\n"
                    "Ex : `!cours publish enonce an1 cc 3 2024-2025`"
                )
                return
            await self._publish_enonce_cmd(ctx, *rest)
            return
        if len(args) != 4:
            await ctx.send(
                "❌ Usage : `!cours publish <type> <matiere> <num> <date>`\n"
                "ou : `!cours publish correction <matiere> <type> <num> <exo>`\n"
                "ou : `!cours publish enonce <matiere> <type> <num> [annee]`"
            )
            return
        await self._publish_classic(ctx, *args)
    async def _publish_classic(self, ctx: commands.Context, type_str: str,
                               matiere_str: str, num: str, date: str):
        guild = self._get_guild()
        if guild is None:
            await ctx.send("❌ Serveur ISTIC L1 G2 introuvable.")
            return
        ok, type_code, matiere, err = self._validate_args(type_str, matiere_str, num, date)
        if not ok:
            await ctx.send(f"❌ {err}")
            return
        type_lower = type_str.lower()
        matiere_lower = matiere_str.lower()
        formatted_date = format_date(date)
        session_label = f"{type_code}{num} {matiere} ({formatted_date})"
        await ctx.send(f"🚀 Publication de **{session_label}** en cours...")
        await self._log(
            f"🚀 `!cours publish {type_str} {matiere_str} {num} {date}` lancé par {ctx.author}",
            color=LOG_COLOR_INFO,
            title="Publication lancée",
        )
        session_key = _session_key(type_code, matiere, num, date)
        audio_path = build_audio_path(type_code, matiere, num, date)
        audio_exists = audio_path is not None
        transcript_path = find_transcription(type_code, matiere, num, date)
        transcript_exists = transcript_path is not None
        status_parts = []
        if audio_exists:
            size_mb = os.path.getsize(audio_path) / (1024 * 1024)
            status_parts.append(f"🎧 Audio trouvé ({size_mb:.1f} Mo) : `{os.path.basename(audio_path)}`")
        else:
            expected = f"{type_code}{num}_{matiere}_{date}.m4a"
            status_parts.append(f"⚠️ Audio non trouvé : `{expected}` (ni variante avec espaces)")
        if transcript_exists:
            status_parts.append(f"📝 Transcription trouvée : `{os.path.basename(transcript_path)}`")
        else:
            status_parts.append(f"⚠️ Transcription non trouvée")
        await ctx.send("\n".join(status_parts))
        audio_channel = find_channel(guild, type_lower, CHANNEL_SUFFIXES["audio"], matiere_lower)
        log_fields = [
            ("Matière", matiere, True),
            ("Séance", f"{type_code}{num}", True),
            ("Date", formatted_date, True),
        ]
        if audio_exists and audio_channel:
            file_size = os.path.getsize(audio_path)
            if file_size > DISCORD_FILE_LIMIT:
                await audio_channel.send(
                    f"🎧 **{session_label}**\n"
                    f"⚠️ Fichier trop lourd ({file_size / (1024*1024):.1f} Mo), disponible sur demande."
                )
                await ctx.send("⚠️ Audio trop lourd pour Discord, message d'avertissement posté.")
                await self._log(
                    f"⚠️ Audio trop lourd ({file_size/(1024*1024):.1f} Mo) : avertissement posté dans <#{audio_channel.id}>",
                    color=LOG_COLOR_WARN,
                    fields=log_fields,
                )
            else:
                file = discord.File(audio_path, filename=os.path.basename(audio_path))
                await audio_channel.send(f"🎧 **{session_label}**", file=file)
                await ctx.send("✅ Audio posté.")
                await self._log(
                    f"🎧 Audio posté dans <#{audio_channel.id}>",
                    color=LOG_COLOR_OK,
                    fields=log_fields,
                )
                mark_published(session_key, "audio")
        elif not audio_channel:
            await ctx.send(f"⚠️ Salon audio introuvable pour `{type_lower}-audio-{matiere_lower}`")
            await self._log(
                f"⚠️ Salon audio introuvable (`{type_lower}-audio-{matiere_lower}`)",
                color=LOG_COLOR_WARN,
            )
        elif not audio_exists:
            await self._log(
                f"⚠️ Audio non trouvé pour {session_label}",
                color=LOG_COLOR_WARN,
            )
        transcript_channel = find_channel(guild, type_lower, CHANNEL_SUFFIXES["transcription"], matiere_lower)
        if transcript_exists and transcript_channel:
            file = discord.File(transcript_path, filename=os.path.basename(transcript_path))
            source_name = os.path.basename(transcript_path)
            await transcript_channel.send(
                f"📝 **{session_label}**\n"
                f"Fichier source : `{source_name}`\n"
                f"Date du cours : {formatted_date}",
                file=file,
            )
            await ctx.send("✅ Transcription postée.")
            await self._log(
                f"📝 Transcription postée dans <#{transcript_channel.id}>",
                color=LOG_COLOR_OK,
                fields=log_fields,
            )
            mark_published(session_key, "transcription")
        elif not transcript_channel:
            await ctx.send(f"⚠️ Salon transcription introuvable pour `{type_lower}-transcription-{matiere_lower}`")
            await self._log(
                f"⚠️ Salon transcription introuvable (`{type_lower}-transcription-{matiere_lower}`)",
                color=LOG_COLOR_WARN,
            )
        resume_channel = find_channel(guild, type_lower, CHANNEL_SUFFIXES["resume"], matiere_lower)
        if not transcript_exists:
            await ctx.send("⏭️ Pas de transcription → résumé impossible.")
            return
        if not resume_channel:
            await ctx.send(f"⚠️ Salon résumé introuvable pour `{type_lower}-résumé-{matiere_lower}`")
            return
        if not self._api_ok:
            await resume_channel.send(
                f"📌 **{session_label}** : Résumé en attente (ANTHROPIC_API_KEY absente)"
            )
            await ctx.send("⚠️ Résumé PENDING : API Anthropic non disponible.")
            return
        await ctx.send("🤖 Génération du résumé LaTeX via l'API Anthropic...")
        try:
            ok = await generate_and_post_latex_summary(
                ctx, resume_channel, transcript_path,
                type_code, matiere, num, date, session_label,
                log_fn=self._log,
            )
            if ok:
                mark_published(session_key, "resume")
        except subprocess.TimeoutExpired:
            await resume_channel.send(
                f"📌 **{session_label}** : Résumé en attente (timeout Claude Code)"
            )
            await ctx.send("⚠️ Résumé PENDING : Claude Code timeout.")
            await self._log(
                f"⏱️ Timeout Claude Code pour {session_label}",
                color=LOG_COLOR_WARN,
            )
        except Exception as e:
            log.error(f"Erreur résumé {session_label}: {e}")
            await ctx.send(f"❌ Erreur résumé : `{str(e)[:200]}`")
            await self._log(
                f"❌ Erreur pipeline : `{str(e)[:200]}`",
                color=LOG_COLOR_ERROR,
                title="Erreur publication",
            )
    @cours.command(name="republish")
    async def republish(self, ctx: commands.Context, type_str: str, matiere_str: str,
                        num: str, date: str):
        guild = self._get_guild()
        if guild is None:
            await ctx.send("❌ Serveur ISTIC L1 G2 introuvable.")
            return
        ok, type_code, matiere, err = self._validate_args(type_str, matiere_str, num, date)
        if not ok:
            await ctx.send(f"❌ {err}")
            return
        type_lower = type_str.lower()
        matiere_lower = matiere_str.lower()
        session_label = f"{type_code}{num} {matiere} ({format_date(date)})"
        transcript_path = find_transcription(type_code, matiere, num, date)
        if not transcript_path:
            await ctx.send(f"❌ Transcription introuvable pour {session_label}")
            return
        resume_channel = find_channel(guild, type_lower, CHANNEL_SUFFIXES["resume"], matiere_lower)
        if not resume_channel:
            await ctx.send(f"⚠️ Salon résumé introuvable.")
            return
        if not self._api_ok:
            await ctx.send("❌ ANTHROPIC_API_KEY absente : résumés désactivés.")
            return
        await ctx.send(f"🤖 Re-génération du résumé LaTeX pour **{session_label}**...")
        await self._log(
            f"🔁 `!cours republish {type_str} {matiere_str} {num} {date}` lancé par {ctx.author}",
            color=LOG_COLOR_INFO,
            title="Republication lancée",
        )
        session_key = _session_key(type_code, matiere, num, date)
        try:
            ok = await generate_and_post_latex_summary(
                ctx, resume_channel, transcript_path,
                type_code, matiere, num, date, session_label,
                log_fn=self._log,
            )
            if ok:
                mark_published(session_key, "resume")
        except Exception as e:
            log.error(f"Erreur republish {session_label}: {e}")
            await ctx.send(f"❌ Erreur : `{str(e)[:200]}`")
            await self._log(
                f"❌ Erreur pipeline : `{str(e)[:200]}`",
                color=LOG_COLOR_ERROR,
                title="Erreur republish",
            )
    async def _ensure_td_thread(self, guild: discord.Guild, matiere: str,
                                 type_code: str, num: str,
                                 annee: Optional[str],
                                 titre_td: Optional[str] = None
                                 ) -> Tuple[Optional[discord.Thread], dict, bool, dict]:
        key = thread_key(matiere, type_code, num, annee)
        data = load_discord_published_v2()
        entry = data["threads"].get(key)
        if entry:
            thread_id = entry.get("thread_id")
            thread = None
            if thread_id:
                try:
                    thread = guild.get_thread(int(thread_id))
                except (TypeError, ValueError):
                    thread = None
                if thread is None:
                    try:
                        ch = await guild.fetch_channel(int(thread_id))
                        if isinstance(ch, discord.Thread):
                            thread = ch
                    except (discord.NotFound, discord.Forbidden):
                        thread = None
                    except discord.HTTPException as e:
                        log.warning(f"_ensure_td_thread: fetch_channel échec {thread_id}: {e}")
                        thread = None
            if thread is not None:
                if getattr(thread, "archived", False):
                    try:
                        await thread.edit(archived=False)
                    except discord.HTTPException:
                        pass
                return thread, entry, False, data
            log.info(f"_ensure_td_thread: thread {thread_id} disparu : recréation (key={key})")
            data["threads"].pop(key, None)
            save_discord_published_v2(data)
        forum = find_correction_forum(guild, matiere)
        if forum is None:
            return None, {}, False, data
        enonce_path = resolve_enonce_pdf(matiere, type_code, num, annee)
        titres_canoniques = load_titres_threads()
        titre_canonique = titres_canoniques.get(key)
        effective_titre = titre_canonique if titre_canonique else titre_td
        title = build_td_thread_title(type_code, num, annee, effective_titre)
        enonce_embed = discord.Embed(
            title="📄 Énoncé",
            color=CORRECTION_EMBED_COLORS.get(matiere, LOG_COLOR_DEFAULT),
        )
        if enonce_path:
            size_mb = os.path.getsize(enonce_path) / (1024 * 1024)
            enonce_embed.description = (
                f"Énoncé disponible (pièce jointe, {size_mb:.2f} Mo).\n"
                "Les corrections des exercices suivent dans les messages ci-dessous."
            )
            initial_state = "enonce_only"
        else:
            enonce_embed.description = (
                "Énoncé non disponible sur disque.\n"
                "Les corrections des exercices suivent dans les messages ci-dessous."
            )
            initial_state = "missing_enonce"
        try:
            if enonce_path and os.path.getsize(enonce_path) <= DISCORD_FILE_LIMIT:
                file = discord.File(enonce_path, filename=os.path.basename(enonce_path))
                thread_with_msg = await forum.create_thread(
                    name=title, file=file, embed=enonce_embed,
                )
            else:
                if enonce_path:
                    log.warning(
                        f"_ensure_td_thread: énoncé trop lourd ({enonce_path}), "
                        f"thread créé sans pièce jointe"
                    )
                    enonce_embed.description = (
                        "Énoncé PDF trop lourd (>25 Mo) : disponible sur demande.\n"
                        "Les corrections des exercices suivent ci-dessous."
                    )
                    initial_state = "missing_enonce"
                thread_with_msg = await forum.create_thread(
                    name=title, embed=enonce_embed,
                )
        except discord.HTTPException as e:
            log.error(f"_ensure_td_thread: create_thread échec ({key}): {e}")
            return None, {}, False, data
        thread = thread_with_msg.thread
        first_msg = thread_with_msg.message
        enonce_md5 = None
        if enonce_path:
            try:
                enonce_md5 = await asyncio.get_event_loop().run_in_executor(
                    None, self._md5, enonce_path
                )
            except OSError:
                enonce_md5 = None
        now = _now_iso()
        entry = {
            "matiere": matiere,
            "type": type_code,
            "num": num,
            "annee": annee,
            "titre_td": titre_td,
            "thread_id": str(thread.id),
            "forum_id": str(forum.id),
            "created_at": now,
            "last_updated": now,
            "enonce": {
                "pdf_path": pdf_rel_key(enonce_path) if enonce_path else None,
                "md5": enonce_md5,
                "message_id": str(first_msg.id),
                "published_at": now,
                "status": "present" if enonce_path else "missing",
            },
            "corrections": {},
            "state": initial_state,
            "tags_applied": [],
        }
        labels = await apply_thread_tags(thread, type_code, initial_state)
        entry["tags_applied"] = labels
        data["threads"][key] = entry
        save_discord_published_v2(data)
        return thread, entry, True, data
    async def _update_thread_state(self, thread: discord.Thread,
                                    entry: dict, new_state: str) -> None:
        if entry.get("state") == new_state:
            return
        entry["state"] = new_state
        entry["last_updated"] = _now_iso()
        labels = await apply_thread_tags(thread, entry.get("type", ""), new_state)
        entry["tags_applied"] = labels
    async def _publish_enonce_into_thread(self,
                                          thread: discord.Thread,
                                          entry: dict,
                                          enonce_path: Optional[str],
                                          force_republish: bool = False
                                          ) -> dict:
        if enonce_path is None or not os.path.isfile(enonce_path):
            return {"status": "skip_no_pdf",
                    "reason": "Aucun énoncé PDF sur disque",
                    "message_id": None, "version": None, "was_new": False}
        if os.path.getsize(enonce_path) > DISCORD_FILE_LIMIT:
            size_mb = os.path.getsize(enonce_path) / (1024 * 1024)
            return {"status": "error_pdf_too_big",
                    "reason": f"PDF énoncé trop lourd ({size_mb:.1f} Mo)",
                    "message_id": None, "version": None, "was_new": False}
        md5 = await asyncio.get_event_loop().run_in_executor(
            None, self._md5, enonce_path
        )
        old_enonce = entry.get("enonce") or {}
        old_status = old_enonce.get("status")
        old_md5 = old_enonce.get("md5")
        old_msg_id = old_enonce.get("message_id")
        old_version = int(old_enonce.get("version", 1) or 1)
        if old_status == "missing" or old_enonce.get("pdf_path") is None:
            was_new = True
            new_version = 1
        else:
            if old_md5 == md5 and not force_republish:
                return {"status": "skip_same_md5",
                        "reason": "Énoncé déjà publié (même MD5)",
                        "message_id": int(old_msg_id) if old_msg_id else None,
                        "version": old_version,
                        "was_new": False}
            was_new = False
            new_version = old_version + 1
            if old_msg_id:
                try:
                    old_msg = await thread.fetch_message(int(old_msg_id))
                    await old_msg.delete()
                except discord.NotFound:
                    log.info(
                        f"_publish_enonce_into_thread: ancien msg {old_msg_id} "
                        f"absent, skip delete"
                    )
                except discord.HTTPException as e:
                    log.warning(
                        f"_publish_enonce_into_thread: delete msg {old_msg_id} "
                        f"échoué : {e}"
                    )
        ex_label = "📄 Énoncé"
        if new_version > 1:
            ex_label = f"🔄 Version {new_version} : 📄 Énoncé"
        size_mb = os.path.getsize(enonce_path) / (1024 * 1024)
        embed = discord.Embed(
            title=ex_label,
            description=(
                f"Énoncé disponible (pièce jointe, {size_mb:.2f} Mo).\n"
                "Les corrections des exercices viennent dans les "
                "messages suivants."
            ),
            color=CORRECTION_EMBED_COLORS.get(
                entry.get("matiere", ""), LOG_COLOR_DEFAULT
            ),
        )
        try:
            file = discord.File(enonce_path, filename=os.path.basename(enonce_path))
            msg = await thread.send(file=file, embed=embed)
        except discord.HTTPException as e:
            return {"status": "error_http",
                    "reason": f"thread.send échec : {str(e)[:200]}",
                    "message_id": None, "version": None, "was_new": False}
        now = _now_iso()
        entry["enonce"] = {
            "pdf_path": pdf_rel_key(enonce_path),
            "md5": md5,
            "message_id": str(msg.id),
            "published_at": now,
            "status": "present",
            "version": new_version,
        }
        entry["last_updated"] = now
        if entry.get("state") == "missing_enonce":
            has_corr = bool(entry.get("corrections", {}))
            new_state = "corrections_present" if has_corr else "enonce_only"
            await self._update_thread_state(thread, entry, new_state)
        return {
            "status": "ok" if was_new else "ok_v2",
            "reason": (
                "Énoncé attaché"
                if was_new
                else f"Énoncé mis à jour (v{new_version})"
            ),
            "message_id": msg.id,
            "version": new_version,
            "was_new": was_new,
        }
    async def _do_publish_enonce(self, guild: discord.Guild,
                                  matiere: str, type_code: str,
                                  num: str, annee: Optional[str] = None,
                                  force_republish: bool = False) -> dict:
        result: dict = {
            "status": None, "reason": "",
            "thread_id": None, "thread_url": None,
            "message_id": None, "version": None,
            "was_thread_created": False, "was_new_enonce": False,
            "annee": annee,
        }
        enonce_path = resolve_enonce_pdf(matiere, type_code, num, annee)
        if enonce_path is None:
            result["status"] = "error_no_enonce"
            result["reason"] = (
                f"Aucun énoncé PDF trouvé pour {type_code}{num} {matiere}"
                + (f" ({annee})" if annee else "")
            )
            return result
        key = thread_key(matiere, type_code, num, annee)
        data = load_discord_published_v2()
        entry_pre = data["threads"].get(key)
        thread, entry, was_created, data = await self._ensure_td_thread(
            guild, matiere, type_code, num, annee, titre_td=None,
        )
        result["was_thread_created"] = was_created
        if thread is None:
            if not find_correction_forum(guild, matiere):
                result["status"] = "error_forum_missing"
                result["reason"] = (
                    f"Forum `corrections-{matiere.lower()}` introuvable"
                )
            else:
                result["status"] = "error_http"
                result["reason"] = "Création du thread a échoué (cf. logs)"
            return result
        result["thread_id"] = thread.id
        result["thread_url"] = (
            f"https://discord.com/channels/{guild.id}/{thread.id}"
        )
        if was_created:
            enonce_meta = entry.get("enonce") or {}
            result["status"] = "ok"
            result["reason"] = "Thread créé avec énoncé"
            result["was_new_enonce"] = True
            result["version"] = enonce_meta.get("version", 1) or 1
            mid = enonce_meta.get("message_id")
            result["message_id"] = int(mid) if mid else None
            return result
        sub = await self._publish_enonce_into_thread(
            thread, entry, enonce_path, force_republish=force_republish,
        )
        data["threads"][key] = entry
        save_discord_published_v2(data)
        sub_status = sub["status"]
        result["status"] = sub_status
        result["reason"] = sub["reason"]
        result["message_id"] = sub.get("message_id")
        result["version"] = sub.get("version")
        result["was_new_enonce"] = sub.get("was_new", False)
        return result
    async def _do_publish_correction(self, guild: discord.Guild,
                                     matiere: str, type_code: str,
                                     num: str, exo: str,
                                     annee: Optional[str] = None,
                                     force_republish: bool = False) -> dict:
        exo_str = str(exo)
        result: dict = {
            "status": None, "reason": "",
            "pdf_path": None, "rel_key": None, "md5": None,
            "thread_id": None, "thread_url": None,
            "message_id": None, "forum_id": None,
            "thread_title": None, "size_mb": None,
            "version": None, "was_thread_created": False,
            "tache_found": False, "tried_paths": [], "annee": annee,
        }
        pdf_path, tried = resolve_correction_pdf(
            matiere, type_code, num, exo_str, annee=annee
        )
        result["tried_paths"] = tried
        if not pdf_path:
            result["status"] = "error_pdf_missing"
            result["reason"] = (
                f"PDF correction introuvable pour {type_code}{num} "
                f"ex{exo_str} {matiere}"
            )
            return result
        result["pdf_path"] = pdf_path
        result["size_mb"] = os.path.getsize(pdf_path) / (1024 * 1024)
        if os.path.getsize(pdf_path) > DISCORD_FILE_LIMIT:
            result["status"] = "error_pdf_too_big"
            result["reason"] = f"PDF trop lourd ({result['size_mb']:.1f} Mo)"
            return result
        tache_path = resolve_tache_md(matiere, type_code, num, exo_str)
        parsed: Optional[dict] = None
        if tache_path:
            try:
                parsed = await asyncio.get_event_loop().run_in_executor(
                    None, parse_tache_md, tache_path
                )
                result["tache_found"] = True
            except Exception as e:
                log.warning(f"parse_tache_md({tache_path}) a échoué : {e}")
                parsed = None
        if type_code.upper() == "CC" and not annee:
            pname = parse_correction_filename(pdf_path)
            if pname:
                annee = pname.get("annee")
                result["annee"] = annee
        md5 = await asyncio.get_event_loop().run_in_executor(
            None, self._md5, pdf_path
        )
        rel_key = pdf_rel_key(pdf_path)
        result["md5"] = md5
        result["rel_key"] = rel_key
        key = thread_key(matiere, type_code, num, annee)
        titre_td = (parsed or {}).get("titre_td")
        thread, entry, was_created, data = await self._ensure_td_thread(
            guild, matiere, type_code, num, annee, titre_td=titre_td
        )
        result["was_thread_created"] = was_created
        if thread is None:
            if not find_correction_forum(guild, matiere):
                result["status"] = "error_forum_missing"
                result["reason"] = f"Forum `corrections-{matiere.lower()}` introuvable"
            else:
                result["status"] = "error_http"
                result["reason"] = "Création du thread a échoué (cf. logs)"
            return result
        result["thread_id"] = thread.id
        result["forum_id"] = int(entry["forum_id"])
        result["thread_title"] = entry.get("titre_td") or build_td_thread_title(
            type_code, num, annee, titre_td
        )
        result["thread_url"] = f"https://discord.com/channels/{guild.id}/{thread.id}"
        existing_corr = entry.get("corrections", {}).get(exo_str)
        if existing_corr and existing_corr.get("md5") == md5 and not force_republish:
            result["status"] = "skip_same_md5"
            result["reason"] = "Déjà publié (même MD5)"
            result["message_id"] = existing_corr.get("message_id")
            result["version"] = existing_corr.get("version")
            return result
        if parsed:
            embed = build_correction_embed(parsed)
        else:
            embed = discord.Embed(
                color=CORRECTION_EMBED_COLORS.get(matiere, LOG_COLOR_DEFAULT),
                description="(pas de fiche TACHE associée)",
            )
        if exo_str == "0":
            ex_label = "🎯 Sujet complet"
        else:
            ex_label = f"🎯 Exercice {exo_str}"
        new_version = 1
        versions_list: List[dict] = []
        if existing_corr:
            new_version = int(existing_corr.get("version", 1)) + 1
            versions_list = list(existing_corr.get("versions") or [])
            old_msg_id = existing_corr.get("message_id")
            if old_msg_id:
                try:
                    old_msg = await thread.fetch_message(int(old_msg_id))
                    await old_msg.delete()
                except discord.NotFound:
                    log.info(f"Versioning: ancien msg {old_msg_id} déjà absent, skip delete")
                except discord.HTTPException as e:
                    log.warning(f"Versioning: échec delete msg {old_msg_id}: {e}")
        if new_version > 1:
            ex_label = f"🔄 Version {new_version} : {ex_label}"
        if embed.fields:
            first = embed.fields[0]
            embed.set_field_at(
                0, name=f"{ex_label} · {first.name}",
                value=first.value, inline=first.inline,
            )
        else:
            embed.add_field(name=ex_label, value="—", inline=False)
        try:
            file = discord.File(pdf_path, filename=os.path.basename(pdf_path))
            msg = await thread.send(file=file, embed=embed)
        except discord.HTTPException as e:
            result["status"] = "error_http"
            result["reason"] = f"thread.send échec : {str(e)[:200]}"
            return result
        except Exception as e:
            result["status"] = "error_other"
            result["reason"] = f"Exception : {str(e)[:200]}"
            return result
        result["message_id"] = msg.id
        result["version"] = new_version
        now = _now_iso()
        versions_list.append({
            "version": new_version,
            "md5": md5,
            "message_id": str(msg.id),
            "timestamp": now,
        })
        entry.setdefault("corrections", {})[exo_str] = {
            "pdf_path": rel_key,
            "md5": md5,
            "message_id": str(msg.id),
            "version": new_version,
            "published_at": now,
            "versions": versions_list,
        }
        entry["last_updated"] = now
        had_prior = any(
            k != exo_str for k in entry["corrections"].keys()
        ) or bool(existing_corr)
        if not had_prior:
            await self._update_thread_state(thread, entry, "corrections_present")
        data["threads"][key] = entry
        save_discord_published_v2(data)
        result["status"] = "ok_v2" if new_version > 1 else "ok"
        result["reason"] = f"Publié (v{new_version})"
        return result
    async def _publish_correction_cmd(self, ctx: commands.Context,
                                      matiere_str: str, type_str: str,
                                      num: str, exo: str):
        guild = self._get_guild()
        if guild is None:
            await ctx.send("❌ Serveur ISTIC L1 G2 introuvable.")
            return
        matiere_lower = matiere_str.lower()
        type_lower = type_str.lower()
        if matiere_lower not in MATIERE_MAP:
            await ctx.send(
                f"❌ Matière invalide `{matiere_str}`. "
                f"Valeurs : {', '.join(MATIERE_MAP.keys())}"
            )
            return
        allowed_types = {"td": "TD", "tp": "TP", "cc": "CC", "quiz": "quiz"}
        if type_lower not in allowed_types:
            await ctx.send(
                f"❌ Type invalide `{type_str}`. "
                f"Valeurs : {', '.join(allowed_types.keys())}"
            )
            return
        matiere = MATIERE_MAP[matiere_lower]
        type_code = allowed_types[type_lower]
        exo_str = str(exo)
        await self._log(
            f"🚀 `!cours publish correction {matiere_lower} {type_lower} "
            f"{num} {exo_str}` lancé par {ctx.author}",
            color=LOG_COLOR_INFO,
            title="Publication correction",
        )
        r = await self._do_publish_correction(
            guild, matiere, type_code, num, exo_str
        )
        await self._format_publish_result(ctx, r)
    async def _format_publish_result(self, ctx: commands.Context, r: dict):
        status = r["status"]
        thread_url = r.get("thread_url")
        if status in ("ok", "ok_v2"):
            prefix = "✅" if status == "ok" else "🔄"
            verb = "publiée" if status == "ok" else f"mise à jour (v{r['version']})"
            created_note = " (nouveau thread)" if r.get("was_thread_created") else ""
            await ctx.send(
                f"{prefix} Correction {verb}{created_note} : "
                f"**{r['thread_title']}** ({r['size_mb']:.2f} Mo)\n{thread_url}"
            )
            await self._log(
                f"{prefix} Correction {verb}{created_note}\n"
                f"Fichier : `{r['rel_key']}` ({r['size_mb']:.2f} Mo)\n"
                f"Thread : {thread_url}",
                color=LOG_COLOR_OK,
                title="Correction publiée",
            )
        elif status == "skip_same_md5":
            await ctx.send(
                f"ℹ️ Correction déjà publiée (même MD5) : skip.\n{thread_url or ''}"
            )
            await self._log(
                f"ℹ️ Correction déjà publiée (même MD5) : `{r['rel_key']}`\n"
                f"Thread : {thread_url or '(inconnu)'}",
                color=LOG_COLOR_INFO,
            )
        elif status == "error_pdf_missing":
            tried_str = "\n".join(
                f"• `{os.path.relpath(p, COURS_ROOT)}`" for p in r["tried_paths"]
            ) or "(aucun)"
            await ctx.send(
                f"❌ {r['reason']}.\nPatterns testés :\n{tried_str}"
            )
            await self._log(
                f"❌ {r['reason']}",
                color=LOG_COLOR_ERROR,
                title="Publication correction",
            )
        elif status == "error_forum_missing":
            await ctx.send(
                f"❌ {r['reason']}. Lance `!cours setup-forums` d'abord."
            )
            await self._log(
                f"❌ {r['reason']} : lancer `!cours setup-forums`",
                color=LOG_COLOR_ERROR,
            )
        elif status == "error_pdf_too_big":
            await ctx.send(f"❌ {r['reason']} : limite Discord 25 Mo.")
        else:
            await ctx.send(f"❌ {r['reason']}")
            await self._log(
                f"❌ Publication correction échouée : `{r['reason']}`",
                color=LOG_COLOR_ERROR,
            )
    async def _publish_enonce_cmd(self, ctx: commands.Context,
                                   matiere_str: str, type_str: str,
                                   num: str,
                                   annee_str: Optional[str] = None):
        guild = self._get_guild()
        if guild is None:
            await ctx.send("❌ Serveur ISTIC L1 G2 introuvable.")
            return
        matiere_lower = matiere_str.lower()
        type_lower = type_str.lower()
        if matiere_lower not in MATIERE_MAP:
            await ctx.send(
                f"❌ Matière invalide `{matiere_str}`. "
                f"Valeurs : {', '.join(MATIERE_MAP.keys())}"
            )
            return
        allowed_types = {"td": "TD", "tp": "TP", "cc": "CC", "quiz": "quiz"}
        if type_lower not in allowed_types:
            await ctx.send(
                f"❌ Type invalide `{type_str}`. "
                f"Valeurs : {', '.join(allowed_types.keys())}"
            )
            return
        matiere = MATIERE_MAP[matiere_lower]
        type_code = allowed_types[type_lower]
        await self._log(
            f"📄 `!cours publish enonce {matiere_lower} {type_lower} {num}"
            + (f" {annee_str}" if annee_str else "")
            + f"` lancé par {ctx.author}",
            color=LOG_COLOR_INFO,
            title="Publication énoncé",
        )
        r = await self._do_publish_enonce(
            guild, matiere, type_code, num, annee=annee_str,
        )
        await self._format_enonce_result(ctx, r)
    async def _format_enonce_result(self, ctx: commands.Context, r: dict):
        status = r.get("status")
        thread_url = r.get("thread_url") or ""
        if status == "ok":
            if r.get("was_thread_created"):
                emoji, verb = "✅", "Thread créé avec énoncé"
            elif r.get("was_new_enonce"):
                emoji, verb = "📄", "Énoncé ajouté au thread existant"
            else:
                emoji, verb = "ℹ️", "Énoncé déjà à jour (re-validé)"
            await ctx.send(f"{emoji} {verb}\n{thread_url}")
            await self._log(
                f"{emoji} {verb}\nThread : {thread_url}",
                color=LOG_COLOR_OK, title="Énoncé publié",
            )
        elif status == "ok_v2":
            await ctx.send(
                f"🔄 Énoncé mis à jour (v{r['version']}) : "
                f"ancien post supprimé + nouveau\n{thread_url}"
            )
            await self._log(
                f"🔄 Énoncé mis à jour v{r['version']}\nThread : {thread_url}",
                color=LOG_COLOR_OK, title="Énoncé mis à jour",
            )
        elif status == "skip_same_md5":
            await ctx.send(
                f"ℹ️ Énoncé déjà publié (même MD5) : skip.\n{thread_url}"
            )
        elif status == "skip_no_pdf":
            await ctx.send(
                f"⚠️ {r.get('reason', 'Aucun PDF d énoncé trouvé.')}"
            )
        elif status == "error_no_enonce":
            await ctx.send(
                f"❌ {r.get('reason')}.\n"
                "Vérifie que `enonce_TD{n}_{MAT}.pdf` (ou variante) "
                "existe sur disque."
            )
            await self._log(
                f"❌ Énoncé introuvable : {r.get('reason')}",
                color=LOG_COLOR_ERROR, title="Publication énoncé",
            )
        elif status == "error_pdf_too_big":
            await ctx.send(f"❌ {r.get('reason')} : limite Discord 25 Mo.")
        elif status == "error_forum_missing":
            await ctx.send(
                f"❌ {r.get('reason')}. Lance `!cours setup-forums` d'abord."
            )
        else:
            await ctx.send(f"❌ {r.get('reason', 'Erreur inconnue.')}")
            await self._log(
                f"❌ Publication énoncé échouée : `{r.get('reason')}`",
                color=LOG_COLOR_ERROR, title="Publication énoncé",
            )
    _PERSO_KIND_ORDER = {
        "tache": 1, "script": 2, "script_print": 3,
        "slides": 4, "slides_src": 5, "video": 6,
    }
    async def _apply_perso_thread_tags(self,
                                        thread: "discord.Thread",
                                        type_code: str,
                                        materiel_kinds: set) -> List[str]:
        forum = thread.parent
        if not isinstance(forum, discord.ForumChannel):
            return []
        target_labels: List[str] = []
        type_key = (type_code.lower() if type_code.lower() == "quiz"
                    else type_code.upper())
        type_tuple = PERSO_TAG_LABELS_TYPE.get(type_key)
        if type_tuple:
            target_labels.append(type_tuple[0])
        for kind in materiel_kinds:
            if kind == "slides_src":
                normalized = "slides"
            elif kind == "script_print":
                normalized = "script"
            else:
                normalized = kind
            mat_tuple = PERSO_TAG_LABELS_MATERIEL.get(normalized)
            if mat_tuple and mat_tuple[0] not in target_labels:
                target_labels.append(mat_tuple[0])
        target_tags: List[discord.ForumTag] = []
        for label in target_labels:
            t = get_forum_tag(forum, label)
            if t is not None:
                target_tags.append(t)
        try:
            await thread.edit(applied_tags=target_tags)
        except discord.HTTPException as e:
            log.warning(f"_apply_perso_thread_tags {thread.id}: {e}")
        return [t.name for t in target_tags]
    async def _ensure_perso_thread(self, guild: discord.Guild,
                                    matiere: str, type_code: str, num: str,
                                    annee: Optional[str] = None
                                    ) -> Tuple[Optional["discord.Thread"],
                                               dict, bool, dict]:
        forum = find_perso_forum(guild, matiere)
        if forum is None:
            return None, {}, False, load_discord_perso_published()
        data = load_discord_perso_published()
        key = thread_key(matiere, type_code, num, annee)
        entry = data["threads"].get(key)
        thread = None
        if entry and entry.get("thread_id"):
            try:
                thread = guild.get_thread(int(entry["thread_id"]))
            except (TypeError, ValueError):
                thread = None
            if thread is None:
                try:
                    ch = await guild.fetch_channel(int(entry["thread_id"]))
                    if isinstance(ch, discord.Thread):
                        thread = ch
                except (discord.NotFound, discord.Forbidden):
                    thread = None
                except discord.HTTPException as e:
                    log.warning(
                        f"_ensure_perso_thread fetch_channel "
                        f"{entry['thread_id']}: {e}"
                    )
                    thread = None
            if thread is not None:
                if getattr(thread, "archived", False):
                    try:
                        await thread.edit(archived=False)
                    except discord.HTTPException:
                        pass
                return thread, entry, False, data
            log.info(
                f"_ensure_perso_thread: thread {entry['thread_id']} "
                f"disparu : recréation (key={key})"
            )
            data["threads"].pop(key, None)
            save_discord_perso_published(data)
            entry = None
        titres = load_titres_threads()
        titre_canon = titres.get(key, "")
        title = build_td_thread_title(type_code, num, annee, titre_canon)
        intro_embed = discord.Embed(
            title="🔒 Travail personnel",
            description=(
                f"Thread privé pour `{key}` : matériel personnel "
                f"(TACHEs, scripts oraux, slides, vidéos).\n"
                "Visible uniquement par le rôle admin."
            ),
            color=0x36393F,
        )
        try:
            thread_with_msg = await forum.create_thread(
                name=title, embed=intro_embed,
            )
        except discord.HTTPException as e:
            log.error(f"_ensure_perso_thread create_thread {key}: {e}")
            return None, {}, False, data
        thread = thread_with_msg.thread
        now = _now_iso()
        entry = {
            "matiere": matiere,
            "type": type_code,
            "num": num,
            "annee": annee,
            "thread_id": str(thread.id),
            "forum_id": str(forum.id),
            "title": title,
            "posts": {},
            "tags_applied": [],
            "created_at": now,
            "last_updated": now,
        }
        data["threads"][key] = entry
        save_discord_perso_published(data)
        return thread, entry, True, data
    async def _publish_perso_post(self,
                                   thread: "discord.Thread",
                                   entry: dict, item: dict,
                                   force_republish: bool = False) -> dict:
        post_key = item["post_key"]
        file_path = item["file_path"]
        if not os.path.isfile(file_path):
            return {"status": "skip_no_file", "reason": "fichier disparu",
                    "message_id": None, "version": None}
        size_bytes = item["size_bytes"]
        try:
            md5 = await asyncio.get_event_loop().run_in_executor(
                None, self._md5, file_path
            )
        except OSError as e:
            return {"status": "error_other",
                    "reason": f"MD5 illisible : {e}",
                    "message_id": None, "version": None}
        existing = entry.get("posts", {}).get(post_key)
        if existing and existing.get("md5") == md5 and not force_republish:
            return {
                "status": "skip_same_md5",
                "reason": "déjà publié (même MD5)",
                "message_id": int(existing["message_id"]) if existing.get("message_id") else None,
                "version": existing.get("version", 1),
            }
        new_version = 1
        if existing:
            new_version = int(existing.get("version", 1)) + 1
            old_msg_id = existing.get("message_id")
            if old_msg_id:
                try:
                    old_msg = await thread.fetch_message(int(old_msg_id))
                    await old_msg.delete()
                except (discord.NotFound, discord.Forbidden):
                    pass
                except discord.HTTPException as e:
                    log.warning(
                        f"_publish_perso_post: delete old msg "
                        f"{old_msg_id} échoué : {e}"
                    )
        kind = item["kind"]
        kind_label = {
            "tache":        ("📋", "TACHE",             "Briefing de correction"),
            "script":       ("📝", "Script oral",       "Texte à lire pendant l'enregistrement"),
            "script_print": ("📄", "Script imprimable", "Version PDF du script (LaTeX rendu, impression N&B)"),
            "slides":       ("📊", "Slides",            "Présentation Beamer compilée"),
            "slides_src":   ("📐", "Slides source",     "Source LaTeX/Beamer"),
            "video":        ("🎬", "Vidéo",             "Enregistrement"),
        }
        emoji, label, descr = kind_label.get(
            kind, ("📎", kind.title(), "Fichier perso"),
        )
        title = f"{emoji} {label}"
        if new_version > 1:
            title = f"🔄 v{new_version} : {title}"
        if item.get("exo") and item["exo"] != "0":
            title += f" : Exercice {item['exo']}"
        size_mb = size_bytes / (1024 * 1024)
        embed = discord.Embed(
            title=title,
            description=f"{descr}\nFichier : `{item['rel_key']}` ({size_mb:.2f} Mo)",
            color=0x5865F2,
        )
        embed.set_footer(text=f"Pipeline COURS · perso · v{new_version}")
        is_video = (kind == "video")
        too_big = size_bytes > DISCORD_FILE_LIMIT
        if is_video and too_big:
            embed.add_field(
                name="⚠️ Trop lourd pour Discord",
                value=(
                    f"Taille {size_mb:.0f} Mo > limite Discord 25 Mo.\n"
                    f"Conservée localement : `{item['rel_key']}`"
                ),
                inline=False,
            )
            try:
                msg = await thread.send(embed=embed)
            except discord.HTTPException as e:
                return {"status": "error_http", "reason": str(e)[:200],
                        "message_id": None, "version": None}
            entry.setdefault("posts", {})[post_key] = {
                "kind": kind,
                "rel_key": item["rel_key"],
                "md5": md5,
                "size_mb": round(size_mb, 1),
                "is_too_big": True,
                "message_id": str(msg.id),
                "version": new_version,
                "published_at": _now_iso(),
            }
            return {
                "status": "ok" if new_version == 1 else "ok_v2",
                "reason": "vidéo trop lourde : mention seule postée",
                "message_id": msg.id,
                "version": new_version,
            }
        if too_big:
            return {
                "status": "skip_too_big",
                "reason": f"Taille {size_mb:.0f} Mo > 25 Mo, fichier non publié",
                "message_id": None, "version": None,
            }
        try:
            file = discord.File(file_path,
                                filename=os.path.basename(file_path))
            msg = await thread.send(file=file, embed=embed)
        except discord.HTTPException as e:
            return {"status": "error_http", "reason": str(e)[:200],
                    "message_id": None, "version": None}
        entry.setdefault("posts", {})[post_key] = {
            "kind": kind,
            "rel_key": item["rel_key"],
            "md5": md5,
            "size_mb": round(size_mb, 2),
            "is_too_big": False,
            "message_id": str(msg.id),
            "version": new_version,
            "published_at": _now_iso(),
        }
        return {
            "status": "ok" if new_version == 1 else "ok_v2",
            "reason": "publié",
            "message_id": msg.id,
            "version": new_version,
        }
    async def _do_publish_perso(self, guild: discord.Guild,
                                 matiere: str, type_code: str, num: str,
                                 annee: Optional[str] = None,
                                 force_republish: bool = False) -> dict:
        all_items = list_perso_material(matiere)
        target_key = thread_key(matiere, type_code, num, annee)
        items = [i for i in all_items if i["thread_key"] == target_key]
        if not items:
            return {
                "status": "no_material",
                "reason": "Aucun matériel personnel détecté pour ce TD/TP/CC.",
                "thread_id": None, "thread_url": None,
                "was_thread_created": False,
                "ok": 0, "ok_v2": 0, "skip_same_md5": 0,
                "skip_no_file": 0, "skip_too_big": 0, "errors": 0,
                "details": [],
            }
        def _sort_key(it):
            order = self._PERSO_KIND_ORDER.get(it["kind"], 99)
            try:
                exo_n = int(it["exo"]) if it.get("exo") else 0
            except ValueError:
                exo_n = 0
            return (order, exo_n, it["post_key"])
        items.sort(key=_sort_key)
        thread, entry, was_created, data = await self._ensure_perso_thread(
            guild, matiere, type_code, num, annee
        )
        if thread is None:
            return {
                "status": "error_forum_missing",
                "reason": (
                    f"Forum `{perso_forum_name(matiere)}` introuvable. "
                    "Lance `!cours setup-perso` d'abord."
                ),
                "thread_id": None, "thread_url": None,
                "was_thread_created": False,
                "ok": 0, "ok_v2": 0, "skip_same_md5": 0,
                "skip_no_file": 0, "skip_too_big": 0, "errors": 0,
                "details": [],
            }
        counts = {
            "ok": 0, "ok_v2": 0, "skip_same_md5": 0,
            "skip_no_file": 0, "skip_too_big": 0, "errors": 0,
        }
        details: List[dict] = []
        kinds_present: set = set()
        for item in items:
            try:
                r = await self._publish_perso_post(
                    thread, entry, item, force_republish=force_republish,
                )
            except Exception as e:
                log.error(
                    f"_publish_perso_post {item['post_key']}: {e}",
                    exc_info=True,
                )
                counts["errors"] += 1
                details.append({"post_key": item["post_key"],
                                "status": "error_exception"})
                continue
            status = r.get("status", "error_unknown")
            if status in counts:
                counts[status] += 1
            elif status.startswith("error_"):
                counts["errors"] += 1
            details.append({
                "post_key": item["post_key"], "status": status,
                "version": r.get("version"),
            })
            if status in ("ok", "ok_v2", "skip_same_md5"):
                kinds_present.add(item["kind"])
            await asyncio.sleep(2)
        for pk in entry.get("posts", {}):
            existing_kind = entry["posts"][pk].get("kind")
            if existing_kind:
                kinds_present.add(existing_kind)
        labels = await self._apply_perso_thread_tags(
            thread, type_code, kinds_present
        )
        entry["tags_applied"] = labels
        entry["last_updated"] = _now_iso()
        data["threads"][target_key] = entry
        save_discord_perso_published(data)
        return {
            "status": "ok",
            "thread_id": thread.id,
            "thread_url": (
                f"https://discord.com/channels/{guild.id}/{thread.id}"
            ),
            "was_thread_created": was_created,
            "thread_title": entry.get("title"),
            **counts,
            "details": details,
        }
    BACKFILL_SLEEP_SECONDS = 15
    BACKFILL_CONFIRM_TIMEOUT = 60
    BACKFILL_PROGRESS_EVERY = 10
    @cours.command(name="backfill")
    async def backfill(self, ctx: commands.Context, matiere_str: Optional[str] = None):
        guild = self._get_guild()
        if guild is None:
            await ctx.send("❌ Serveur ISTIC L1 G2 introuvable.")
            return
        if matiere_str is None or matiere_str.lower() not in MATIERE_MAP:
            await ctx.send(
                "❌ Usage : `!cours backfill <matiere>`\n"
                f"Matières : {', '.join(MATIERE_MAP.keys())}"
            )
            return
        matiere = MATIERE_MAP[matiere_str.lower()]
        forum = find_correction_forum(guild, matiere)
        if forum is None:
            await ctx.send(
                f"❌ Forum `corrections-{matiere.lower()}` introuvable. "
                f"Lance `!cours setup-forums` d'abord."
            )
            return
        await self._log(
            f"📦 `!cours backfill {matiere_str.lower()}` lancé par {ctx.author}",
            color=LOG_COLOR_INFO, title="Backfill lancé",
        )
        scan_msg = await ctx.send(f"🔍 Scan de `COURS/{matiere}/**/corrections/*.pdf` …")
        pattern = os.path.join(COURS_ROOT, matiere, "**", "corrections", "*.pdf")
        all_pdfs = sorted(
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: glob.glob(pattern, recursive=True)
            )
        )
        try:
            await scan_msg.delete()
        except discord.HTTPException:
            pass
        if not all_pdfs:
            await ctx.send(f"ℹ️ Aucun PDF correction trouvé sous `COURS/{matiere}/`.")
            return
        groups: Dict[str, dict] = {}
        unparseable: List[str] = []
        for pdf in all_pdfs:
            rel = pdf_rel_key(pdf)
            pn = parse_correction_filename(pdf)
            if pn is None:
                unparseable.append(rel)
                continue
            key = thread_key(matiere, pn["type_code"], pn["num"], pn.get("annee"))
            g = groups.setdefault(key, {
                "type_code": pn["type_code"],
                "num": pn["num"],
                "annee": pn.get("annee"),
                "items": [],
            })
            g["items"].append({
                "pdf": pdf,
                "rel": rel,
                "exo": pn["exo"],
                "parsed": pn,
            })
        for g in groups.values():
            g["items"].sort(key=lambda it: int(it["exo"]) if it["exo"].isdigit() else 0)
        tracking = load_discord_published_v2()
        threads_to_create: List[str] = []
        threads_partial: List[Tuple[str, int]] = []
        threads_full_sync: List[str] = []
        corrections_to_publish = 0
        already_md5_count = 0
        def _md5_sync(p: str) -> str:
            return self._md5(p)
        for key, g in groups.items():
            entry = tracking["threads"].get(key)
            new_items = 0
            total_items = len(g["items"])
            if entry is None:
                new_items = total_items
                threads_to_create.append(key)
            else:
                existing_corr = entry.get("corrections", {})
                for it in g["items"]:
                    old = existing_corr.get(it["exo"])
                    if old is None:
                        new_items += 1
                        continue
                    try:
                        md5 = await asyncio.get_event_loop().run_in_executor(
                            None, _md5_sync, it["pdf"]
                        )
                    except OSError:
                        unparseable.append(f"{it['rel']} (lecture MD5 échouée)")
                        continue
                    if old.get("md5") == md5:
                        already_md5_count += 1
                    else:
                        new_items += 1
                if new_items > 0:
                    threads_partial.append((key, new_items))
                else:
                    threads_full_sync.append(key)
            corrections_to_publish += new_items
        eta_seconds = corrections_to_publish * self.BACKFILL_SLEEP_SECONDS
        eta_mins = eta_seconds // 60
        eta_secs = eta_seconds % 60
        embed = discord.Embed(
            title=f"📦 Backfill {matiere} : Aperçu",
            color=LOG_COLOR_INFO,
        )
        embed.add_field(
            name="Résumé",
            value=(
                f"📊 PDFs scannés : **{len(all_pdfs)}**\n"
                f"🧵 Threads à créer : **{len(threads_to_create)}**\n"
                f"➕ Threads à compléter : **{len(threads_partial)}**\n"
                f"⏭️ Threads déjà synchronisés : **{len(threads_full_sync)}**\n"
                f"✍️ Corrections à publier : **{corrections_to_publish}**\n"
                f"⏭️ Corrections déjà publiées (même MD5) : **{already_md5_count}**\n"
                f"❌ Nom PDF non reconnu : **{len(unparseable)}**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Cible",
            value=(
                f"Forum : <#{forum.id}>\n"
                f"Durée estimée : ~{eta_mins} min {eta_secs:02d} s "
                f"({self.BACKFILL_SLEEP_SECONDS}s entre corrections)"
            ),
            inline=False,
        )
        if threads_to_create:
            preview = "\n".join(f"• `{k}`" for k in threads_to_create[:5])
            if len(threads_to_create) > 5:
                preview += f"\n… (+{len(threads_to_create) - 5} autres)"
            embed.add_field(name="Premiers threads à créer", value=preview, inline=False)
        if threads_partial:
            part_preview = "\n".join(
                f"• `{k}` (+{n})" for k, n in threads_partial[:5]
            )
            if len(threads_partial) > 5:
                part_preview += f"\n… (+{len(threads_partial) - 5} autres)"
            embed.add_field(name="Threads à compléter", value=part_preview, inline=False)
        if unparseable:
            un_list = "\n".join(f"• `{u}`" for u in unparseable[:10])
            if len(unparseable) > 10:
                un_list += f"\n… (+{len(unparseable) - 10} autres)"
            embed.add_field(name="⚠️ Noms non reconnus", value=un_list, inline=False)
        if corrections_to_publish == 0:
            embed.set_footer(text="Rien à publier : tout est déjà synchronisé.")
            await ctx.send(embed=embed)
            await self._log(
                f"📦 Backfill {matiere} : rien à publier "
                f"({already_md5_count} corrections déjà synchro, "
                f"{len(unparseable)} noms non reconnus)",
                color=LOG_COLOR_OK, title="Backfill terminé",
            )
            return
        embed.set_footer(text="Réagis avec ✅ pour lancer, ❌ pour annuler (60 sec).")
        prompt_msg = await ctx.send(embed=embed)
        try:
            await prompt_msg.add_reaction("✅")
            await prompt_msg.add_reaction("❌")
        except discord.HTTPException:
            pass
        def check_react(reaction: discord.Reaction, user) -> bool:
            return (
                user == ctx.author
                and reaction.message.id == prompt_msg.id
                and str(reaction.emoji) in ("✅", "❌")
            )
        try:
            reaction, _user = await self.bot.wait_for(
                "reaction_add", check=check_react,
                timeout=self.BACKFILL_CONFIRM_TIMEOUT,
            )
        except asyncio.TimeoutError:
            await ctx.send("⏱️ Timeout : backfill annulé.")
            return
        if str(reaction.emoji) == "❌":
            await ctx.send("❌ Backfill annulé.")
            return
        await ctx.send(
            f"🚀 Backfill **{matiere}** démarré : {corrections_to_publish} "
            f"corrections réparties sur {len(groups)} thread(s) "
            f"(~{eta_mins} min)."
        )
        await self._log(
            f"🚀 Backfill {matiere} démarré : "
            f"{corrections_to_publish} corrections, "
            f"{len(threads_to_create)} threads à créer",
            color=LOG_COLOR_INFO, title="Backfill démarré",
        )
        start_ts = time.monotonic()
        published = 0
        skipped_runtime = 0
        threads_created = 0
        errors: List[Tuple[str, str]] = []
        total_to_publish = corrections_to_publish
        action_count = 0
        for key in sorted(groups.keys()):
            g = groups[key]
            for it in g["items"]:
                rel = it["rel"]
                try:
                    r = await self._do_publish_correction(
                        guild, matiere,
                        g["type_code"], g["num"], it["exo"],
                        annee=g["annee"],
                    )
                except Exception as e:
                    log.error(f"Backfill: exception sur {rel}: {e}")
                    errors.append((rel, f"exception: {str(e)[:150]}"))
                    await self._log(
                        f"❌ Backfill {matiere} : exception sur `{rel}`, `{str(e)[:150]}`",
                        color=LOG_COLOR_ERROR,
                    )
                    continue
                status = r["status"]
                if status in ("ok", "ok_v2"):
                    published += 1
                    if r.get("was_thread_created"):
                        threads_created += 1
                    action_count += 1
                elif status.startswith("skip_"):
                    skipped_runtime += 1
                    continue
                else:
                    errors.append((rel, r["reason"]))
                    await self._log(
                        f"❌ Backfill {matiere} : `{rel}` → {r['reason']}",
                        color=LOG_COLOR_ERROR,
                    )
                if published > 0 and published % self.BACKFILL_PROGRESS_EVERY == 0:
                    elapsed = time.monotonic() - start_ts
                    remaining = max(0, total_to_publish - published)
                    eta_left = remaining * self.BACKFILL_SLEEP_SECONDS
                    await self._log(
                        f"⏳ Backfill {matiere} : {published}/{total_to_publish} "
                        f"corrections publiées ({threads_created} threads créés, "
                        f"{int(elapsed // 60)} min écoulées, reste ~{int(eta_left // 60)} min)",
                        color=LOG_COLOR_INFO, title=f"Progression {matiere}",
                    )
                if action_count < total_to_publish:
                    await asyncio.sleep(self.BACKFILL_SLEEP_SECONDS)
        total_elapsed = time.monotonic() - start_ts
        mins = int(total_elapsed // 60)
        secs = int(total_elapsed % 60)
        summary_color = LOG_COLOR_OK if not errors else LOG_COLOR_WARN
        final = discord.Embed(
            title=f"✅ Backfill {matiere} terminé",
            color=summary_color,
        )
        final.add_field(name="Corrections publiées", value=str(published), inline=True)
        final.add_field(name="Threads créés", value=str(threads_created), inline=True)
        final.add_field(
            name="Skips runtime",
            value=str(skipped_runtime),
            inline=True,
        )
        final.add_field(name="Erreurs", value=str(len(errors)), inline=True)
        final.add_field(
            name="Durée totale",
            value=f"{mins} min {secs:02d} s", inline=False,
        )
        if errors:
            err_list = "\n".join(f"• `{p}` : {r}" for p, r in errors[:8])
            if len(errors) > 8:
                err_list += f"\n… (+{len(errors) - 8} autres)"
            final.add_field(name="Détails erreurs", value=err_list, inline=False)
        await ctx.send(embed=final)
        await self._log(
            f"✅ Backfill {matiere} terminé : {published} publiées, "
            f"{threads_created} threads créés, "
            f"{len(errors)} erreur(s), {mins} min {secs:02d} s",
            color=summary_color, title="Backfill terminé",
        )
    _ENONCE_SCAN_EXCLUDE = (
        os.sep + "_archives" + os.sep,
        os.sep + "_INBOX" + os.sep,
        os.sep + "_A_TRIER" + os.sep,
        os.sep + "_A_VALIDER" + os.sep,
        os.sep + "_temp_latex" + os.sep,
        os.sep + "corrections" + os.sep,
        os.sep + "transcriptions" + os.sep,
        os.sep + "scripts_oraux" + os.sep,
    )
    @cours.command(name="backfill-enonces", aliases=["backfillenonces"])
    async def backfill_enonces(self, ctx: commands.Context,
                                matiere_str: Optional[str] = None):
        guild = self._get_guild()
        if guild is None:
            await ctx.send("❌ Serveur ISTIC L1 G2 introuvable.")
            return
        if matiere_str is None or matiere_str.lower() not in MATIERE_MAP:
            await ctx.send(
                "❌ Usage : `!cours backfill-enonces <matiere>`\n"
                f"Matières : {', '.join(MATIERE_MAP.keys())}"
            )
            return
        matiere = MATIERE_MAP[matiere_str.lower()]
        forum = find_correction_forum(guild, matiere)
        if forum is None:
            await ctx.send(
                f"❌ Forum `corrections-{matiere.lower()}` introuvable. "
                f"Lance `!cours setup-forums` d'abord."
            )
            return
        await self._log(
            f"📦 `!cours backfill-enonces {matiere_str.lower()}` "
            f"lancé par {ctx.author}",
            color=LOG_COLOR_INFO, title="Backfill énoncés lancé",
        )
        scan_msg = await ctx.send(
            f"🔍 Scan de `COURS/{matiere}/**/enonce_*.pdf` …"
        )
        pattern = os.path.join(COURS_ROOT, matiere, "**", "enonce_*.pdf")
        all_pdfs_raw = await asyncio.get_event_loop().run_in_executor(
            None, lambda: glob.glob(pattern, recursive=True)
        )
        all_pdfs = sorted(
            p for p in all_pdfs_raw
            if not any(excl in p for excl in self._ENONCE_SCAN_EXCLUDE)
        )
        try:
            await scan_msg.delete()
        except discord.HTTPException:
            pass
        if not all_pdfs:
            await ctx.send(
                f"ℹ️ Aucun énoncé PDF trouvé sous `COURS/{matiere}/`."
            )
            return
        tracking = load_discord_published_v2()
        to_create: List[Tuple[str, str, str, Optional[str]]] = []
        to_attach: List[Tuple[str, str, str, Optional[str]]] = []
        to_update: List[Tuple[str, str, str, Optional[str]]] = []
        already_sync: List[str] = []
        unparseable: List[str] = []
        for pdf in all_pdfs:
            rel = pdf_rel_key(pdf)
            pn = parse_enonce_filename(pdf)
            if pn is None:
                unparseable.append(rel)
                continue
            type_code = pn["type_code"]
            num = pn["num"]
            annee = pn.get("annee")
            key = thread_key(matiere, type_code, num, annee)
            entry = tracking["threads"].get(key)
            tup = (key, type_code, num, annee)
            if entry is None:
                to_create.append(tup)
                continue
            enonce_meta = entry.get("enonce") or {}
            if enonce_meta.get("status") == "missing" or enonce_meta.get("pdf_path") is None:
                to_attach.append(tup)
                continue
            try:
                md5 = await asyncio.get_event_loop().run_in_executor(
                    None, self._md5, pdf
                )
            except OSError:
                unparseable.append(f"{rel} (lecture MD5 échouée)")
                continue
            if enonce_meta.get("md5") == md5:
                already_sync.append(key)
            else:
                to_update.append(tup)
        actions = to_create + to_attach + to_update
        actions_count = len(actions)
        eta_seconds = actions_count * self.BACKFILL_SLEEP_SECONDS
        eta_mins = eta_seconds // 60
        eta_secs = eta_seconds % 60
        embed = discord.Embed(
            title=f"📦 Backfill énoncés {matiere} : Aperçu",
            color=LOG_COLOR_INFO,
        )
        embed.add_field(
            name="Résumé",
            value=(
                f"📊 Énoncés scannés : **{len(all_pdfs)}**\n"
                f"🆕 Threads à créer (énoncé inclus) : **{len(to_create)}**\n"
                f"➕ Énoncés manquants à attacher : **{len(to_attach)}**\n"
                f"🔄 Énoncés à mettre à jour (MD5 différent) : **{len(to_update)}**\n"
                f"⏭️ Déjà synchronisés : **{len(already_sync)}**\n"
                f"❌ Noms non reconnus : **{len(unparseable)}**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Cible",
            value=(
                f"Forum : <#{forum.id}>\n"
                f"Durée estimée : ~{eta_mins} min {eta_secs:02d} s "
                f"({self.BACKFILL_SLEEP_SECONDS}s entre publications)"
            ),
            inline=False,
        )
        if to_create:
            preview = "\n".join(f"• `{k}` (créer)" for k, _, _, _ in to_create[:5])
            if len(to_create) > 5:
                preview += f"\n… (+{len(to_create) - 5} autres)"
            embed.add_field(name="Premiers à créer", value=preview, inline=False)
        if to_attach:
            preview = "\n".join(f"• `{k}` (attacher)" for k, _, _, _ in to_attach[:5])
            if len(to_attach) > 5:
                preview += f"\n… (+{len(to_attach) - 5} autres)"
            embed.add_field(name="À attacher", value=preview, inline=False)
        if to_update:
            preview = "\n".join(f"• `{k}` (v→v+1)" for k, _, _, _ in to_update[:5])
            if len(to_update) > 5:
                preview += f"\n… (+{len(to_update) - 5} autres)"
            embed.add_field(name="À mettre à jour", value=preview, inline=False)
        if unparseable:
            un_list = "\n".join(f"• `{u}`" for u in unparseable[:10])
            if len(unparseable) > 10:
                un_list += f"\n… (+{len(unparseable) - 10} autres)"
            embed.add_field(name="⚠️ Noms non reconnus", value=un_list, inline=False)
        if actions_count == 0:
            embed.set_footer(text="Rien à publier : tout est déjà synchronisé.")
            await ctx.send(embed=embed)
            await self._log(
                f"📦 Backfill énoncés {matiere} : rien à publier "
                f"({len(already_sync)} déjà synchro, "
                f"{len(unparseable)} noms non reconnus)",
                color=LOG_COLOR_OK, title="Backfill énoncés terminé",
            )
            return
        embed.set_footer(text="Réagis avec ✅ pour lancer, ❌ pour annuler (60 sec).")
        prompt_msg = await ctx.send(embed=embed)
        try:
            await prompt_msg.add_reaction("✅")
            await prompt_msg.add_reaction("❌")
        except discord.HTTPException:
            pass
        def check_react(reaction: discord.Reaction, user) -> bool:
            return (
                user == ctx.author
                and reaction.message.id == prompt_msg.id
                and str(reaction.emoji) in ("✅", "❌")
            )
        try:
            reaction, _user = await self.bot.wait_for(
                "reaction_add", check=check_react,
                timeout=self.BACKFILL_CONFIRM_TIMEOUT,
            )
        except asyncio.TimeoutError:
            await ctx.send("⏱️ Timeout : backfill énoncés annulé.")
            return
        if str(reaction.emoji) == "❌":
            await ctx.send("❌ Backfill énoncés annulé.")
            return
        await ctx.send(
            f"🚀 Backfill énoncés **{matiere}** démarré : {actions_count} "
            f"action(s) (~{eta_mins} min)."
        )
        await self._log(
            f"🚀 Backfill énoncés {matiere} démarré : "
            f"{len(to_create)} threads à créer, "
            f"{len(to_attach)} à attacher, {len(to_update)} v→v+1",
            color=LOG_COLOR_INFO, title="Backfill énoncés démarré",
        )
        start_ts = time.monotonic()
        created = 0
        attached = 0
        updated = 0
        skipped = 0
        errors: List[Tuple[str, str]] = []
        action_count = 0
        for tup in actions:
            key, type_code, num, annee = tup
            try:
                r = await self._do_publish_enonce(
                    guild, matiere, type_code, num, annee=annee,
                )
            except Exception as e:
                log.error(f"Backfill énoncés: exception sur {key}: {e}")
                errors.append((key, f"exception: {str(e)[:150]}"))
                await self._log(
                    f"❌ Backfill énoncés {matiere} : exception sur `{key}`, "
                    f"`{str(e)[:150]}`",
                    color=LOG_COLOR_ERROR,
                )
                continue
            status = r.get("status")
            if status == "ok" and r.get("was_thread_created"):
                created += 1
                action_count += 1
            elif status == "ok":
                attached += 1
                action_count += 1
            elif status == "ok_v2":
                updated += 1
                action_count += 1
            elif status == "skip_same_md5":
                skipped += 1
                continue
            else:
                errors.append((key, r.get("reason", "?")))
                await self._log(
                    f"❌ Backfill énoncés {matiere} : `{key}` → {r.get('reason')}",
                    color=LOG_COLOR_ERROR,
                )
            if action_count < actions_count:
                await asyncio.sleep(self.BACKFILL_SLEEP_SECONDS)
        total_elapsed = time.monotonic() - start_ts
        mins = int(total_elapsed // 60)
        secs = int(total_elapsed % 60)
        summary_color = LOG_COLOR_OK if not errors else LOG_COLOR_WARN
        final = discord.Embed(
            title=f"✅ Backfill énoncés {matiere} terminé",
            color=summary_color,
        )
        final.add_field(name="Threads créés", value=str(created), inline=True)
        final.add_field(name="Énoncés attachés", value=str(attached), inline=True)
        final.add_field(name="Mises à jour v2", value=str(updated), inline=True)
        final.add_field(name="Skips runtime", value=str(skipped), inline=True)
        final.add_field(name="Erreurs", value=str(len(errors)), inline=True)
        final.add_field(
            name="Durée totale",
            value=f"{mins} min {secs:02d} s", inline=False,
        )
        if errors:
            err_list = "\n".join(f"• `{k}` : {r}" for k, r in errors[:8])
            if len(errors) > 8:
                err_list += f"\n… (+{len(errors) - 8} autres)"
            final.add_field(name="Détails erreurs", value=err_list, inline=False)
        await ctx.send(embed=final)
        await self._log(
            f"✅ Backfill énoncés {matiere} terminé : "
            f"{created} créés, {attached} attachés, {updated} v2, "
            f"{len(errors)} erreur(s), {mins} min {secs:02d} s",
            color=summary_color, title="Backfill énoncés terminé",
        )
    @cours.command(name="status")
    async def status(self, ctx: commands.Context):
        inbox = os.path.join(COURS_ROOT, "_INBOX")
        if not os.path.isdir(inbox):
            await ctx.send("📂 `_INBOX` n'existe pas ou est inaccessible.")
            return
        files = [f for f in os.listdir(inbox) if os.path.isfile(os.path.join(inbox, f))]
        if not files:
            await ctx.send("✅ `_INBOX` est vide : rien en attente.")
            return
        listing = "\n".join(f"• `{f}`" for f in sorted(files)[:30])
        count = len(files)
        await ctx.send(f"📂 **{count} fichier(s) en attente dans `_INBOX` :**\n{listing}")
    @cours.command(name="missing")
    async def missing(self, ctx: commands.Context):
        missing_list = []
        for matiere_lower, matiere in MATIERE_MAP.items():
            for type_lower, type_code in TYPE_MAP.items():
                type_dir = os.path.join(COURS_ROOT, matiere, type_code)
                if not os.path.isdir(type_dir):
                    continue
                for entry in os.listdir(type_dir):
                    entry_path = os.path.join(type_dir, entry)
                    if not os.path.isdir(entry_path):
                        continue
                    m = re.match(rf"^{type_code}(\d+)$", entry, re.IGNORECASE)
                    if not m:
                        continue
                    num = m.group(1)
                    pattern = f"{type_code}{num}_{matiere}_"
                    found = False
                    if os.path.isdir(AUDIO_ROOT):
                        for f in os.listdir(AUDIO_ROOT):
                            if f.startswith(pattern) and f.endswith(".m4a"):
                                found = True
                                break
                    if not found:
                        missing_list.append(f"{type_code}{num} {matiere}")
        if not missing_list:
            await ctx.send("✅ Toutes les séances connues ont un audio correspondant.")
        else:
            listing = "\n".join(f"• `{s}`" for s in sorted(missing_list)[:40])
            await ctx.send(f"🔍 **{len(missing_list)} séance(s) sans audio :**\n{listing}")
    @staticmethod
    def _format_scan_line(s: dict) -> str:
        pub = s.get("published", {})
        flags = []
        flags.append("🎧" if s["has_audio"] else "—")
        flags.append("📝" if s["has_transcript"] else "—")
        done = []
        if pub.get("audio"): done.append("audio")
        if pub.get("transcription"): done.append("trans")
        if pub.get("resume"): done.append("résumé")
        done_str = f" [déjà: {', '.join(done)}]" if done else ""
        return f"{s['type']}{s['num']} {s['matiere']} ({s['date']}) {' '.join(flags)}{done_str}"
    @cours.command(name="scan")
    async def scan(self, ctx: commands.Context, matiere: Optional[str] = None):
        if matiere and matiere.lower() not in MATIERE_MAP:
            await ctx.send(
                f"❌ Matière invalide `{matiere}`. Valeurs : {', '.join(MATIERE_MAP.keys())}"
            )
            return
        sessions = scan_available(matiere_filter=matiere)
        scope = f" ({MATIERE_MAP[matiere.lower()]})" if matiere else ""
        if not sessions:
            await ctx.send(f"✅ Tout est à jour{scope} : rien à publier.")
            return
        lines = [self._format_scan_line(s) for s in sessions[:25]]
        embed = discord.Embed(
            title=f"🔎 {len(sessions)} séance(s) à publier{scope}",
            description="\n".join(f"• `{l}`" for l in lines),
            color=LOG_COLOR_INFO,
        )
        if len(sessions) > 25:
            embed.set_footer(text=f"(+{len(sessions) - 25} autres non affichées)")
        await ctx.send(embed=embed)
    @cours.command(name="auto")
    async def auto(self, ctx: commands.Context, matiere: Optional[str] = None):
        if matiere and matiere.lower() not in MATIERE_MAP:
            await ctx.send(
                f"❌ Matière invalide `{matiere}`. Valeurs : {', '.join(MATIERE_MAP.keys())}"
            )
            return
        sessions = scan_available(matiere_filter=matiere)
        scope = f" ({MATIERE_MAP[matiere.lower()]})" if matiere else ""
        if not sessions:
            await ctx.send(f"✅ Tout est à jour{scope} : rien à publier.")
            return
        summary = ", ".join(f"{s['type']}{s['num']} {s['matiere']}" for s in sessions[:10])
        more = f" (+{len(sessions) - 10})" if len(sessions) > 10 else ""
        embed = discord.Embed(
            title=f"🚀 {len(sessions)} séance(s) à publier{scope}",
            description=f"{summary}{more}\n\nRéagis ✅ ou réponds `oui` dans les 30s pour confirmer.",
            color=LOG_COLOR_WARN,
        )
        prompt_msg = await ctx.send(embed=embed)
        try:
            await prompt_msg.add_reaction("✅")
        except Exception:
            pass
        def check_msg(m: discord.Message) -> bool:
            return (
                m.author == ctx.author
                and m.channel == ctx.channel
                and m.content.strip().lower() in ("oui", "yes", "y", "ok", "✅")
            )
        def check_react(reaction: discord.Reaction, user) -> bool:
            return (
                user == ctx.author
                and reaction.message.id == prompt_msg.id
                and str(reaction.emoji) == "✅"
            )
        done, pending = await asyncio.wait(
            [
                asyncio.create_task(self.bot.wait_for("message", check=check_msg)),
                asyncio.create_task(self.bot.wait_for("reaction_add", check=check_react)),
            ],
            timeout=30,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        if not done:
            await ctx.send("⏱️ Timeout : publication annulée.")
            return
        await ctx.send(f"▶️ Publication de {len(sessions)} séance(s) en séquence...")
        await self._log(
            f"🚀 `!cours auto{' ' + matiere if matiere else ''}` lancé par {ctx.author} : {len(sessions)} séance(s){scope}",
            color=LOG_COLOR_INFO,
            title="Auto-publication",
        )
        publish_cmd = self.publish
        for s in sessions:
            type_lower = s["type"].lower()
            matiere_lower = s["matiere"].lower()
            await ctx.send(f"• **{s['type']}{s['num']} {s['matiere']} ({s['date']})**")
            try:
                await ctx.invoke(publish_cmd, type_lower, matiere_lower, s["num"], s["date"])
            except Exception as e:
                log.error(f"auto: erreur sur {s}: {e}")
                await ctx.send(f"❌ Erreur sur `{s['type']}{s['num']} {s['matiere']}` : `{str(e)[:150]}`")
                await self._log(
                    f"❌ Auto : erreur sur {s['type']}{s['num']} {s['matiere']}, `{str(e)[:150]}`",
                    color=LOG_COLOR_ERROR,
                )
        await ctx.send("✅ Auto-publication terminée.")
        await self._log("✅ Auto-publication terminée.", color=LOG_COLOR_OK)
    @cours.command(name="absent")
    async def absent(self, ctx: commands.Context, type_str: str, matiere_str: str,
                     num: str, date: Optional[str] = None, *, raison: str = "absent"):
        guild = self._get_guild()
        if guild is None:
            await ctx.send("❌ Serveur ISTIC L1 G2 introuvable.")
            return
        type_lower = type_str.lower()
        matiere_lower = matiere_str.lower()
        if type_lower not in TYPE_MAP:
            await ctx.send(f"❌ Type invalide `{type_str}`. Valeurs : {', '.join(TYPE_MAP.keys())}")
            return
        if matiere_lower not in MATIERE_MAP:
            await ctx.send(f"❌ Matière invalide `{matiere_str}`. Valeurs : {', '.join(MATIERE_MAP.keys())}")
            return
        if not num.isdigit():
            await ctx.send(f"❌ Numéro invalide `{num}`.")
            return
        if date is not None and not re.match(r"^\d{4}$", date):
            await ctx.send(f"❌ Date invalide `{date}` (format JJMM attendu).")
            return
        type_code = TYPE_MAP[type_lower]
        matiere = MATIERE_MAP[matiere_lower]
        date_part = f" {date}" if date else ""
        label = f"{type_code}{num} {matiere}{date_part}"
        key = mark_absent(type_code, matiere, num, date=date, raison=raison)
        channels = {
            "audio": find_channel(guild, type_lower, CHANNEL_SUFFIXES["audio"], matiere_lower),
            "texte": find_channel(guild, type_lower, CHANNEL_SUFFIXES["transcription"], matiere_lower),
            "résumé": find_channel(guild, type_lower, CHANNEL_SUFFIXES["resume"], matiere_lower),
        }
        posted_any = False
        missing_channels = []
        for kind, ch in channels.items():
            if ch is None:
                missing_channels.append(kind)
                continue
            try:
                await ch.send(f"[Pas de {kind} du {label}]")
                posted_any = True
            except Exception as e:
                log.warning(f"Envoi absence échoué dans #{ch.name}: {e}")
        if posted_any:
            absences = load_absences()
            if key in absences:
                absences[key]["posted_discord"] = True
                save_absences(absences)
        summary = [f"🚫 Absence enregistrée : `{key}` (raison : {raison})"]
        if missing_channels:
            summary.append(f"⚠️ Salons introuvables : {', '.join(missing_channels)}")
        await ctx.send("\n".join(summary))
        await self._log(
            f"🚫 Absence `{key}` : {raison} (posté: {'oui' if posted_any else 'non'})",
            color=LOG_COLOR_WARN,
            title="Absence marquée",
        )
    @cours.command(name="absences")
    async def absences(self, ctx: commands.Context):
        abs_ = load_absences()
        if not abs_:
            await ctx.send("✅ Aucune absence enregistrée.")
            return
        lines = []
        for key in sorted(abs_.keys()):
            entry = abs_[key]
            raison = entry.get("raison", "—")
            date = entry.get("date") or "—"
            posted = "📤" if entry.get("posted_discord") else "⏳"
            lines.append(f"{posted} `{key}` · {date} · {raison}")
        chunks = []
        buf = ""
        for l in lines:
            if len(buf) + len(l) + 1 > 3800:
                chunks.append(buf)
                buf = l
            else:
                buf = (buf + "\n" + l) if buf else l
        if buf:
            chunks.append(buf)
        for i, chunk in enumerate(chunks):
            embed = discord.Embed(
                title=(f"🚫 {len(abs_)} absence(s) enregistrée(s)"
                       if i == 0 else f"… suite ({i+1}/{len(chunks)})"),
                description=chunk,
                color=LOG_COLOR_WARN,
            )
            if i == 0:
                embed.set_footer(text="📤 = déjà posté sur Discord · ⏳ = non posté")
            await ctx.send(embed=embed)
    @staticmethod
    def _classify_channel(name: str) -> Optional[Tuple[str, str]]:
        low = name.lower()
        base = re.sub(r"^[^a-z0-9]+", "", low)
        if not base:
            return None
        tokens = set(re.split(r"[^a-z0-9]+", base))
        if "audio" in tokens:
            return ("🎧", base)
        if "transcription" in tokens:
            return ("📝", base)
        if "résumé" in tokens or "resume" in tokens:
            return ("📌", base)
        if base == "logs" or "logs" in tokens and base.startswith("logs"):
            return ("📋", base)
        if base in ("tests", "test"):
            return ("🔧", base)
        if base in ("general", "général"):
            return ("💬", base)
        return None
    @classmethod
    def _target_channel_name(cls, name: str) -> Optional[str]:
        classified = cls._classify_channel(name)
        if classified is None:
            return None
        emoji, base_ = classified
        return f"{emoji}・{base_}"
    @cours.command(name="setup-channels", aliases=["setupchannels"])
    async def setup_channels(self, ctx: commands.Context):
        guild = self._get_guild()
        if guild is None:
            await ctx.send("❌ Serveur ISTIC L1 G2 introuvable.")
            return
        plan: List[Tuple[discord.TextChannel, str]] = []
        already_ok = 0
        for ch in guild.text_channels:
            target = self._target_channel_name(ch.name)
            if target is None:
                continue
            if ch.name == target:
                already_ok += 1
                continue
            plan.append((ch, target))
        if not plan:
            await ctx.send(f"✅ Tous les salons sont déjà corrects ({already_ok} détectés).")
            return
        preview = "\n".join(f"• `{ch.name}` → `{new}`" for ch, new in plan[:20])
        more = f"\n(+{len(plan) - 20} autres)" if len(plan) > 20 else ""
        embed = discord.Embed(
            title=f"🔧 Renommage de {len(plan)} salon(s)",
            description=f"{preview}{more}\n\nRéagis ✅ ou réponds `oui` dans les 30s.",
            color=LOG_COLOR_WARN,
        )
        embed.set_footer(text=f"{already_ok} salon(s) déjà corrects")
        prompt_msg = await ctx.send(embed=embed)
        try:
            await prompt_msg.add_reaction("✅")
        except Exception:
            pass
        def check_msg(m: discord.Message) -> bool:
            return (
                m.author == ctx.author
                and m.channel == ctx.channel
                and m.content.strip().lower() in ("oui", "yes", "y", "ok", "✅")
            )
        def check_react(reaction: discord.Reaction, user) -> bool:
            return (
                user == ctx.author
                and reaction.message.id == prompt_msg.id
                and str(reaction.emoji) == "✅"
            )
        done, pending = await asyncio.wait(
            [
                asyncio.create_task(self.bot.wait_for("message", check=check_msg)),
                asyncio.create_task(self.bot.wait_for("reaction_add", check=check_react)),
            ],
            timeout=30,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        if not done:
            await ctx.send("⏱️ Timeout : renommage annulé.")
            return
        await ctx.send(f"▶️ Renommage de {len(plan)} salon(s) (≈{len(plan)}s)...")
        renamed = 0
        errors: List[str] = []
        for ch, new in plan:
            perms = ch.permissions_for(guild.me)
            if not perms.manage_channels:
                granted = False
                if ch.id == LOG_CHANNEL_ID and ch.permissions_for(guild.me).manage_roles:
                    try:
                        await ch.set_permissions(
                            guild.me,
                            manage_channels=True,
                            reason="Setup channels : auto-grant pour rename",
                        )
                        granted = True
                    except discord.Forbidden:
                        granted = False
                    except Exception:
                        granted = False
                if not granted:
                    errors.append(
                        f"#{ch.name} : permission `manage_channels` manquante, "
                        f"accorde-la au bot puis relance `!cours setup-channels`"
                    )
                    await asyncio.sleep(0.2)
                    continue
            try:
                await ch.edit(name=new, reason="Setup channels : pipeline COURS")
                renamed += 1
            except discord.Forbidden:
                errors.append(f"#{ch.name} : Forbidden (rôle du bot sous le salon ?)")
            except Exception as e:
                errors.append(f"#{ch.name} ({str(e)[:80]})")
            await asyncio.sleep(1.0)
        result = discord.Embed(
            title="🔧 Setup channels : terminé",
            color=LOG_COLOR_OK if not errors else LOG_COLOR_WARN,
        )
        result.add_field(name="Renommés", value=str(renamed), inline=True)
        result.add_field(name="Déjà corrects", value=str(already_ok), inline=True)
        result.add_field(name="Erreurs", value=str(len(errors)), inline=True)
        if errors:
            result.add_field(name="Détails", value="\n".join(errors)[:1000], inline=False)
        await ctx.send(embed=result)
        await self._log(
            f"🔧 Setup channels : {renamed} renommé(s), {already_ok} déjà corrects, {len(errors)} erreur(s)",
            color=LOG_COLOR_OK if not errors else LOG_COLOR_WARN,
            title="Setup channels",
        )
    @cours.command(name="setup-forums", aliases=["setupforums"])
    async def setup_forums(self, ctx: commands.Context):
        guild = self._get_guild()
        if guild is None:
            await ctx.send("❌ Serveur ISTIC L1 G2 introuvable.")
            return
        created: List[Tuple[str, discord.ForumChannel]] = []
        already: List[Tuple[str, discord.ForumChannel]] = []
        skipped_no_cat: List[str] = []
        errors: List[str] = []
        bot_member = guild.me
        default_role = guild.default_role
        for matiere in MATIERE_MAP.values():
            category = find_matiere_category(guild, matiere)
            if category is None:
                skipped_no_cat.append(matiere)
                await self._log(
                    f"⚠️ Catégorie matière `{matiere}` introuvable : forum non créé",
                    color=LOG_COLOR_WARN,
                )
                continue
            expected_name = correction_forum_name(matiere)
            existing = next(
                (ch for ch in category.channels
                 if isinstance(ch, discord.ForumChannel)
                 and expected_name in ch.name.lower()),
                None,
            )
            if existing is not None:
                already.append((matiere, existing))
                continue
            overwrites = {
                default_role: discord.PermissionOverwrite(
                    view_channel=True,
                    read_message_history=True,
                    send_messages_in_threads=True,
                    create_public_threads=False,
                    create_private_threads=False,
                    manage_threads=False,
                ),
                bot_member: discord.PermissionOverwrite(
                    view_channel=True,
                    read_message_history=True,
                    create_public_threads=True,
                    send_messages_in_threads=True,
                    attach_files=True,
                    embed_links=True,
                    manage_threads=True,
                ),
            }
            try:
                forum = await category.create_forum(
                    name=expected_name,
                    overwrites=overwrites,
                    reason="Setup forums : pipeline COURS (Phase A)",
                    topic=f"Corrections des exercices : {matiere}",
                )
                created.append((matiere, forum))
            except discord.Forbidden:
                errors.append(f"{matiere}: Forbidden (manage_channels sur la catégorie ?)")
            except discord.HTTPException as e:
                errors.append(f"{matiere}: {str(e)[:100]}")
        color = LOG_COLOR_OK if not errors else LOG_COLOR_WARN
        embed = discord.Embed(title="📚 Setup forums corrections", color=color)
        def _fmt(pairs: List[Tuple[str, discord.ForumChannel]]) -> str:
            return "\n".join(f"• **{m}** → <#{f.id}> (`{f.id}`)"
                             for m, f in pairs) or "—"
        embed.add_field(name=f"✅ Créés ({len(created)})",
                        value=_fmt(created), inline=False)
        embed.add_field(name=f"ℹ️ Déjà présents ({len(already)})",
                        value=_fmt(already), inline=False)
        if skipped_no_cat:
            embed.add_field(
                name=f"⚠️ Catégorie manquante ({len(skipped_no_cat)})",
                value=", ".join(skipped_no_cat),
                inline=False,
            )
        if errors:
            embed.add_field(name=f"❌ Erreurs ({len(errors)})",
                            value="\n".join(errors)[:1000], inline=False)
        await ctx.send(embed=embed)
        await self._log(
            f"📚 Setup forums : {len(created)} créé(s), {len(already)} déjà, "
            f"{len(skipped_no_cat)} sans catégorie, {len(errors)} erreur(s)",
            color=color,
            title="Setup forums",
        )
    @cours.command(name="setup-tags", aliases=["setuptags"])
    async def setup_tags(self, ctx: commands.Context):
        guild = self._get_guild()
        if guild is None:
            await ctx.send("❌ Serveur ISTIC L1 G2 introuvable.")
            return
        all_specs = list(TAG_LABELS_TYPE.values()) + list(TAG_LABELS_STATE.values())
        report_lines: List[str] = []
        total_created = 0
        total_skipped = 0
        errors: List[str] = []
        for matiere in MATIERE_MAP.values():
            forum = find_correction_forum(guild, matiere)
            if forum is None:
                report_lines.append(f"⚠️ **{matiere}** : forum introuvable (skip)")
                continue
            existing_names = {tag.name for tag in forum.available_tags}
            missing = [(name, emoji) for (name, emoji) in all_specs
                       if name not in existing_names]
            log.info(
                f"setup-tags {matiere} : "
                f"existing={sorted(repr(n) for n in existing_names)} "
                f"expected={sorted(repr(n) for (n, _) in all_specs)} "
                f"missing={[repr(n) for (n, _) in missing]}"
            )
            if not missing:
                report_lines.append(f"ℹ️ **{matiere}** : {len(all_specs)} tags déjà présents")
                total_skipped += len(all_specs)
                continue
            new_tags: List[discord.ForumTag] = []
            for name, emoji in missing:
                if emoji:
                    partial = discord.PartialEmoji(name=emoji)
                    new_tags.append(discord.ForumTag(
                        name=name, emoji=partial, moderated=False))
                else:
                    new_tags.append(discord.ForumTag(name=name, moderated=False))
            full_list = list(forum.available_tags) + new_tags
            try:
                await forum.edit(available_tags=full_list)
                total_created += len(missing)
                total_skipped += (len(all_specs) - len(missing))
                pretty = [f"{e} {n}" if e else n for (n, e) in missing]
                report_lines.append(
                    f"✅ **{matiere}** : +{len(missing)} tag(s) créé(s) "
                    f"({', '.join(pretty)})"
                )
            except discord.Forbidden:
                errors.append(f"{matiere}: Forbidden (manage_channels sur le forum ?)")
            except discord.HTTPException as e:
                errors.append(f"{matiere}: {str(e)[:120]}")
        color = LOG_COLOR_OK if not errors else LOG_COLOR_WARN
        embed = discord.Embed(title="🏷️ Setup tags forum", color=color)
        embed.add_field(
            name="Résumé",
            value=(
                f"Tags créés : **{total_created}**\n"
                f"Déjà présents : **{total_skipped}**\n"
                f"Erreurs : **{len(errors)}**"
            ),
            inline=False,
        )
        if report_lines:
            embed.add_field(name="Détails par matière",
                            value="\n".join(report_lines)[:1000], inline=False)
        if errors:
            embed.add_field(name="Erreurs",
                            value="\n".join(errors)[:1000], inline=False)
        await ctx.send(embed=embed)
        await self._log(
            f"🏷️ Setup tags : +{total_created} créés, {total_skipped} déjà, "
            f"{len(errors)} erreur(s)",
            color=color, title="Setup tags",
        )
    @cours.command(name="republish-correction", aliases=["republishcorrection"])
    async def republish_correction(self, ctx: commands.Context,
                                    matiere_str: str, type_str: str,
                                    num: str, exo: str,
                                    annee_str: Optional[str] = None):
        guild = self._get_guild()
        if guild is None:
            await ctx.send("❌ Serveur ISTIC L1 G2 introuvable.")
            return
        matiere_lower = matiere_str.lower()
        type_lower = type_str.lower()
        allowed_types = {"td": "TD", "tp": "TP", "cc": "CC", "quiz": "quiz"}
        if matiere_lower not in MATIERE_MAP:
            await ctx.send(
                f"❌ Matière invalide `{matiere_str}`. "
                f"Valeurs : {', '.join(MATIERE_MAP.keys())}"
            )
            return
        if type_lower not in allowed_types:
            await ctx.send(
                f"❌ Type invalide `{type_str}`. "
                f"Valeurs : {', '.join(allowed_types.keys())}"
            )
            return
        matiere = MATIERE_MAP[matiere_lower]
        type_code = allowed_types[type_lower]
        await self._log(
            f"🔄 `!cours republish-correction {matiere_lower} {type_lower} "
            f"{num} {exo}{' ' + annee_str if annee_str else ''}` lancé par {ctx.author}",
            color=LOG_COLOR_INFO, title="Republication forcée",
        )
        r = await self._do_publish_correction(
            guild, matiere, type_code, num, str(exo),
            annee=annee_str, force_republish=True,
        )
        await self._format_publish_result(ctx, r)
    @cours.command(name="purge-thread", aliases=["purgethread"])
    async def purge_thread(self, ctx: commands.Context,
                            matiere_str: str, type_str: str, num: str,
                            annee_str: Optional[str] = None):
        matiere_lower = matiere_str.lower()
        type_lower = type_str.lower()
        allowed_types = {"td": "TD", "tp": "TP", "cc": "CC", "quiz": "quiz"}
        if matiere_lower not in MATIERE_MAP or type_lower not in allowed_types:
            await ctx.send(
                "❌ Usage : `!cours purge-thread <matiere> <type> <num> [annee]`"
            )
            return
        matiere = MATIERE_MAP[matiere_lower]
        type_code = allowed_types[type_lower]
        key = thread_key(matiere, type_code, num, annee_str)
        data = load_discord_published_v2()
        entry = data["threads"].pop(key, None)
        if entry is None:
            await ctx.send(f"ℹ️ Aucune entrée JSON pour `{key}`.")
            return
        save_discord_published_v2(data)
        corr_count = len(entry.get("corrections", {}))
        await ctx.send(
            f"🗑️ Entrée `{key}` purgée "
            f"({corr_count} correction(s) tracée(s) perdue(s)).\n"
            f"La prochaine publication recréera un thread neuf."
        )
        await self._log(
            f"🗑️ Thread entry purgée : `{key}` ({corr_count} corrections)",
            color=LOG_COLOR_WARN, title="Purge thread",
        )
    def _perso_category_overwrites(self, guild: discord.Guild) -> dict:
        ow = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, read_message_history=True,
                send_messages=True, send_messages_in_threads=True,
                create_public_threads=True, manage_threads=True,
                manage_channels=True, attach_files=True, embed_links=True,
            ),
        }
        admin_role = guild.get_role(ADMIN_ROLE_ID)
        if admin_role is not None:
            ow[admin_role] = discord.PermissionOverwrite(
                view_channel=True, read_message_history=True,
                send_messages=True, send_messages_in_threads=True,
                manage_messages=True, manage_threads=True,
            )
        return ow
    @cours.command(name="setup-perso", aliases=["setupperso"])
    async def setup_perso(self, ctx: commands.Context):
        guild = self._get_guild()
        if guild is None:
            await ctx.send("❌ Serveur ISTIC L1 G2 introuvable.")
            return
        await self._log(
            f"🔒 `!cours setup-perso` lancé par {ctx.author}",
            color=LOG_COLOR_INFO, title="Setup perso",
        )
        overwrites = self._perso_category_overwrites(guild)
        category = find_perso_category(guild)
        cat_action = "?"
        try:
            if category is None:
                category = await guild.create_category(
                    name=PERSO_CATEGORY_NAME,
                    overwrites=overwrites,
                    reason="Setup perso : pipeline COURS (Phase F1)",
                )
                cat_action = "créée"
            else:
                await category.edit(overwrites=overwrites,
                                    reason="Setup perso : refresh perms")
                cat_action = "permissions mises à jour"
        except discord.Forbidden:
            await ctx.send("❌ Permission refusée pour créer / éditer "
                           "la catégorie 🔒 PERSONNEL.")
            return
        except discord.HTTPException as e:
            await ctx.send(f"❌ Erreur Discord : `{str(e)[:200]}`")
            return
        created: List[Tuple[str, "discord.ForumChannel"]] = []
        already: List[Tuple[str, "discord.ForumChannel"]] = []
        errors: List[str] = []
        targets: List[Tuple[str, str, str]] = []
        for matiere in MATIERE_MAP.values():
            targets.append((matiere, perso_forum_name(matiere),
                            f"Matériel personnel : {matiere} (privé)"))
        targets.append(("HORS-SUJETS", HORS_SUJETS_FORUM_NAME,
                        "Contenu hors-cours (mémos, brainstorms, etc., privé)"))
        for matiere in MATIERE_MAP.values():
            targets.append((f"INBOX-{matiere}", inbox_forum_name(matiere),
                            f"Boîte de dépôt vrac : {matiere} "
                            f"(photos, scans, PDFs, captures, privé, Phase O)"))
        for label, expected, topic in targets:
            existing = next(
                (ch for ch in category.channels
                 if isinstance(ch, discord.ForumChannel)
                 and expected in ch.name.lower()),
                None,
            )
            if existing is not None:
                already.append((label, existing))
                continue
            try:
                forum = await category.create_forum(
                    name=expected,
                    reason="Setup perso : pipeline COURS (Phase F1/L)",
                    topic=topic,
                )
                created.append((label, forum))
            except discord.Forbidden:
                errors.append(f"{label}: Forbidden (manage_channels ?)")
            except discord.HTTPException as e:
                errors.append(f"{label}: {str(e)[:100]}")
        color = LOG_COLOR_OK if not errors else LOG_COLOR_WARN
        embed = discord.Embed(title="🔒 Setup forums personnels", color=color)
        def _fmt(pairs):
            return "\n".join(f"• **{m}** → <#{f.id}>" for m, f in pairs) or "—"
        embed.add_field(
            name="Catégorie",
            value=f"`{PERSO_CATEGORY_NAME}` : {cat_action}",
            inline=False,
        )
        embed.add_field(name=f"✅ Créés ({len(created)})",
                        value=_fmt(created), inline=False)
        embed.add_field(name=f"ℹ️ Déjà présents ({len(already)})",
                        value=_fmt(already), inline=False)
        if errors:
            embed.add_field(name=f"❌ Erreurs ({len(errors)})",
                            value="\n".join(errors)[:1000], inline=False)
        await ctx.send(embed=embed)
        await self._log(
            f"🔒 Setup perso : catégorie {cat_action}, "
            f"{len(created)} forum(s) créé(s), {len(already)} déjà, "
            f"{len(errors)} erreur(s)",
            color=color, title="Setup perso terminé",
        )
    @cours.command(name="setup-tags-perso", aliases=["setuptagsperso"])
    async def setup_tags_perso(self, ctx: commands.Context):
        guild = self._get_guild()
        if guild is None:
            await ctx.send("❌ Serveur ISTIC L1 G2 introuvable.")
            return
        all_specs = (list(PERSO_TAG_LABELS_TYPE.values())
                     + list(PERSO_TAG_LABELS_MATERIEL.values()))
        report_lines: List[str] = []
        total_created = 0
        total_skipped = 0
        errors: List[str] = []
        for matiere in MATIERE_MAP.values():
            forum = find_perso_forum(guild, matiere)
            if forum is None:
                report_lines.append(
                    f"⚠️ **{matiere}** : forum `{perso_forum_name(matiere)}` "
                    "introuvable (skip : lance `!cours setup-perso` d'abord)"
                )
                continue
            existing_names = {tag.name for tag in forum.available_tags}
            missing = [(n, e) for (n, e) in all_specs
                       if n not in existing_names]
            if not missing:
                report_lines.append(
                    f"ℹ️ **{matiere}** : {len(all_specs)} tags déjà présents"
                )
                total_skipped += len(all_specs)
                continue
            new_tags: List[discord.ForumTag] = []
            for name, emoji in missing:
                if emoji:
                    partial = discord.PartialEmoji(name=emoji)
                    new_tags.append(discord.ForumTag(
                        name=name, emoji=partial, moderated=False))
                else:
                    new_tags.append(
                        discord.ForumTag(name=name, moderated=False))
            full_list = list(forum.available_tags) + new_tags
            try:
                await forum.edit(available_tags=full_list)
                total_created += len(missing)
                total_skipped += (len(all_specs) - len(missing))
                pretty = [f"{e} {n}" if e else n for (n, e) in missing]
                report_lines.append(
                    f"✅ **{matiere}** : +{len(missing)} tag(s) créé(s) "
                    f"({', '.join(pretty)})"
                )
            except discord.Forbidden:
                errors.append(f"{matiere}: Forbidden (manage_channels ?)")
            except discord.HTTPException as e:
                errors.append(f"{matiere}: {str(e)[:120]}")
        color = LOG_COLOR_OK if not errors else LOG_COLOR_WARN
        embed = discord.Embed(title="🏷️ Setup tags forum perso", color=color)
        embed.add_field(
            name="Résumé",
            value=(
                f"Tags créés : **{total_created}**\n"
                f"Déjà présents : **{total_skipped}**\n"
                f"Erreurs : **{len(errors)}**"
            ),
            inline=False,
        )
        if report_lines:
            embed.add_field(name="Détails par matière",
                            value="\n".join(report_lines)[:1000], inline=False)
        if errors:
            embed.add_field(name="Erreurs",
                            value="\n".join(errors)[:1000], inline=False)
        await ctx.send(embed=embed)
    @cours.command(name="publish-perso", aliases=["publishperso"])
    async def publish_perso_cmd(self, ctx: commands.Context,
                                 matiere_str: str, type_str: str,
                                 num: str,
                                 annee_str: Optional[str] = None):
        guild = self._get_guild()
        if guild is None:
            await ctx.send("❌ Serveur ISTIC L1 G2 introuvable.")
            return
        matiere_lower = matiere_str.lower()
        type_lower = type_str.lower()
        allowed_types = {"td": "TD", "tp": "TP", "cc": "CC", "cm": "CM", "quiz": "quiz"}
        if matiere_lower not in MATIERE_MAP:
            await ctx.send(
                f"❌ Matière invalide `{matiere_str}`. "
                f"Valeurs : {', '.join(MATIERE_MAP.keys())}"
            )
            return
        if type_lower not in allowed_types:
            await ctx.send(
                f"❌ Type invalide `{type_str}`. "
                f"Valeurs : {', '.join(allowed_types.keys())}"
            )
            return
        matiere = MATIERE_MAP[matiere_lower]
        type_code = allowed_types[type_lower]
        await self._log(
            f"🔒 `!cours publish-perso {matiere_lower} {type_lower} {num}"
            + (f" {annee_str}" if annee_str else "")
            + f"` lancé par {ctx.author}",
            color=LOG_COLOR_INFO, title="Publish perso",
        )
        r = await self._do_publish_perso(
            guild, matiere, type_code, num, annee=annee_str,
        )
        await self._format_perso_result(ctx, r, matiere, type_code, num)
    async def _format_perso_result(self, ctx: commands.Context, r: dict,
                                    matiere: str, type_code: str, num: str):
        status = r.get("status")
        if status == "no_material":
            await ctx.send(
                f"ℹ️ Aucun matériel perso détecté pour `{type_code}{num} {matiere}`.\n"
                "Vérifie que des fichiers TACHE/SCRIPT/slides/vidéos existent "
                "sous `COURS/{matiere}/{type}/{type}{num}/...`."
            )
            return
        if status == "error_forum_missing":
            await ctx.send(f"❌ {r.get('reason')}")
            return
        if status != "ok":
            await ctx.send(f"❌ Erreur : `{r.get('reason', 'inconnue')}`")
            return
        ok = r.get("ok", 0)
        ok_v2 = r.get("ok_v2", 0)
        skip = r.get("skip_same_md5", 0)
        skip_nf = r.get("skip_no_file", 0)
        skip_big = r.get("skip_too_big", 0)
        errs = r.get("errors", 0)
        total = ok + ok_v2 + skip + skip_nf + skip_big + errs
        kind_counts: Dict[str, Dict[str, int]] = {}
        for d in r.get("details", []):
            pk = d.get("post_key", "?")
            kind = pk.split(":", 1)[0] if ":" in pk else pk
            kind_counts.setdefault(kind, {"ok": 0, "ok_v2": 0,
                                          "skip": 0, "err": 0})
            s = d.get("status", "")
            if s == "ok":            kind_counts[kind]["ok"] += 1
            elif s == "ok_v2":       kind_counts[kind]["ok_v2"] += 1
            elif s.startswith("skip_"): kind_counts[kind]["skip"] += 1
            else:                    kind_counts[kind]["err"] += 1
        kind_lines = []
        for kind in ("tache", "script", "script_print", "slides", "slides_src", "video"):
            if kind not in kind_counts:
                continue
            c = kind_counts[kind]
            parts = []
            if c["ok"]:    parts.append(f"{c['ok']} OK")
            if c["ok_v2"]: parts.append(f"{c['ok_v2']} v2")
            if c["skip"]:  parts.append(f"{c['skip']} skip")
            if c["err"]:   parts.append(f"{c['err']} err")
            kind_lines.append(f"• {kind} : {', '.join(parts)}")
        embed = discord.Embed(
            title=f"🔒 Publication personnelle : {matiere} {type_code}{num}",
            color=LOG_COLOR_OK if errs == 0 else LOG_COLOR_WARN,
            description=(
                f"Thread : **{r.get('thread_title')}**\n"
                f"<{r.get('thread_url', '?')}>"
            ),
        )
        created_note = " (nouveau thread)" if r.get("was_thread_created") else ""
        embed.add_field(
            name=f"Résumé{created_note}",
            value=(
                f"Posts traités : **{total}**\n"
                f"  ✅ Nouveaux : {ok}\n"
                f"  🔄 Mis à jour : {ok_v2}\n"
                f"  ⏭️ Inchangés (MD5) : {skip}\n"
                f"  ⚠️ Fichier disparu : {skip_nf}\n"
                f"  ⚠️ Trop lourd (skip) : {skip_big}\n"
                f"  ❌ Erreurs : {errs}"
            ),
            inline=False,
        )
        if kind_lines:
            embed.add_field(name="Détail par catégorie",
                            value="\n".join(kind_lines), inline=False)
        await ctx.send(embed=embed)
        await self._log(
            f"🔒 Publish perso {matiere} {type_code}{num} : "
            f"{ok} OK, {ok_v2} v2, {skip} skip, {errs} err",
            color=LOG_COLOR_OK if errs == 0 else LOG_COLOR_WARN,
            title="Publish perso terminé",
        )
    @cours.command(name="backfill-perso", aliases=["backfillperso"])
    async def backfill_perso(self, ctx: commands.Context,
                              matiere_str: Optional[str] = None):
        guild = self._get_guild()
        if guild is None:
            await ctx.send("❌ Serveur ISTIC L1 G2 introuvable.")
            return
        if matiere_str is None or matiere_str.lower() not in MATIERE_MAP:
            await ctx.send(
                "❌ Usage : `!cours backfill-perso <matiere>`\n"
                f"Matières : {', '.join(MATIERE_MAP.keys())}"
            )
            return
        matiere = MATIERE_MAP[matiere_str.lower()]
        forum = find_perso_forum(guild, matiere)
        if forum is None:
            await ctx.send(
                f"❌ Forum `{perso_forum_name(matiere)}` introuvable. "
                "Lance `!cours setup-perso` d'abord."
            )
            return
        await self._log(
            f"🔒 `!cours backfill-perso {matiere_str.lower()}` "
            f"lancé par {ctx.author}",
            color=LOG_COLOR_INFO, title="Backfill perso lancé",
        )
        scan_msg = await ctx.send(
            f"🔍 Scan du matériel perso `COURS/{matiere}/...`"
        )
        all_items = await asyncio.get_event_loop().run_in_executor(
            None, lambda: list_perso_material(matiere)
        )
        try:
            await scan_msg.delete()
        except discord.HTTPException:
            pass
        if not all_items:
            await ctx.send(
                f"ℹ️ Aucun matériel personnel détecté pour `{matiere}`."
            )
            return
        groups: Dict[str, List[dict]] = {}
        for it in all_items:
            groups.setdefault(it["thread_key"], []).append(it)
        tracking = load_discord_perso_published()
        threads_to_create: List[str] = []
        threads_partial: List[Tuple[str, int]] = []
        threads_full_sync: List[str] = []
        big_videos: List[str] = []
        posts_to_publish = 0
        already_md5 = 0
        for tk, items in groups.items():
            entry = tracking["threads"].get(tk)
            new_or_changed = 0
            if entry is None:
                new_or_changed = len(items)
                threads_to_create.append(tk)
                for it in items:
                    if it["kind"] == "video" and it["size_bytes"] > DISCORD_FILE_LIMIT:
                        big_videos.append(tk)
            else:
                existing_posts = entry.get("posts", {})
                for it in items:
                    pk = it["post_key"]
                    old = existing_posts.get(pk)
                    if old is None:
                        new_or_changed += 1
                        if it["kind"] == "video" and it["size_bytes"] > DISCORD_FILE_LIMIT:
                            big_videos.append(tk)
                        continue
                    try:
                        md5 = await asyncio.get_event_loop().run_in_executor(
                            None, self._md5, it["file_path"]
                        )
                    except OSError:
                        continue
                    if old.get("md5") == md5:
                        already_md5 += 1
                    else:
                        new_or_changed += 1
                if new_or_changed > 0:
                    threads_partial.append((tk, new_or_changed))
                else:
                    threads_full_sync.append(tk)
            posts_to_publish += new_or_changed
        actions_count = len(threads_to_create) + len(threads_partial)
        eta_seconds = actions_count * PERSO_BACKFILL_SLEEP_SECONDS
        eta_mins = eta_seconds // 60
        eta_secs = eta_seconds % 60
        embed = discord.Embed(
            title=f"🔒 Backfill perso {matiere} : Aperçu",
            color=LOG_COLOR_INFO,
        )
        embed.add_field(
            name="Résumé",
            value=(
                f"📊 Matériel scanné : **{len(all_items)}** fichiers "
                f"sur **{len(groups)}** thread(s)\n"
                f"🆕 Threads à créer : **{len(threads_to_create)}**\n"
                f"➕ Threads à compléter : **{len(threads_partial)}**\n"
                f"⏭️ Threads déjà synchronisés : **{len(threads_full_sync)}**\n"
                f"✍️ Posts à publier : **{posts_to_publish}**\n"
                f"⏭️ Posts déjà publiés (même MD5) : **{already_md5}**\n"
                f"🎬 Vidéos > 25 Mo (mention seule) : **{len(set(big_videos))}**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Cible",
            value=(
                f"Forum : <#{forum.id}>\n"
                f"Durée estimée : ~{eta_mins} min {eta_secs:02d} s "
                f"({PERSO_BACKFILL_SLEEP_SECONDS}s entre threads)"
            ),
            inline=False,
        )
        if threads_to_create:
            preview = "\n".join(f"• `{k}`" for k in threads_to_create[:5])
            if len(threads_to_create) > 5:
                preview += f"\n… (+{len(threads_to_create) - 5} autres)"
            embed.add_field(name="Premiers à créer", value=preview, inline=False)
        if threads_partial:
            preview = "\n".join(
                f"• `{k}` (+{n} posts)" for k, n in threads_partial[:5]
            )
            if len(threads_partial) > 5:
                preview += f"\n… (+{len(threads_partial) - 5} autres)"
            embed.add_field(name="À compléter", value=preview, inline=False)
        if actions_count == 0:
            embed.set_footer(text="Rien à publier : tout est déjà synchronisé.")
            await ctx.send(embed=embed)
            return
        embed.set_footer(text="Réagis avec ✅ pour lancer, ❌ pour annuler (60 s).")
        prompt_msg = await ctx.send(embed=embed)
        try:
            await prompt_msg.add_reaction("✅")
            await prompt_msg.add_reaction("❌")
        except discord.HTTPException:
            pass
        def check_react(reaction: discord.Reaction, user) -> bool:
            return (user == ctx.author
                    and reaction.message.id == prompt_msg.id
                    and str(reaction.emoji) in ("✅", "❌"))
        try:
            reaction, _user = await self.bot.wait_for(
                "reaction_add", check=check_react,
                timeout=self.BACKFILL_CONFIRM_TIMEOUT,
            )
        except asyncio.TimeoutError:
            await ctx.send("⏱️ Timeout : backfill perso annulé.")
            return
        if str(reaction.emoji) == "❌":
            await ctx.send("❌ Backfill perso annulé.")
            return
        await ctx.send(
            f"🚀 Backfill perso **{matiere}** démarré : "
            f"{actions_count} thread(s) à traiter (~{eta_mins} min)."
        )
        await self._log(
            f"🚀 Backfill perso {matiere} démarré : "
            f"{len(threads_to_create)} threads à créer, "
            f"{len(threads_partial)} à compléter",
            color=LOG_COLOR_INFO, title="Backfill perso démarré",
        )
        start_ts = time.monotonic()
        threads_created = 0
        posts_ok = 0
        posts_v2 = 0
        posts_skip = 0
        errors: List[Tuple[str, str]] = []
        action_index = 0
        action_keys = [k for k, _ in threads_partial] + threads_to_create
        for tk in action_keys:
            items = groups[tk]
            type_code = items[0]["type_code"]
            num = items[0]["num"]
            annee = items[0]["annee"]
            try:
                r = await self._do_publish_perso(
                    guild, matiere, type_code, num, annee=annee,
                )
            except Exception as e:
                log.error(f"Backfill perso: exception sur {tk}: {e}",
                          exc_info=True)
                errors.append((tk, f"exception: {str(e)[:150]}"))
                await self._log(
                    f"❌ Backfill perso {matiere}, exception `{tk}` : "
                    f"`{str(e)[:150]}`",
                    color=LOG_COLOR_ERROR,
                )
                continue
            if r.get("status") != "ok":
                errors.append((tk, r.get("reason", "?")))
                continue
            if r.get("was_thread_created"):
                threads_created += 1
            posts_ok += r.get("ok", 0)
            posts_v2 += r.get("ok_v2", 0)
            posts_skip += r.get("skip_same_md5", 0)
            if r.get("errors", 0) > 0:
                errors.append((tk, f"{r['errors']} erreur(s) côté posts"))
            action_index += 1
            if action_index < actions_count:
                await asyncio.sleep(PERSO_BACKFILL_SLEEP_SECONDS)
        total_elapsed = time.monotonic() - start_ts
        mins = int(total_elapsed // 60)
        secs = int(total_elapsed % 60)
        summary_color = LOG_COLOR_OK if not errors else LOG_COLOR_WARN
        final = discord.Embed(
            title=f"✅ Backfill perso {matiere} terminé",
            color=summary_color,
        )
        final.add_field(name="Threads créés", value=str(threads_created), inline=True)
        final.add_field(name="Posts publiés", value=str(posts_ok), inline=True)
        final.add_field(name="Mises à jour v2", value=str(posts_v2), inline=True)
        final.add_field(name="Skips MD5", value=str(posts_skip), inline=True)
        final.add_field(name="Erreurs", value=str(len(errors)), inline=True)
        final.add_field(name="Durée totale",
                        value=f"{mins} min {secs:02d} s", inline=False)
        if errors:
            err_list = "\n".join(f"• `{k}` : {r}" for k, r in errors[:8])
            if len(errors) > 8:
                err_list += f"\n… (+{len(errors) - 8} autres)"
            final.add_field(name="Détails erreurs", value=err_list, inline=False)
        await ctx.send(embed=final)
        await self._log(
            f"✅ Backfill perso {matiere} terminé : "
            f"{threads_created} threads créés, {posts_ok} OK, "
            f"{posts_v2} v2, {len(errors)} erreur(s), "
            f"{mins} min {secs:02d} s",
            color=summary_color, title="Backfill perso terminé",
        )
    @cours.command(name="purge-perso", aliases=["purgeperso"])
    async def purge_perso(self, ctx: commands.Context,
                           matiere_str: str, type_str: str, num: str,
                           annee_str: Optional[str] = None):
        matiere_lower = matiere_str.lower()
        type_lower = type_str.lower()
        allowed_types = {"td": "TD", "tp": "TP", "cc": "CC", "cm": "CM", "quiz": "quiz"}
        if matiere_lower not in MATIERE_MAP or type_lower not in allowed_types:
            await ctx.send(
                "❌ Usage : `!cours purge-perso <matiere> <type> <num> [annee]`"
            )
            return
        matiere = MATIERE_MAP[matiere_lower]
        type_code = allowed_types[type_lower]
        key = thread_key(matiere, type_code, num, annee_str)
        data = load_discord_perso_published()
        entry = data["threads"].pop(key, None)
        if entry is None:
            await ctx.send(f"ℹ️ Aucune entrée JSON perso pour `{key}`.")
            return
        save_discord_perso_published(data)
        post_count = len(entry.get("posts", {}))
        await ctx.send(
            f"🗑️ Entrée perso `{key}` purgée "
            f"({post_count} post(s) tracé(s) perdu(s)).\n"
            f"La prochaine `!cours publish-perso` recréera un thread neuf."
        )
        await self._log(
            f"🗑️ Thread perso entry purgée : `{key}` ({post_count} posts)",
            color=LOG_COLOR_WARN, title="Purge perso",
        )
    @cours.command(name="retag-orphan", aliases=["retagorphan"])
    async def retag_orphan(self, ctx: commands.Context, forum_name: str):
        guild = self._get_guild()
        if guild is None:
            await ctx.send("❌ Serveur ISTIC L1 G2 introuvable.")
            return
        candidates = [
            forum_name,
            f"corrections-{forum_name.lower()}",
            f"perso-{forum_name.lower()}",
        ]
        forum: Optional[discord.ForumChannel] = None
        for f in guild.forums:
            if any(f.name == c or f.name.endswith(c) for c in candidates):
                forum = f
                break
        if forum is None:
            await ctx.send(
                f"❌ Forum `{forum_name}` introuvable. Forums disponibles :\n"
                + ", ".join(f"`{f.name}`" for f in guild.forums[:20])
            )
            return
        is_perso = "perso-" in forum.name
        is_correction = "corrections-" in forum.name
        if not (is_perso or is_correction):
            await ctx.send(
                f"❌ `{forum.name}` n'est ni un forum perso-* ni corrections-* "
                f"(les tags TYPE/état/matériel ne s'y appliquent pas)."
            )
            return
        orphans: List[discord.Thread] = []
        for th in forum.threads:
            if not th.applied_tags:
                orphans.append(th)
        try:
            async for th in forum.archived_threads(limit=500):
                if not th.applied_tags:
                    orphans.append(th)
        except discord.HTTPException as e:
            log.warning(f"retag-orphan : scan archived a échoué ({e})")
        if not orphans:
            await ctx.send(
                f"✅ Tous les threads de `{forum.name}` ont déjà au moins un tag."
            )
            return
        await ctx.send(
            f"🔍 Forum `{forum.name}` : {len(orphans)} thread(s) sans tags. "
            f"Application en cours…"
        )
        fixed = 0
        skipped_no_type: List[str] = []
        errors: List[str] = []
        for th in orphans:
            type_code = infer_type_code_from_title(th.name)
            if type_code is None:
                skipped_no_type.append(th.name[:60])
                continue
            try:
                if is_perso:
                    applied = await self._apply_perso_thread_tags(
                        th, type_code, set()
                    )
                else:
                    applied = await apply_thread_tags(
                        th, type_code, "corrections_present"
                    )
                if applied:
                    fixed += 1
            except Exception as e:
                errors.append(f"`{th.name[:40]}` : {str(e)[:120]}")
            await asyncio.sleep(1)
        lines = [
            f"✅ **{fixed}/{len(orphans)}** thread(s) tagué(s) dans `{forum.name}`."
        ]
        if skipped_no_type:
            lines.append(
                f"⏭ {len(skipped_no_type)} thread(s) sans motif TYPE inférable "
                f"depuis le titre :"
            )
            for n in skipped_no_type[:8]:
                lines.append(f"   • `{n}`")
            if len(skipped_no_type) > 8:
                lines.append(f"   • … (+{len(skipped_no_type) - 8} autres)")
        if errors:
            lines.append(f"⚠️ {len(errors)} erreur(s) :")
            for e in errors[:5]:
                lines.append(f"   • {e}")
        if is_perso and fixed > 0:
            lines.append(
                "\nℹ️ Tag matériel non inféré ici. Re-run "
                "`!cours backfill-perso <matiere>` pour des tags complets."
            )
        await ctx.send("\n".join(lines)[:1900])
        await self._log(
            f"🏷 Retag-orphan `{forum.name}` : {fixed}/{len(orphans)} OK"
            + (f", {len(skipped_no_type)} skip" if skipped_no_type else "")
            + (f", {len(errors)} err" if errors else ""),
            color=LOG_COLOR_OK if not errors else LOG_COLOR_WARN,
            title="Retag orphan",
        )
    @staticmethod
    def _build_disk_lookup(matiere: str) -> Dict[str, str]:
        lookup: Dict[str, str] = {}
        base = os.path.join(COURS_ROOT, matiere)
        if not os.path.isdir(base):
            return lookup
        for root, _, files in os.walk(base):
            for f in files:
                if f not in lookup:
                    lookup[f] = os.path.join(root, f)
        return lookup
    async def _build_correction_entry_from_thread(
        self,
        thread: discord.Thread,
        matiere: str,
        type_code: str,
        num: str,
        annee: Optional[str],
        disk_lookup: Dict[str, str],
    ) -> dict:
        forum_id = thread.parent.id if thread.parent else 0
        entry: dict = {
            "matiere": matiere,
            "type": type_code,
            "num": num,
            "annee": annee,
            "thread_id": str(thread.id),
            "forum_id": str(forum_id),
            "titre_td": thread.name,
            "enonce": {},
            "corrections": {},
            "state": "missing_enonce",
            "tags_applied": [t.name for t in thread.applied_tags],
            "created_at": (thread.created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                           if thread.created_at else None),
            "last_updated": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        try:
            async for msg in thread.history(limit=200, oldest_first=True):
                for att in msg.attachments:
                    fname = att.filename
                    disk_path = disk_lookup.get(fname)
                    if not disk_path:
                        continue
                    rel = os.path.relpath(disk_path, COURS_ROOT).replace(os.sep, "/")
                    md5 = self._md5(disk_path)
                    pub_at = (msg.created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                              if msg.created_at else None)
                    parsed_e = parse_enonce_filename(fname)
                    if parsed_e and not entry["enonce"]:
                        entry["enonce"] = {
                            "pdf_path": rel,
                            "md5": md5,
                            "message_id": str(msg.id),
                            "status": "present",
                            "published_at": pub_at,
                        }
                        continue
                    parsed_c = parse_correction_filename(fname)
                    if parsed_c:
                        exo = parsed_c["exo"]
                        entry["corrections"][exo] = {
                            "pdf_path": rel,
                            "md5": md5,
                            "message_id": str(msg.id),
                            "version": 1,
                            "published_at": pub_at,
                            "versions": [{
                                "md5": md5,
                                "message_id": str(msg.id),
                                "version": 1,
                                "timestamp": pub_at,
                            }],
                        }
        except discord.HTTPException as e:
            log.warning(f"resync-tracking : history thread {thread.id} → {e}")
        if entry["enonce"] and entry["corrections"]:
            entry["state"] = "corrections_present"
        elif entry["enonce"]:
            entry["state"] = "enonce_only"
        elif entry["corrections"]:
            entry["state"] = "corrections_present"
        else:
            entry["state"] = "missing_enonce"
        return entry
    async def _build_perso_entry_from_thread(
        self,
        thread: discord.Thread,
        matiere: str,
        type_code: str,
        num: str,
        annee: Optional[str],
        disk_lookup: Dict[str, str],
    ) -> dict:
        forum_id = thread.parent.id if thread.parent else 0
        entry: dict = {
            "matiere": matiere,
            "type": type_code,
            "num": num,
            "annee": annee,
            "thread_id": str(thread.id),
            "forum_id": str(forum_id),
            "title": thread.name,
            "posts": {},
            "tags_applied": [t.name for t in thread.applied_tags],
            "created_at": (thread.created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                           if thread.created_at else None),
            "last_updated": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        try:
            async for msg in thread.history(limit=400, oldest_first=True):
                for att in msg.attachments:
                    fname = att.filename
                    disk_path = disk_lookup.get(fname)
                    if not disk_path:
                        continue
                    rel = os.path.relpath(disk_path, COURS_ROOT).replace(os.sep, "/")
                    classification = _perso_classify_file(rel, fname)
                    if not classification:
                        continue
                    kind, exo_suffix = classification
                    md5 = self._md5(disk_path)
                    pub_at = (msg.created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                              if msg.created_at else None)
                    exo_part = f"ex{exo_suffix}" if exo_suffix else "global"
                    if kind == "script":
                        ext = os.path.splitext(fname)[1].lstrip(".").lower()
                        post_key = f"script:{exo_part}:{ext}"
                    else:
                        post_key = f"{kind}:{exo_part}"
                    entry["posts"][post_key] = {
                        "kind": kind,
                        "rel_key": rel,
                        "md5": md5,
                        "message_id": str(msg.id),
                        "version": 1,
                        "is_too_big": False,
                        "size_mb": (round(att.size / 1024 / 1024, 2)
                                    if att.size else 0.0),
                        "published_at": pub_at,
                    }
        except discord.HTTPException as e:
            log.warning(f"resync-tracking : history thread {thread.id} → {e}")
        return entry
    @cours.command(name="resync-tracking", aliases=["resynctracking"])
    async def resync_tracking(self, ctx: commands.Context, forum_name: str,
                               *args):
        apply = "--apply" in args
        guild = self._get_guild()
        if guild is None:
            await ctx.send("❌ Serveur ISTIC L1 G2 introuvable.")
            return
        candidates = [
            forum_name,
            f"corrections-{forum_name.lower()}",
            f"perso-{forum_name.lower()}",
        ]
        forum: Optional[discord.ForumChannel] = None
        for f in guild.forums:
            if any(f.name == c or f.name.endswith(c) for c in candidates):
                forum = f
                break
        if forum is None:
            await ctx.send(
                f"❌ Forum `{forum_name}` introuvable parmi : "
                + ", ".join(f"`{f.name}`" for f in guild.forums[:15])
            )
            return
        is_perso = "perso-" in forum.name
        is_correction = "corrections-" in forum.name
        if not (is_perso or is_correction):
            await ctx.send(f"❌ `{forum.name}` non supporté (perso-* / corrections-*)")
            return
        m_match = re.search(r"(?:perso|corrections)-([a-z0-9]+)",
                            forum.name, re.IGNORECASE)
        if not m_match:
            await ctx.send(f"❌ Impossible d'extraire la matière de `{forum.name}`")
            return
        matiere_low = m_match.group(1).lower()
        matiere = MATIERE_MAP.get(matiere_low, matiere_low.upper())
        await ctx.send(
            f"🔄 Resync `{forum.name}` "
            f"({'**APPLY**' if apply else 'dry-run'}) : matière `{matiere}`. "
            f"Indexation disque…"
        )
        disk_lookup = await asyncio.get_event_loop().run_in_executor(
            None, self._build_disk_lookup, matiere
        )
        await ctx.send(f"📁 Disque : **{len(disk_lookup)}** fichiers indexés.")
        if is_perso:
            tracking = load_discord_perso_published()
        else:
            tracking = load_discord_published_v2()
        all_threads: List[discord.Thread] = list(forum.threads)
        try:
            async for th in forum.archived_threads(limit=500):
                all_threads.append(th)
        except discord.HTTPException as e:
            log.warning(f"resync-tracking : archived_threads → {e}")
        await ctx.send(f"🔍 {len(all_threads)} thread(s) à examiner…")
        new_count = 0
        updated_count = 0
        skipped_already = 0
        skipped_unparseable: List[str] = []
        preview: List[str] = []
        for th in all_threads:
            parsed = parse_thread_title_full(th.name)
            if parsed is None:
                skipped_unparseable.append(th.name[:60])
                continue
            type_code, num, annee = parsed
            key = thread_key(matiere, type_code, num, annee)
            existing = tracking["threads"].get(key)
            content_field = "posts" if is_perso else "corrections"
            if existing and existing.get(content_field):
                skipped_already += 1
                continue
            if is_perso:
                entry = await self._build_perso_entry_from_thread(
                    th, matiere, type_code, num, annee, disk_lookup
                )
            else:
                entry = await self._build_correction_entry_from_thread(
                    th, matiere, type_code, num, annee, disk_lookup
                )
            n_files = len(entry.get(content_field, {}))
            if existing:
                updated_count += 1
                action = "UPDATE"
            else:
                new_count += 1
                action = "  NEW "
            preview.append(f"{action}  {key:38s} ({n_files} attachments matchés)")
            tracking["threads"][key] = entry
            await asyncio.sleep(0.5)
        lines = [
            f"### Resync `{forum.name}` : "
            f"{'**APPLY**' if apply else '**DRY RUN**'}",
            f"📊 NEW: **{new_count}** · UPDATE: **{updated_count}** · "
            f"déjà trackés: {skipped_already} · "
            f"non parsables: {len(skipped_unparseable)}",
        ]
        if preview:
            lines.append("\n**Preview (10 premiers)** :")
            for p in preview[:10]:
                lines.append(f"  `{p}`")
            if len(preview) > 10:
                lines.append(f"  … (+{len(preview) - 10} autres)")
        if skipped_unparseable:
            lines.append("\n**Titres non parsables (5 premiers)** :")
            for t in skipped_unparseable[:5]:
                lines.append(f"  • `{t}`")
            if len(skipped_unparseable) > 5:
                lines.append(f"  • … (+{len(skipped_unparseable) - 5} autres)")
        commit_changes = (new_count + updated_count) > 0
        if apply and commit_changes:
            target_path = (DISCORD_PERSO_PUBLISHED_JSON if is_perso
                           else DISCORD_PUBLISHED_JSON)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            bak_path = f"{target_path}.bak.{ts}"
            try:
                shutil.copy2(target_path, bak_path)
                lines.append(f"💾 Backup : `{os.path.basename(bak_path)}`")
            except Exception as e:
                lines.append(f"⚠️ Backup échoué : `{e}`")
            try:
                if is_perso:
                    save_discord_perso_published(tracking)
                else:
                    save_discord_published_v2(tracking)
                lines.append("✅ Tracking JSON mis à jour.")
            except Exception as e:
                lines.append(f"❌ Sauvegarde JSON échouée : `{e}`")
        elif apply:
            lines.append("ℹ️ Aucune entrée à ajouter ou mettre à jour.")
        else:
            lines.append("\nℹ️ Re-run avec `--apply` pour committer.")
        out = "\n".join(lines)
        await ctx.send(out[:1900])
        if len(out) > 1900:
            await ctx.send(f"…(+{len(out)-1900} chars tronqués dans le récap)")
        await self._log(
            f"🔄 Resync-tracking `{forum.name}` "
            f"({'apply' if apply else 'dry-run'}) : "
            f"{new_count} new, {updated_count} update, "
            f"{skipped_already} skip, {len(skipped_unparseable)} unparseable",
            color=LOG_COLOR_OK if commit_changes or not apply else LOG_COLOR_INFO,
            title="Resync tracking",
        )
    @cours.command(name="sync-absences", aliases=["syncabsences"])
    async def sync_absences(self, ctx: commands.Context):
        guild = self._get_guild()
        if guild is None:
            await ctx.send("❌ Serveur ISTIC L1 G2 introuvable.")
            return
        target_keywords = ("audio", "transcription", "résumé", "resume")
        channels = [
            ch for ch in guild.text_channels
            if any(k in ch.name.lower() for k in target_keywords)
        ]
        if not channels:
            await ctx.send("⚠️ Aucun salon audio/transcription/résumé trouvé.")
            return
        after = datetime(2025, 10, 1)
        per_channel_limit = 5000
        status = await ctx.send(
            f"🔄 Scan de l'historique Discord en cours... "
            f"({len(channels)} salons, depuis {after.date()}, max {per_channel_limit} msgs/salon)"
        )
        await self._log(
            f"🔄 `!cours sync-absences` lancé par {ctx.author} : {len(channels)} salon(s)",
            color=LOG_COLOR_INFO,
            title="Sync absences",
        )
        absences = load_absences()
        new_count = 0
        already_count = 0
        scanned_msgs = 0
        scanned_channels = 0
        errors: List[str] = []
        for ch in channels:
            scanned_channels += 1
            try:
                async for msg in ch.history(limit=per_channel_limit, after=after, oldest_first=True):
                    scanned_msgs += 1
                    content = msg.content or ""
                    if "[Pas d" not in content:
                        continue
                    m = self._ABSENCE_MSG_RE.search(content)
                    if not m:
                        continue
                    type_code = m.group("type").upper()
                    num = m.group("num")
                    matiere = m.group("matiere").upper()
                    date = m.group("date")
                    if matiere.lower() not in MATIERE_MAP:
                        continue
                    key = _absence_key(type_code, matiere, num)
                    if key in absences:
                        already_count += 1
                        if not absences[key].get("posted_discord"):
                            absences[key]["posted_discord"] = True
                    else:
                        absences[key] = {
                            "raison": "détecté via sync Discord",
                            "date": date,
                            "posted_discord": True,
                            "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
                        }
                        new_count += 1
            except discord.Forbidden:
                errors.append(f"#{ch.name} (accès refusé)")
            except Exception as e:
                errors.append(f"#{ch.name} ({str(e)[:80]})")
        save_absences(absences)
        try:
            await status.delete()
        except Exception:
            pass
        embed = discord.Embed(
            title="🔄 Sync absences : terminé",
            color=LOG_COLOR_OK if not errors else LOG_COLOR_WARN,
        )
        embed.add_field(name="Nouvelles absences", value=str(new_count), inline=True)
        embed.add_field(name="Déjà connues", value=str(already_count), inline=True)
        embed.add_field(name="Total en base", value=str(len(absences)), inline=True)
        embed.add_field(
            name="Scan",
            value=f"{scanned_channels} salon(s), {scanned_msgs} messages",
            inline=False,
        )
        if errors:
            embed.add_field(
                name="Erreurs",
                value="\n".join(errors)[:1000],
                inline=False,
            )
        await ctx.send(embed=embed)
        await self._log(
            f"✅ Sync absences : +{new_count} nouvelles, {already_count} déjà connues "
            f"({scanned_msgs} msgs dans {scanned_channels} salons)",
            color=LOG_COLOR_OK if not errors else LOG_COLOR_WARN,
            title="Sync absences terminé",
        )
    _ABSENCE_MSG_RE = re.compile(
        r"\[Pas d(?:'|e )(?P<kind>audio|texte|résumé|resume) "
        r"du (?P<type>CM|TD|TP)(?P<num>\d+) (?P<matiere>[A-Z]+\d*)"
        r"(?: (?P<date>\d{4}))?\]",
        re.IGNORECASE,
    )
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot and message.author.id != self.bot.user.id:
            pass
        if message.guild is None or message.guild.id != ISTIC_GUILD_ID:
            return
        m = self._ABSENCE_MSG_RE.search(message.content or "")
        if not m:
            return
        type_code = m.group("type").upper()
        num = m.group("num")
        matiere = m.group("matiere").upper()
        date = m.group("date")
        if matiere.lower() not in MATIERE_MAP:
            return
        key = _absence_key(type_code, matiere, num)
        absences = load_absences()
        already = key in absences
        if not already:
            mark_absent(
                type_code, matiere, num,
                date=date,
                raison="détecté via message Discord",
                posted_discord=True,
            )
            await self._log(
                f"🔎 Absence détectée via message : `{key}` "
                f"(canal <#{message.channel.id}>)",
                color=LOG_COLOR_INFO,
                title="Absence détectée",
            )
        else:
            entry = absences[key]
            if not entry.get("posted_discord"):
                entry["posted_discord"] = True
                save_absences(absences)
        try:
            await message.add_reaction("✅")
        except Exception:
            pass
    @commands.Cog.listener()
    async def on_ready(self):
        if self._startup_scan_done:
            return
        self._startup_scan_done = True
        try:
            await self.bot.wait_until_ready()
            await asyncio.sleep(5)
            await self._process_pending_commands()
        except Exception as e:
            log.error(f"Erreur pendant le scan de rattrapage : {e}")
    async def _process_pending_commands(self):
        guild = self._get_guild()
        if guild is None:
            return
        admin_role = guild.get_role(ADMIN_ROLE_ID)
        if admin_role is None:
            log.warning("Rôle admin introuvable : scan rattrapage annulé")
            return
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        pending: List[discord.Message] = []
        for ch in guild.text_channels:
            if not ch.permissions_for(guild.me).read_message_history:
                continue
            try:
                msgs = [m async for m in ch.history(limit=50, oldest_first=False)]
            except (discord.Forbidden, discord.HTTPException):
                continue
            msgs.reverse()
            for i, m in enumerate(msgs):
                content = (m.content or "").strip()
                if not content.startswith("!cours "):
                    continue
                if m.created_at < cutoff:
                    continue
                if m.author.bot:
                    continue
                member = m.author if isinstance(m.author, discord.Member) else guild.get_member(m.author.id)
                if member is None or admin_role not in member.roles:
                    continue
                following = msgs[i + 1:i + 3]
                bot_replied = any(f.author.id == self.bot.user.id for f in following)
                bot_reacted = any(
                    r.me for r in m.reactions if str(r.emoji) == "✅"
                )
                if bot_replied or bot_reacted:
                    continue
                pending.append(m)
        if not pending:
            return
        await self._log(
            f"📬 Scan terminé : {len(pending)} commande(s) en attente trouvée(s)",
            color=LOG_COLOR_INFO,
        )
        pending.sort(key=lambda m: m.created_at)
        for m in pending:
            ago = datetime.now(timezone.utc) - m.created_at
            hours = int(ago.total_seconds() // 3600)
            mins = int((ago.total_seconds() % 3600) // 60)
            time_ago = f"{hours}h{mins:02d}"
            await self._log(
                f"📬 Commande en attente : `{m.content[:200]}` "
                f"de {m.author} ({time_ago} plus tôt) dans <#{m.channel.id}>",
                color=LOG_COLOR_INFO,
                title="Rattrapage",
            )
            try:
                ctx = await self.bot.get_context(m)
                if ctx.valid and ctx.command is not None:
                    await self.bot.invoke(ctx)
                    try:
                        await m.add_reaction("✅")
                    except Exception:
                        pass
                else:
                    await self._log(
                        f"⚠️ Commande non reconnue : `{m.content[:200]}`",
                        color=LOG_COLOR_WARN,
                    )
            except Exception as e:
                log.error(f"Rattrapage échec pour {m.id}: {e}")
                await self._log(
                    f"❌ Échec rattrapage `{m.content[:150]}` : `{str(e)[:200]}`",
                    color=LOG_COLOR_ERROR,
                )
    _EX_PATTERNS = [
        re.compile(r"exercice\s+(\d+)", re.IGNORECASE),
        re.compile(r"\bexo\s+(\d+)", re.IGNORECASE),
        re.compile(r"le\s+petit\s+(\d+)", re.IGNORECASE),
        re.compile(r"num[ée]ro\s+(\d+)", re.IGNORECASE),
    ]
    @staticmethod
    def _sequence_gaps(nums: List[int]) -> List[int]:
        if not nums:
            return []
        lo, hi = min(nums), max(nums)
        found = set(nums)
        return [i for i in range(lo, hi + 1) if i not in found]
    def _collect_audio_for_matiere(self, matiere: str) -> Dict[str, List[int]]:
        out = {"CM": [], "TD": [], "TP": []}
        if not os.path.isdir(AUDIO_ROOT):
            return out
        for f in os.listdir(AUDIO_ROOT):
            m = _AUDIO_PATTERN.match(f)
            if not m:
                continue
            if m.group(3).upper() != matiere:
                continue
            t = m.group(1).upper()
            if t in out:
                out[t].append(int(m.group(2)))
        return out
    def _collect_transcripts_for_matiere(self, matiere: str) -> Dict[str, List[int]]:
        out = {"CM": [], "TD": [], "TP": []}
        matiere_dir = os.path.join(COURS_ROOT, matiere)
        if not os.path.isdir(matiere_dir):
            return out
        for type_code in ("CM", "TD", "TP"):
            tdir = os.path.join(matiere_dir, type_code)
            if not os.path.isdir(tdir):
                continue
            for root, _, files in os.walk(tdir):
                for fn in files:
                    if not fn.lower().endswith(".txt"):
                        continue
                    m = _TXT_PATTERN.match(fn)
                    if not m or m.group(3).upper() != matiere:
                        continue
                    if m.group(1).upper() == type_code:
                        out[type_code].append(int(m.group(2)))
        return out
    def _detect_exercises_in_td(self, td_dir: str) -> List[int]:
        trans = os.path.join(td_dir, "transcriptions")
        if not os.path.isdir(trans):
            return []
        found: set = set()
        for fn in os.listdir(trans):
            if not fn.lower().endswith(".txt"):
                continue
            try:
                with open(os.path.join(trans, fn), "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            for pat in self._EX_PATTERNS:
                for m in pat.finditer(text):
                    n_str = next((g for g in m.groups() if g), None)
                    if n_str:
                        try:
                            n = int(n_str)
                            if 1 <= n <= 50:
                                found.add(n)
                        except ValueError:
                            pass
        return sorted(found)
    def _build_matiere_report(self, matiere: str) -> Dict:
        audio = self._collect_audio_for_matiere(matiere)
        trans = self._collect_transcripts_for_matiere(matiere)
        absences = load_absences()
        published = load_published()
        abs_for_mat = {k: v for k, v in absences.items() if k.endswith(f"_{matiere}")}
        pub_for_mat = {k: v for k, v in published.items() if f"_{matiere}_" in k}
        cm_set = sorted(set(trans["CM"]) | set(audio["CM"]))
        td_set = sorted(set(trans["TD"]) | set(audio["TD"]))
        tp_set = sorted(set(trans["TP"]) | set(audio["TP"]))
        td_folders: Dict[int, Dict] = {}
        td_root = os.path.join(COURS_ROOT, matiere, "TD")
        if os.path.isdir(td_root):
            for entry in sorted(os.listdir(td_root)):
                m = re.match(r"^TD(\d+)$", entry)
                if not m:
                    continue
                td_num = m.group(1)
                folder = os.path.join(td_root, entry)
                if not has_enonce(folder, "TD", td_num, matiere):
                    continue
                exs = self._detect_exercises_in_td(folder)
                td_folders[int(td_num)] = {"exercises": exs, "path": folder}
        tp_corr_count = 0
        tp_total = 0
        tp_root = os.path.join(COURS_ROOT, matiere, "TP")
        if os.path.isdir(tp_root):
            for entry in os.listdir(tp_root):
                p = os.path.join(tp_root, entry)
                if not os.path.isdir(p) or entry.startswith("_"):
                    continue
                if re.match(r"^TP\d+$", entry, re.IGNORECASE):
                    tp_total += 1
                    corr = os.path.join(p, "corrections")
                    if os.path.isdir(corr) and any(
                        f.lower().endswith(".pdf") for f in os.listdir(corr)
                    ):
                        tp_corr_count += 1
        return {
            "matiere": matiere,
            "cm_nums": cm_set,
            "cm_audio": sorted(set(audio["CM"])),
            "cm_gaps": self._sequence_gaps(cm_set),
            "td_nums": td_set,
            "td_gaps": self._sequence_gaps(td_set),
            "tp_nums": tp_set,
            "tp_gaps": self._sequence_gaps(tp_set),
            "td_folders": td_folders,
            "tp_total": tp_total,
            "tp_corr": tp_corr_count,
            "absences": abs_for_mat,
            "published": pub_for_mat,
        }
    def _format_cm_field(self, r: Dict) -> str:
        nums = r["cm_nums"]
        audio = set(r["cm_audio"])
        pub = r["published"]
        abs_ = r["absences"]
        pub_cm = sum(1 for k in pub if k.startswith("CM"))
        abs_cm = sum(1 for k in abs_ if k.startswith("CM"))
        parts = [
            f"{len(nums)} transcriptions",
            f"{len(audio)} audios",
            f"{pub_cm} publiés",
            f"{abs_cm} absents",
        ]
        line = " · ".join(parts)
        if r["cm_gaps"]:
            line += f"\nTrous : CM{', CM'.join(str(n) for n in r['cm_gaps'])}"
        return line or "—"
    def _format_td_field(self, r: Dict) -> str:
        folders = r["td_folders"]
        if not folders:
            td_line = "aucune feuille TD avec énoncé PDF"
        else:
            details = []
            for num in sorted(folders):
                exs = folders[num]["exercises"]
                if exs:
                    details.append(f"TD{num}[{','.join(str(e) for e in exs)}]")
                else:
                    details.append(f"TD{num}[—]")
            td_line = "Feuilles : " + " · ".join(details)
        seq = r["td_nums"]
        extra = f"\n{len(seq)} séance(s) au total"
        if r["td_gaps"]:
            extra += f" · trous : TD{', TD'.join(str(n) for n in r['td_gaps'])}"
        return td_line + extra
    def _format_tp_field(self, r: Dict) -> str:
        parts = [f"{len(r['tp_nums'])} transcriptions"]
        if r["tp_total"]:
            parts.append(f"corrections : {r['tp_corr']}/{r['tp_total']}")
        if r["tp_gaps"]:
            parts.append(f"trous : TP{', TP'.join(str(n) for n in r['tp_gaps'])}")
        return " · ".join(parts) or "—"
    _CORRECTION_PAT = re.compile(r"correction_TD\d+_ex(\d+)_", re.IGNORECASE)
    def _compute_actions(self, matiere: str, r: Dict) -> List[Tuple[int, str]]:
        actions: List[Tuple[int, str]] = []
        matiere_lower = matiere.lower()
        type_lower_map = {v: k for k, v in TYPE_MAP.items()}
        ready = [
            s for s in scan_available(matiere_filter=matiere)
            if s["has_audio"] and s["has_transcript"]
        ]
        if len(ready) >= 3:
            actions.append((1,
                f"🟢 {len(ready)} séance(s) prêtes → `!cours auto {matiere_lower}`"
            ))
        else:
            for s in ready:
                tlow = type_lower_map.get(s["type"], s["type"].lower())
                actions.append((1,
                    f"🟢 Publier {s['type']}{s['num']} {matiere} → "
                    f"`!cours publish {tlow} {matiere_lower} {s['num']} {s['date']}`"
                ))
        for num in sorted(r["td_folders"]):
            folder = r["td_folders"][num]
            detected = set(folder["exercises"])
            corrected: set = set()
            corr_dir = os.path.join(folder["path"], "corrections")
            if os.path.isdir(corr_dir):
                for fn in os.listdir(corr_dir):
                    m = self._CORRECTION_PAT.search(fn)
                    if m:
                        try:
                            corrected.add(int(m.group(1)))
                        except ValueError:
                            pass
            to_correct = sorted(detected - corrected)
            if to_correct:
                actions.append((2,
                    f"📝 Corriger TD{num} ex {','.join(str(e) for e in to_correct)} "
                    f"({len(detected)} détecté(s), {len(corrected)} déjà fait(s))"
                ))
        for sub in ("TD", "TP"):
            atr = os.path.join(COURS_ROOT, matiere, sub, "_A_TRIER", "transcriptions")
            if os.path.isdir(atr):
                txts = [f for f in os.listdir(atr) if f.lower().endswith(".txt")]
                if txts:
                    actions.append((3,
                        f"📂 Ranger {len(txts)} transcription(s) {sub} dans `{matiere}/{sub}/_A_TRIER/`"
                    ))
        not_posted = [
            k for k, v in r["absences"].items() if not v.get("posted_discord")
        ]
        if not_posted:
            sample = ", ".join(not_posted[:3])
            more = f" (+{len(not_posted)-3})" if len(not_posted) > 3 else ""
            actions.append((4,
                f"🚫 {len(not_posted)} absence(s) à poster sur Discord : {sample}{more}"
            ))
        missing_enonces: List[str] = []
        for sub in ("TD", "TP"):
            sub_dir = os.path.join(COURS_ROOT, matiere, sub)
            if not os.path.isdir(sub_dir):
                continue
            for entry in sorted(os.listdir(sub_dir)):
                full = os.path.join(sub_dir, entry)
                if not os.path.isdir(full):
                    continue
                m_num = re.match(rf"^{sub}(\d+)$", entry, re.IGNORECASE)
                if not m_num:
                    continue
                if not has_enonce(full, sub, m_num.group(1), matiere):
                    missing_enonces.append(entry)
        if missing_enonces:
            actions.append((5,
                f"📄 Télécharger énoncé(s) : {', '.join(missing_enonces[:6])}"
                + (f" (+{len(missing_enonces)-6})" if len(missing_enonces) > 6 else "")
            ))
        if r["cm_gaps"]:
            actions.append((6,
                f"⚠️ Trou séquence CM : {', '.join(f'CM{n}' for n in r['cm_gaps'])}"
            ))
        if r["td_gaps"]:
            actions.append((6,
                f"⚠️ Trou séquence TD : {', '.join(f'TD{n}' for n in r['td_gaps'])}"
            ))
        if r["tp_gaps"]:
            actions.append((6,
                f"⚠️ Trou séquence TP : {', '.join(f'TP{n}' for n in r['tp_gaps'])}"
            ))
        actions.sort(key=lambda t: t[0])
        return actions
    def _build_actions_embed(self, matiere: str, actions: List[Tuple[int, str]]) -> discord.Embed:
        if not actions:
            embed = discord.Embed(
                title=f"📋 Actions : {matiere}",
                description="✅ Rien à faire pour le moment.",
                color=LOG_COLOR_OK,
            )
        else:
            shown = actions[:10]
            extra = len(actions) - len(shown)
            desc_lines = [line for _, line in shown]
            if extra > 0:
                desc_lines.append(f"… (+{extra} autre(s) action(s))")
            embed = discord.Embed(
                title=f"📋 Actions : {matiere}",
                description="\n".join(desc_lines),
                color=EMBED_COLORS.get(matiere, LOG_COLOR_INFO),
            )
        mapping_path = os.path.join(COURS_ROOT, matiere, "MAPPING_SEANCES.md")
        if os.path.isfile(mapping_path):
            embed.set_footer(text=f"Mapping disponible : {matiere}/MAPPING_SEANCES.md")
        return embed
    def _build_matiere_embed(self, r: Dict) -> discord.Embed:
        matiere = r["matiere"]
        embed = discord.Embed(
            title=f"📊 Rapport {matiere}",
            color=EMBED_COLORS.get(matiere, LOG_COLOR_DEFAULT),
        )
        embed.add_field(name="CM", value=self._format_cm_field(r) or "—", inline=False)
        embed.add_field(name="TD", value=self._format_td_field(r) or "—", inline=False)
        embed.add_field(name="TP", value=self._format_tp_field(r) or "—", inline=False)
        abs_count = len(r["absences"])
        pub_count = len(r["published"])
        total_eligible = len(r["cm_nums"]) + len(r["td_nums"]) + len(r["tp_nums"])
        embed.add_field(name="Absences", value=f"{abs_count} séance(s)", inline=True)
        embed.add_field(
            name="Publications",
            value=f"{pub_count}/{total_eligible} publiées" if total_eligible else "—",
            inline=True,
        )
        return embed
    async def _deep_analysis(self, matiere: str) -> Optional[Tuple[str, int, int]]:
        if not self._api_ok:
            return None
        cm_dir = os.path.join(COURS_ROOT, matiere, "CM")
        if not os.path.isdir(cm_dir):
            return None
        snippets: List[str] = []
        for fn in sorted(os.listdir(cm_dir)):
            if not fn.lower().endswith(".txt"):
                continue
            m = _TXT_PATTERN.match(fn)
            if not m or m.group(1).upper() != "CM" or m.group(3).upper() != matiere:
                continue
            try:
                with open(os.path.join(cm_dir, fn), "r", encoding="utf-8", errors="replace") as f:
                    snippets.append(f"--- {fn} ---\n{f.read(500).strip()}")
            except OSError:
                continue
        if not snippets:
            return None
        prompt = (
            f"Voici les débuts de {len(snippets)} transcriptions de CM pour {matiere}. "
            f"Identifie :\n"
            f"- Les thèmes couverts par chaque CM\n"
            f"- Les thèmes qui semblent manquer dans la séquence (trous logiques)\n"
            f"- Les recoupements entre CM et TD si possible\n"
            f"Réponds en 10 lignes max.\n\n"
            + "\n\n".join(snippets)
        )
        loop = asyncio.get_event_loop()
        def _call() -> Tuple[str, int, int]:
            client = anthropic.Anthropic(api_key=API_KEY)
            resp = client.messages.create(
                model=SUMMARY_MODEL,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
            return text.strip(), resp.usage.input_tokens, resp.usage.output_tokens
        try:
            return await loop.run_in_executor(None, _call)
        except Exception as e:
            log.error(f"deep analysis {matiere}: {e}")
            return None
    @cours.command(name="rapport")
    async def rapport(self, ctx: commands.Context, *, args: str = ""):
        parts = args.split()
        deep = "--deep" in parts
        parts = [p for p in parts if p != "--deep"]
        matiere: Optional[str] = parts[0] if parts else None
        if matiere and matiere.lower() not in MATIERE_MAP:
            await ctx.send(
                f"❌ Matière invalide `{matiere}`. Valeurs : {', '.join(MATIERE_MAP.keys())}"
            )
            return
        matieres_list = [MATIERE_MAP[matiere.lower()]] if matiere else list(MATIERE_MAP.values())
        start_time = datetime.now(timezone.utc)
        await self._log(
            f"📊 `!cours rapport {matiere or 'global'}{' --deep' if deep else ''}` lancé par {ctx.author}",
            color=LOG_COLOR_INFO,
            title="Rapport lancé",
        )
        await ctx.send(
            f"🔄 Génération du rapport {'approfondi ' if deep else ''}"
            f"({', '.join(matieres_list)})..."
        )
        loop = asyncio.get_event_loop()
        total_api_cost = 0.0
        for mat in matieres_list:
            mat_start = datetime.now(timezone.utc)
            await self._log(
                f"📊 Analyse de **{mat}** en cours...",
                color=LOG_COLOR_INFO,
            )
            r = await loop.run_in_executor(None, self._build_matiere_report, mat)
            actions = await loop.run_in_executor(None, self._compute_actions, mat, r)
            mat_duration = (datetime.now(timezone.utc) - mat_start).total_seconds()
            report_embed = self._build_matiere_embed(r)
            report_embed.set_footer(text=f"Pipeline COURS · {mat_duration:.1f}s")
            await ctx.send(embed=report_embed)
            await ctx.send(embed=self._build_actions_embed(mat, actions))
            await self._log(
                f"📊 **{mat}** terminé en {mat_duration:.1f}s\n"
                f"• {len(r['cm_nums'])} CM ({len(r['cm_audio'])} audios) | "
                f"trous : {', '.join(f'CM{n}' for n in r['cm_gaps']) or 'aucun'}\n"
                f"• {len(r['td_nums'])} TD séances | "
                f"feuilles : {', '.join(f'TD{n}' for n in r['td_folders']) or 'aucune'}\n"
                f"• {len(r['tp_nums'])} TP | corrections : {r['tp_corr']}/{r['tp_total']}\n"
                f"• Absences : {len(r['absences'])} | Publiés : {len(r['published'])}\n"
                f"• Actions à faire : {len(actions)}",
                color=LOG_COLOR_OK,
                title=f"Rapport {mat}",
            )
            if deep:
                api_start = datetime.now(timezone.utc)
                await self._log(
                    f"🧠 Appel API pour analyse approfondie de **{mat}**...",
                    color=LOG_COLOR_INFO,
                )
                result = await self._deep_analysis(mat)
                api_duration = (datetime.now(timezone.utc) - api_start).total_seconds()
                if result is None:
                    await ctx.send(f"⚠️ Analyse IA indisponible pour {mat}.")
                    await self._log(
                        f"⚠️ Analyse IA **{mat}** indisponible après {api_duration:.1f}s",
                        color=LOG_COLOR_WARN,
                    )
                    continue
                text, in_tok, out_tok = result
                cost_usd = (in_tok / 1e6) * COST_INPUT_PER_1M + (out_tok / 1e6) * COST_OUTPUT_PER_1M
                cost_eur = cost_usd * USD_TO_EUR
                total_api_cost += cost_eur
                deep_embed = discord.Embed(
                    title=f"🧠 Analyse IA : {mat}",
                    description=text[:4000],
                    color=EMBED_COLORS.get(mat, LOG_COLOR_INFO),
                )
                deep_embed.set_footer(
                    text=f"API Anthropic · {in_tok}+{out_tok} tok · ≈{cost_eur:.4f}€ · {api_duration:.1f}s"
                )
                await ctx.send(embed=deep_embed)
                await self._log(
                    f"🧠 Analyse IA **{mat}** terminée en {api_duration:.1f}s\n"
                    f"💰 {in_tok} in + {out_tok} out ≈ {cost_eur:.4f}€",
                    color=LOG_COLOR_OK,
                    title=f"Analyse IA {mat}",
                )
        total_duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        summary = f"📊 Rapport terminé en {total_duration:.1f}s ({len(matieres_list)} matière(s))"
        if deep and total_api_cost > 0:
            summary += f"\n💰 Coût API total : {total_api_cost:.4f}€"
        await self._log(summary, color=LOG_COLOR_OK, title="Rapport terminé")
    _INBOX_PATTERN = re.compile(
        r"^(CM|TD|TP)(\d+)[_ ]([A-Z]+\d*)[_ ](\d{4})\.(txt|m4a|pdf|docx)$",
        re.IGNORECASE,
    )
    @staticmethod
    def _md5(path: str) -> str:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    def _resolve_inbox_destination(self, filename: str) -> Optional[str]:
        if filename == "Triche QUIZ1 PRG2.txt":
            return os.path.join(COURS_ROOT, "PRG2", "CC", "Triche_QUIZ1_PRG2.txt")
        m = self._INBOX_PATTERN.match(filename)
        if not m:
            return None
        type_code = m.group(1).upper()
        num = m.group(2)
        matiere = m.group(3).upper()
        date = m.group(4)
        ext = m.group(5).lower()
        if matiere.lower() not in MATIERE_MAP:
            return None
        canonical = f"{type_code}{num}_{matiere}_{date}.{ext}"
        if type_code == "CM":
            return os.path.join(COURS_ROOT, matiere, "CM", canonical)
        if type_code == "TD":
            return os.path.join(
                COURS_ROOT, matiere, "TD", "_A_TRIER", "transcriptions", canonical
            )
        if type_code == "TP":
            return os.path.join(COURS_ROOT, matiere, "TP", canonical)
        return None
    def _process_inbox_file(
        self, filepath: str
    ) -> Tuple[Optional[str], Optional[Dict[str, str]]]:
        filename = os.path.basename(filepath)
        dst = self._resolve_inbox_destination(filename)
        if dst is None:
            return None, None
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst):
            try:
                if self._md5(filepath) == self._md5(dst):
                    os.remove(filepath)
                    return (
                        f"🗑 doublon identique supprimé : `{filename}` "
                        f"(déjà dans `{os.path.relpath(dst, COURS_ROOT)}`)"
                    ), None
            except OSError:
                pass
            base, ext = os.path.splitext(dst)
            dst = f"{base}_from_INBOX{ext}"
        try:
            shutil.move(filepath, dst)
        except (OSError, shutil.Error) as e:
            return f"❌ échec déplacement `{filename}`: {e}", None
        msg = f"📂 `{filename}` → `{os.path.relpath(dst, COURS_ROOT)}`"
        auto_target: Optional[Dict[str, str]] = None
        m = self._INBOX_PATTERN.match(filename)
        if m:
            type_code = m.group(1).upper()
            matiere = m.group(3).upper()
            ext = m.group(5).lower()
            if type_code == "CM" and ext == "txt" and matiere.lower() in MATIERE_MAP:
                auto_target = {
                    "type": type_code,
                    "matiere": matiere,
                    "num": m.group(2),
                    "date": m.group(4),
                }
        return msg, auto_target
    async def _auto_publish_cm(self, matiere: str, num: str, date: str) -> None:
        matiere_upper = matiere.upper()
        key = _session_key("CM", matiere_upper, num, date)
        published = load_published()
        entry = published.get(key, {})
        steps_done = sum(
            1 for k in ("audio", "transcription", "resume") if entry.get(k)
        )
        label = f"CM{num} {matiere_upper} ({date})"
        if steps_done == 3:
            log.info(f"Auto-publication {label} : déjà complète, skip.")
            return
        if 0 < steps_done < 3:
            await self._log(
                f"⚠️ Auto-publication {label} ignorée : {steps_done}/3 étapes "
                f"déjà publiées. Utilise `!cours publish cm {matiere.lower()} "
                f"{num} {date}` ou `!cours republish` à la main si besoin.",
                color=LOG_COLOR_WARN,
                title="Auto-publication",
            )
            return
        await self._log(
            f"🚀 Auto-publication déclenchée pour **{label}** "
            f"(transcription rangée par watchdog _INBOX)",
            color=LOG_COLOR_INFO,
            title="Auto-publication",
        )
        headless = _HeadlessCtx(self.bot)
        await self._publish_classic(headless, "cm", matiere.lower(), num, date)
        await self._log(
            f"✅ Auto-publication terminée pour {label}",
            color=LOG_COLOR_OK,
            title="Auto-publication",
        )
    @tasks.loop(seconds=60)
    async def _inbox_watcher(self):
        try:
            results, auto_targets = await asyncio.get_event_loop().run_in_executor(
                None, self._scan_inbox_once_sync
            )
        except Exception as e:
            log.error(f"Watchdog _INBOX erreur : {e}")
            return
        for msg in results:
            color = LOG_COLOR_OK if msg.startswith("📂") else LOG_COLOR_WARN
            if msg.startswith("❌"):
                color = LOG_COLOR_ERROR
            await self._log(msg, color=color, title="Watchdog _INBOX")
        for tgt in auto_targets:
            try:
                await self._auto_publish_cm(
                    tgt["matiere"], tgt["num"], tgt["date"]
                )
            except Exception as e:
                log.error(f"Auto-publication CM {tgt} échouée : {e}")
                await self._log(
                    f"❌ Auto-publication échouée pour "
                    f"CM{tgt['num']} {tgt['matiere']} ({tgt['date']}) : `{str(e)[:200]}`",
                    color=LOG_COLOR_ERROR,
                    title="Auto-publication",
                )
    def _scan_inbox_once_sync(
        self,
    ) -> Tuple[List[str], List[Dict[str, str]]]:
        inbox = os.path.join(COURS_ROOT, "_INBOX")
        if not os.path.isdir(inbox):
            return [], []
        results: List[str] = []
        auto_targets: List[Dict[str, str]] = []
        current_sizes: Dict[str, int] = {}
        try:
            entries = os.listdir(inbox)
        except OSError:
            return [], []
        for name in entries:
            path = os.path.join(inbox, name)
            if not os.path.isfile(path):
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            current_sizes[path] = size
            previous = self._inbox_last_sizes.get(path)
            if previous is None or previous != size:
                continue
            msg, auto_target = self._process_inbox_file(path)
            if msg:
                results.append(msg)
            if auto_target:
                auto_targets.append(auto_target)
        self._inbox_last_sizes = {
            p: s for p, s in current_sizes.items() if os.path.isfile(p)
        }
        return results, auto_targets
    @_inbox_watcher.before_loop
    async def _before_inbox_watcher(self):
        await self.bot.wait_until_ready()
        if not self._inbox_watcher_logged:
            self._inbox_watcher_logged = True
            await self._log(
                "📂 Watchdog _INBOX activé (scan toutes les 60 s)",
                color=LOG_COLOR_INFO,
                title="Watchdog démarré",
            )
    async def _purge_tracked_thread(
        self,
        guild: discord.Guild,
        kind: str,
        matiere: str,
        type_code: str,
        num: str,
        annee: Optional[str],
    ) -> str:
        if kind == "perso":
            data = load_discord_perso_published()
            saver = save_discord_perso_published
        else:
            data = load_discord_published_v2()
            saver = save_discord_published_v2
        key = thread_key(matiere, type_code, num, annee)
        entry = data["threads"].get(key)
        if not entry:
            return f"⚠️ Purge demandée mais aucun tracking pour `{key}` : skip"
        thread_id_raw = entry.get("thread_id")
        if not thread_id_raw:
            data["threads"].pop(key, None)
            saver(data)
            return f"⚠️ Tracking `{key}` sans thread_id : entrée purgée du JSON"
        try:
            tid = int(thread_id_raw)
        except (TypeError, ValueError):
            data["threads"].pop(key, None)
            saver(data)
            return f"⚠️ thread_id `{thread_id_raw}` invalide : entrée purgée du JSON"
        deleted = False
        try:
            ch = await guild.fetch_channel(tid)
            if isinstance(ch, discord.Thread):
                await ch.delete(reason=f"Purge avant repost officiel ({kind})")
                deleted = True
            else:
                log.info(f"_purge_tracked_thread : channel {tid} n'est pas un Thread")
        except discord.NotFound:
            log.info(f"_purge_tracked_thread : thread {tid} déjà absent (404)")
        except discord.Forbidden as e:
            log.warning(f"_purge_tracked_thread : Forbidden sur thread {tid}: {e}")
        except discord.HTTPException as e:
            log.warning(f"_purge_tracked_thread : HTTPException {tid}: {e}")
        data["threads"].pop(key, None)
        saver(data)
        marker = "🗑 thread supprimé" if deleted else "⚠️ thread déjà absent"
        return f"{marker}, tracking `{key}` purgé"
    async def _publish_official(
        self,
        kind: str,
        matiere: str,
        type_code: str,
        num: str,
        annee: Optional[str] = None,
        exo: Optional[str] = None,
        force_republish: bool = False,
        purge_existing: bool = False,
    ) -> str:
        guild = self._get_guild()
        if guild is None:
            return "❌ Serveur introuvable (pipeline officiel)"
        purge_msg = ""
        if purge_existing:
            purge_msg = await self._purge_tracked_thread(
                guild, kind, matiere, type_code, num, annee
            )
        try:
            if kind == "perso":
                r = await self._do_publish_perso(
                    guild, matiere, type_code, num, annee,
                    force_republish=force_republish,
                )
                msg = self._format_official_perso_result(
                    matiere, type_code, num, annee, r
                )
                return f"{purge_msg} · {msg}" if purge_msg else msg
            if kind == "correction":
                r = await self._do_publish_correction(
                    guild, matiere, type_code, num, exo, annee,
                    force_republish=force_republish,
                )
                msg = self._format_official_correction_result(
                    matiere, type_code, num, exo, annee, r
                )
                return f"{purge_msg} · {msg}" if purge_msg else msg
            if kind == "enonce":
                r = await self._do_publish_enonce(
                    guild, matiere, type_code, num, annee,
                    force_republish=force_republish,
                )
                msg = self._format_official_enonce_result(
                    matiere, type_code, num, annee, r
                )
                return f"{purge_msg} · {msg}" if purge_msg else msg
            return f"❌ kind `{kind}` non supporté en routage officiel"
        except Exception as e:
            log.exception(
                f"_publish_official crash ({kind} {matiere} {type_code}{num})"
            )
            return f"❌ Pipeline officiel crash `{kind}` : {str(e)[:200]}"
    @staticmethod
    def _format_official_perso_result(
        matiere: str, type_code: str, num: str,
        annee: Optional[str], r: dict,
    ) -> str:
        head = f"{matiere} {type_code}{num}" + (f" {annee}" if annee else "")
        if r.get("status") == "no_material":
            return (
                f"⚠️ `_do_publish_perso` ({head}) : aucun matériel disque "
                f"détecté. Rien à publier."
            )
        ok = r.get("ok", 0)
        ok_v2 = r.get("ok_v2", 0)
        skip = r.get("skip_same_md5", 0)
        skip_no = r.get("skip_no_file", 0)
        skip_big = r.get("skip_too_big", 0)
        errors = r.get("errors", 0)
        thread_url = r.get("thread_url") or ""
        new_thread = " (thread créé)" if r.get("was_thread_created") else ""
        url_suffix = f" : {thread_url}" if thread_url else ""
        return (
            f"✅ Pipeline officiel `_do_publish_perso` ({head}){new_thread} · "
            f"OK: {ok} · V2: {ok_v2} · skip MD5: {skip} · "
            f"no-file: {skip_no} · too-big: {skip_big} · errors: {errors}"
            f"{url_suffix}"
        )
    @staticmethod
    def _format_official_correction_result(
        matiere: str, type_code: str, num: str,
        exo: Optional[str], annee: Optional[str], r: dict,
    ) -> str:
        head = f"{matiere} {type_code}{num}"
        if exo and exo != "0":
            head += f" ex{exo}"
        if annee:
            head += f" {annee}"
        status = r.get("status") or "?"
        thread_url = r.get("thread_url") or ""
        version = r.get("version")
        ver_part = f" · v{version}" if version else ""
        url_suffix = f" : {thread_url}" if thread_url else ""
        emoji = "✅" if status in {"ok", "ok_v2"} else (
            "⏭" if status == "skip_same_md5" else "❌"
        )
        return (
            f"{emoji} Pipeline officiel `_do_publish_correction` ({head}) · "
            f"status={status}{ver_part}{url_suffix}"
        )
    @staticmethod
    def _format_official_enonce_result(
        matiere: str, type_code: str, num: str,
        annee: Optional[str], r: dict,
    ) -> str:
        head = f"{matiere} {type_code}{num}" + (f" {annee}" if annee else "")
        status = r.get("status") or "?"
        thread_url = r.get("thread_url") or ""
        version = r.get("version")
        ver_part = f" · v{version}" if version else ""
        url_suffix = f" : {thread_url}" if thread_url else ""
        emoji = "✅" if status in {"ok", "ok_v2"} else (
            "⏭" if status == "skip_same_md5" else "❌"
        )
        return (
            f"{emoji} Pipeline officiel `_do_publish_enonce` ({head}) · "
            f"status={status}{ver_part}{url_suffix}"
        )
    async def _publish_freeform(
        self,
        kind: str,
        matiere: Optional[str],
        title: str,
        description: Optional[str],
        files: List[Dict[str, str]],
        purge_existing: bool = False,
        target_thread_id: Optional[int] = None,
    ) -> str:
        guild = self._get_guild()
        if guild is None:
            return "❌ Serveur introuvable"
        forum: Optional[discord.ForumChannel] = None
        if kind == "perso":
            if not matiere:
                return "❌ matiere requise pour kind=perso"
            forum = find_perso_forum(guild, matiere.upper())
            if forum is None:
                return f"❌ Forum perso-{matiere.lower()} introuvable"
        elif kind == "off-topic":
            forum = find_hors_sujets_forum(guild)
            if forum is None:
                return f"❌ Forum {HORS_SUJETS_FORUM_NAME} introuvable (lance `!cours setup-perso`)"
        elif kind == "correction":
            if not matiere:
                return "❌ matiere requise pour kind=correction"
            forum = find_correction_forum(guild, matiere.upper())
            if forum is None:
                return f"❌ Forum corrections-{matiere.lower()} introuvable (lance `!cours setup-forums`)"
        else:
            return f"❌ kind inconnu : {kind}"
        target_thread: Optional[discord.Thread] = None
        if target_thread_id is not None:
            for th in forum.threads:
                if th.id == target_thread_id:
                    target_thread = th
                    break
            if target_thread is None:
                try:
                    async for th in forum.archived_threads(limit=300):
                        if th.id == target_thread_id:
                            target_thread = th
                            break
                except Exception:
                    pass
        if target_thread is None:
            for th in forum.threads:
                if th.name.strip().lower() == title.strip().lower():
                    target_thread = th
                    break
        if target_thread is None:
            try:
                async for th in forum.archived_threads(limit=200):
                    if th.name.strip().lower() == title.strip().lower():
                        target_thread = th
                        break
            except Exception:
                pass
        purged = False
        if purge_existing and target_thread is not None:
            try:
                old_id = target_thread.id
                await target_thread.delete(
                    reason=f"Publish queue purge_existing ({kind})"
                )
                purged = True
                target_thread = None
                await asyncio.sleep(1.5)
                log.info(f"Purged thread #{old_id} (titre: {title!r})")
            except discord.Forbidden:
                return f"❌ Forbidden lors de la purge du thread `{title}`"
            except discord.HTTPException as e:
                return f"❌ Erreur purge thread : {str(e)[:200]}"
        was_created = False
        if target_thread is None:
            opening = description or f"Thread auto-créé par publish queue ({kind})."
            try:
                created = await forum.create_thread(
                    name=title[:100],
                    content=opening[:1900],
                    reason=f"Publish queue ({kind}{', after purge' if purged else ''})",
                )
                target_thread = created.thread
                was_created = True
            except discord.Forbidden:
                return f"❌ Forbidden lors de la création du thread `{title}`"
            except discord.HTTPException as e:
                return f"❌ Erreur création thread : {str(e)[:200]}"
            await asyncio.sleep(2)
        posted = 0
        skipped: List[str] = []
        for entry in files:
            path = entry.get("path", "")
            label = entry.get("label", os.path.basename(path) or "fichier")
            if not path or not os.path.isfile(path):
                skipped.append(f"{label} (introuvable)")
                continue
            size = os.path.getsize(path)
            try:
                if size > DISCORD_FILE_LIMIT:
                    await target_thread.send(
                        f"{label}\n⚠️ Fichier trop lourd ({size/1024/1024:.1f} Mo), "
                        f"disponible sur demande."
                    )
                else:
                    f = discord.File(path, filename=os.path.basename(path))
                    await target_thread.send(label, file=f)
                posted += 1
                await asyncio.sleep(2)
            except discord.HTTPException as e:
                skipped.append(f"{label} ({str(e)[:80]})")
        applied_labels: List[str] = []
        type_code = infer_type_code_from_title(title)
        if type_code is None:
            log.info(
                f"_publish_freeform : type non inférable depuis le titre "
                f"{title!r} → aucun tag appliqué (kind={kind})"
            )
        elif kind == "perso":
            try:
                applied_labels = await self._apply_perso_thread_tags(
                    target_thread,
                    type_code,
                    infer_perso_materiel_kinds(files),
                )
            except Exception as e:
                log.warning(f"_publish_freeform tags perso : {e}")
        elif kind == "correction":
            kinds_lower = {(e.get("kind", "") or "").strip().lower()
                           for e in files}
            if "correction" in kinds_lower:
                state = "corrections_present"
            elif kinds_lower == {"enonce"}:
                state = "enonce_only"
            else:
                state = "corrections_present"
            try:
                applied_labels = await apply_thread_tags(
                    target_thread, type_code, state
                )
            except Exception as e:
                log.warning(f"_publish_freeform tags correction : {e}")
        if purged and was_created:
            action = "purgé puis recréé"
        elif was_created:
            action = "créé"
        else:
            action = "retrouvé"
        msg = (
            f"✅ Thread {action} : <#{target_thread.id}> · "
            f"{posted}/{len(files)} fichiers postés"
        )
        if applied_labels:
            msg += f" · 🏷 {', '.join(applied_labels)}"
        if skipped:
            msg += f" · skips : {', '.join(skipped)[:150]}"
        return msg
    def _scan_publish_queue_sync(self) -> List[str]:
        if not os.path.isdir(PUBLISH_QUEUE_DIR):
            return []
        out: List[str] = []
        for name in os.listdir(PUBLISH_QUEUE_DIR):
            if not name.endswith(".json"):
                continue
            p = os.path.join(PUBLISH_QUEUE_DIR, name)
            if os.path.isfile(p):
                out.append(p)
        return out
    async def _process_queue_manifest(self, manifest_path: str) -> str:
        manifest_name = os.path.basename(manifest_path)
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            return f"❌ Manifest illisible `{manifest_name}` : {e}"
        kind = (data.get("kind", "") or "").strip().lower()
        matiere = data.get("matiere")
        title = data.get("title") or "Sans titre"
        description = data.get("description")
        files = data.get("files") or []
        session_report = data.get("session_report")
        created_by = data.get("created_by") or "?"
        purge_existing = bool(data.get("purge_existing", False))
        target_thread_id_raw = data.get("target_thread_id")
        target_thread_id: Optional[int] = None
        if target_thread_id_raw is not None:
            try:
                target_thread_id = int(target_thread_id_raw)
            except (TypeError, ValueError):
                target_thread_id = None
        type_code = data.get("type")
        num = data.get("num")
        annee = data.get("annee")
        exo = data.get("exo")
        force_republish = bool(data.get("force_republish", False))
        can_route_official = (
            kind in {"perso", "correction", "enonce"}
            and matiere
            and type_code
            and num
        )
        if kind == "correction" and exo in (None, ""):
            can_route_official = False
        if can_route_official:
            msg = await self._publish_official(
                kind=kind,
                matiere=str(matiere).upper(),
                type_code=str(type_code),
                num=str(num),
                annee=str(annee) if annee else None,
                exo=str(exo) if exo is not None else None,
                force_republish=force_republish,
                purge_existing=purge_existing,
            )
        else:
            msg = await self._publish_freeform(
                kind=kind,
                matiere=matiere,
                title=title,
                description=description,
                files=files,
                purge_existing=purge_existing,
                target_thread_id=target_thread_id,
            )
        os.makedirs(PUBLISH_QUEUE_DONE, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        archive_name = f"{ts}__{manifest_name}"
        archive_path = os.path.join(PUBLISH_QUEUE_DONE, archive_name)
        try:
            shutil.move(manifest_path, archive_path)
        except OSError as e:
            return f"{msg} (mais archivage manifest a echoué : {e})"
        if session_report:
            try:
                await self._post_session_report_embed(
                    title=title,
                    kind=kind,
                    matiere=matiere,
                    created_by=created_by,
                    publish_msg=msg,
                    report=session_report,
                )
            except Exception:
                log.exception("Embed session_report a crashé (non-bloquant)")
        return f"{msg} (manifest archivé : `_done/{archive_name}`)"
    async def _post_session_report_embed(
        self,
        title: str,
        kind: str,
        matiere: Optional[str],
        created_by: str,
        publish_msg: str,
        report: str,
    ) -> None:
        forum_label = (
            f"perso-{matiere.lower()}" if (kind == "perso" and matiere)
            else HORS_SUJETS_FORUM_NAME if kind == "off-topic"
            else "?"
        )
        report_trunc = report.strip()
        if len(report_trunc) > 3800:
            report_trunc = report_trunc[:3800] + "\n[…tronqué]"
        embed = discord.Embed(
            title=f"📨 Pipeline transcription terminé : {title[:80]}",
            description=f"```\n{report_trunc}\n```",
            color=LOG_COLOR_OK,
        )
        embed.add_field(name="Type", value=kind, inline=True)
        embed.add_field(name="Forum cible", value=forum_label, inline=True)
        embed.add_field(name="Source", value=f"`{created_by}`", inline=True)
        embed.add_field(name="Statut publication", value=publish_msg[:1000],
                        inline=False)
        embed.timestamp = datetime.utcnow()
        guild = self._get_guild()
        if guild is None:
            return
        logs_channel = discord.utils.get(guild.text_channels, name="logs")
        if logs_channel is None:
            return
        try:
            await logs_channel.send(embed=embed)
        except discord.HTTPException:
            log.exception("Échec envoi embed session_report")
    @tasks.loop(seconds=60)
    async def _publish_queue_watcher(self):
        try:
            manifests = await asyncio.get_event_loop().run_in_executor(
                None, self._scan_publish_queue_sync
            )
        except Exception as e:
            log.error(f"Watcher publish_queue erreur : {e}")
            return
        if not manifests:
            return
        for manifest_path in manifests:
            try:
                msg = await self._process_queue_manifest(manifest_path)
            except Exception as e:
                log.exception("Manifest crash")
                msg = f"❌ Crash manifest `{os.path.basename(manifest_path)}` : {str(e)[:200]}"
            color = LOG_COLOR_OK if msg.startswith("✅") else LOG_COLOR_WARN
            if msg.startswith("❌"):
                color = LOG_COLOR_ERROR
            await self._log(msg, color=color, title="Publish queue")
    @_publish_queue_watcher.before_loop
    async def _before_publish_queue_watcher(self):
        await self.bot.wait_until_ready()
        os.makedirs(PUBLISH_QUEUE_DIR, exist_ok=True)
        os.makedirs(PUBLISH_QUEUE_DONE, exist_ok=True)
        if not self._publish_queue_logged:
            self._publish_queue_logged = True
            await self._log(
                "📨 Watcher publish queue activé (scan `_publish_queue/` "
                "toutes les 60 s)",
                color=LOG_COLOR_INFO,
                title="Watcher démarré",
            )
    @cours.command(name="inbox")
    async def inbox(self, ctx: commands.Context):
        self._inbox_last_sizes = {}
        inbox = os.path.join(COURS_ROOT, "_INBOX")
        if not os.path.isdir(inbox):
            await ctx.send("⚠️ Dossier `_INBOX` introuvable.")
            return
        try:
            entries = [f for f in os.listdir(inbox)
                       if os.path.isfile(os.path.join(inbox, f))]
        except OSError as e:
            await ctx.send(f"❌ Lecture impossible : {e}")
            return
        if not entries:
            await ctx.send("✅ `_INBOX` est déjà vide.")
            return
        await ctx.send(f"🔍 Scan forcé de `_INBOX` ({len(entries)} fichier(s))...")
        await asyncio.get_event_loop().run_in_executor(None, self._scan_inbox_once_sync)
        results, auto_targets = await asyncio.get_event_loop().run_in_executor(
            None, self._scan_inbox_once_sync
        )
        if not results:
            await ctx.send("Aucun fichier rangé (patterns non reconnus).")
            return
        for msg in results:
            color = LOG_COLOR_OK if msg.startswith("📂") else LOG_COLOR_WARN
            if msg.startswith("❌"):
                color = LOG_COLOR_ERROR
            await self._log(msg, color=color, title="Watchdog _INBOX (forcé)")
        summary = discord.Embed(
            title=f"📂 Scan forcé : {len(results)} action(s)",
            description="\n".join(results)[:3800],
            color=LOG_COLOR_OK,
        )
        await ctx.send(embed=summary)
        for tgt in auto_targets:
            try:
                await self._auto_publish_cm(
                    tgt["matiere"], tgt["num"], tgt["date"]
                )
            except Exception as e:
                log.error(f"Auto-publication CM {tgt} échouée : {e}")
                await self._log(
                    f"❌ Auto-publication échouée pour "
                    f"CM{tgt['num']} {tgt['matiere']} ({tgt['date']}) : `{str(e)[:200]}`",
                    color=LOG_COLOR_ERROR,
                    title="Auto-publication",
                )
    async def _corrections_watcher_loop(self):
        log.info(
            f"Corrections watcher : démarrage "
            f"(polling {WATCHER_CORRECTIONS_INTERVAL_SECONDS}s)"
        )
        self.corrections_watcher_running = True
        try:
            while self.corrections_watcher_running:
                try:
                    await self._corrections_watcher_tick()
                except asyncio.CancelledError:
                    log.info("Corrections watcher : annulation reçue")
                    self.corrections_watcher_running = False
                    raise
                except Exception as e:
                    log.error(
                        f"Corrections watcher tick : exception {e}",
                        exc_info=True,
                    )
                try:
                    await asyncio.sleep(WATCHER_CORRECTIONS_INTERVAL_SECONDS)
                except asyncio.CancelledError:
                    log.info("Corrections watcher : annulation pendant sleep")
                    self.corrections_watcher_running = False
                    raise
        finally:
            self.corrections_watcher_running = False
            log.info("Corrections watcher : boucle terminée")
    async def _corrections_watcher_tick(self):
        guild = self._get_guild()
        if guild is None:
            return
        loop = asyncio.get_event_loop()
        for matiere in MATIERE_MAP.values():
            pattern = os.path.join(
                COURS_ROOT, matiere, "**", "corrections", "*.pdf"
            )
            try:
                pdfs = await loop.run_in_executor(
                    None, lambda p=pattern: glob.glob(p, recursive=True)
                )
            except Exception as e:
                log.warning(f"Watcher scan {matiere} : {e}")
                continue
            for pdf in sorted(pdfs):
                pn = parse_correction_filename(pdf)
                if pn is None:
                    continue
                try:
                    r = await self._do_publish_correction(
                        guild, matiere,
                        pn["type_code"], pn["num"], pn["exo"],
                        annee=pn.get("annee"),
                    )
                except Exception as e:
                    log.error(f"Watcher publish {pdf}: {e}", exc_info=True)
                    continue
                status = r.get("status")
                if status in ("ok", "ok_v2"):
                    self._tally_publication(matiere, status)
                    log.info(
                        f"Watcher : {status} publié {pdf_rel_key(pdf)} "
                        f"(thread {r.get('thread_id')})"
                    )
        await self._maybe_send_daily_recap()
    def _tally_publication(self, matiere: str, status: str) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.corrections_today_date != today:
            self.corrections_today_count = {}
            self.corrections_today_date = today
        bucket = self.corrections_today_count.setdefault(
            matiere, {"ok": 0, "ok_v2": 0}
        )
        bucket[status] = bucket.get(status, 0) + 1
    async def _maybe_send_daily_recap(self) -> None:
        now = datetime.now(timezone.utc)
        if now.hour != WATCHER_DAILY_RECAP_HOUR_UTC:
            return
        if now.minute < WATCHER_DAILY_RECAP_MINUTE:
            return
        today_str = now.strftime("%Y-%m-%d")
        if self._last_recap_date == today_str:
            return
        if not self.corrections_today_count:
            self._last_recap_date = today_str
            return
        total_ok = sum(c.get("ok", 0)
                       for c in self.corrections_today_count.values())
        total_v2 = sum(c.get("ok_v2", 0)
                       for c in self.corrections_today_count.values())
        total_all = total_ok + total_v2
        if total_all == 0:
            self._last_recap_date = today_str
            return
        lines: List[str] = []
        for matiere, counts in sorted(self.corrections_today_count.items()):
            ok = counts.get("ok", 0)
            v2 = counts.get("ok_v2", 0)
            if ok + v2 == 0:
                continue
            line = f"**{matiere}** : {ok} nouvelle(s)"
            if v2:
                line += f" + {v2} mise(s) à jour"
            lines.append(line)
        embed = discord.Embed(
            title=f"📊 Récap publications : {today_str}",
            description=(
                f"Total : **{total_all}** correction(s) "
                f"({total_ok} nouvelle(s), {total_v2} mise(s) à jour)"
            ),
            color=LOG_COLOR_INFO,
        )
        embed.add_field(
            name="Détail par matière",
            value="\n".join(lines) if lines else "—",
            inline=False,
        )
        embed.set_footer(text="Pipeline COURS · Watcher corrections")
        embed.timestamp = datetime.utcnow()
        ch = self.bot.get_channel(LOG_CHANNEL_ID)
        if ch:
            try:
                await ch.send(embed=embed)
            except Exception as e:
                log.warning(f"Récap quotidien : envoi échoué : {e}")
        self._last_recap_date = today_str
    @cours.command(name="watcher")
    async def watcher_cmd(self, ctx: commands.Context,
                          action: Optional[str] = None):
        if action is None or action.lower() not in ("start", "stop", "status"):
            await ctx.send("❌ Usage : `!cours watcher <start|stop|status>`")
            return
        action = action.lower()
        if action == "start":
            if self.corrections_watcher_running or (
                self.corrections_watcher_task
                and not self.corrections_watcher_task.done()
            ):
                await ctx.send("ℹ️ Watcher déjà actif.")
                return
            self.corrections_watcher_task = asyncio.create_task(
                self._corrections_watcher_loop()
            )
            await ctx.send(
                f"✅ Watcher démarré (polling "
                f"{WATCHER_CORRECTIONS_INTERVAL_SECONDS}s sur "
                f"`COURS/{{MAT}}/**/corrections/*.pdf`).\n"
                f"Récap quotidien dans <#{LOG_CHANNEL_ID}> à ~23h Paris."
            )
            await self._log(
                "🚀 Corrections watcher démarré",
                color=LOG_COLOR_OK, title="Watcher",
            )
            return
        if action == "stop":
            task_alive = (self.corrections_watcher_task
                          and not self.corrections_watcher_task.done())
            if not self.corrections_watcher_running and not task_alive:
                await ctx.send("ℹ️ Watcher déjà arrêté.")
                return
            self.corrections_watcher_running = False
            if task_alive:
                self.corrections_watcher_task.cancel()
                try:
                    await self.corrections_watcher_task
                except asyncio.CancelledError:
                    pass
            await ctx.send("✅ Watcher arrêté.")
            await self._log(
                "🛑 Corrections watcher arrêté",
                color=LOG_COLOR_WARN, title="Watcher",
            )
            return
        running = "✅ actif" if self.corrections_watcher_running else "❌ arrêté"
        embed = discord.Embed(
            title="📡 Watcher corrections : État",
            color=LOG_COLOR_INFO,
        )
        embed.add_field(name="Status", value=running, inline=True)
        embed.add_field(
            name="Cadence",
            value=f"{WATCHER_CORRECTIONS_INTERVAL_SECONDS} sec",
            inline=True,
        )
        if self.corrections_today_date and self.corrections_today_count:
            today = self.corrections_today_date
            counts = self.corrections_today_count
            total = sum(c.get("ok", 0) + c.get("ok_v2", 0)
                        for c in counts.values())
            embed.add_field(
                name=f"Publications {today}",
                value=f"{total} correction(s)",
                inline=False,
            )
            for mat, c in sorted(counts.items()):
                ok, v2 = c.get("ok", 0), c.get("ok_v2", 0)
                if ok + v2 > 0:
                    embed.add_field(
                        name=mat,
                        value=f"{ok} nouvelles + {v2} v2",
                        inline=True,
                    )
        else:
            embed.add_field(
                name="Publications du jour",
                value="aucune (ou compteur pas encore initialisé)",
                inline=False,
            )
        await ctx.send(embed=embed)
async def setup(bot: commands.Bot):
    await bot.add_cog(CoursPipeline(bot))
    log.info("Cog Cours chargé")