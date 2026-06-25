from __future__ import annotations

import gzip
import json
import os
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path


JMDICT_URL = "https://www.edrdg.org/pub/Nihongo/JMdict_e.gz"
JMDICT_ATTRIBUTION = (
    "JMdict/EDICT Dictionary Project (EDRDG), used under its dictionary licence."
)


class JMDictLookup:
    """Conservative exact-match lookup against the current JMdict English edition."""

    def __init__(self, cache_dir: Path, max_age_days: int = 30) -> None:
        self.cache_dir = cache_dir
        self.cache_path = cache_dir / "JMdict_e.gz"
        self.meta_path = cache_dir / "JMdict_e.meta.json"
        self.max_age = timedelta(days=max_age_days)
        self.meta: dict = {}

    def _is_fresh(self) -> bool:
        if not self.cache_path.exists() or not self.meta_path.exists():
            return False
        try:
            self.meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            checked_at = datetime.fromisoformat(self.meta["checkedAt"])
            return datetime.now(timezone.utc) - checked_at <= self.max_age
        except (KeyError, ValueError, OSError, json.JSONDecodeError):
            return False

    def _ensure_dictionary(self) -> bool:
        if self._is_fresh():
            return True
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(
            JMDICT_URL,
            headers={"User-Agent": "japanese-study-daily-news/1.0"},
        )
        temporary = self.cache_path.with_suffix(".tmp")
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                temporary.write_bytes(response.read())
                last_modified = response.headers.get("Last-Modified", "")
            os.replace(temporary, self.cache_path)
            self.meta = {
                "checkedAt": datetime.now(timezone.utc).isoformat(),
                "lastModified": last_modified,
                "sourceUrl": JMDICT_URL,
                "attribution": JMDICT_ATTRIBUTION,
            }
            self.meta_path.write_text(
                json.dumps(self.meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return True
        except Exception:
            temporary.unlink(missing_ok=True)
            if self.cache_path.exists():
                try:
                    self.meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
                except Exception:
                    self.meta = {}
                self.meta["stale"] = True
                return True
            return False

    @staticmethod
    def _text_list(element: ET.Element, path: str) -> list[str]:
        return [
            child.text.strip()
            for child in element.findall(path)
            if child.text and child.text.strip()
        ]

    def lookup_many(self, terms: set[str]) -> dict[str, list[dict]]:
        wanted = {term for term in terms if term}
        results = {term: [] for term in wanted}
        if not wanted or not self._ensure_dictionary():
            return results

        with gzip.open(self.cache_path, "rb") as source:
            for _, entry in ET.iterparse(source, events=("end",)):
                if entry.tag != "entry":
                    continue
                spellings = self._text_list(entry, "k_ele/keb")
                readings = self._text_list(entry, "r_ele/reb")
                matched = wanted.intersection(spellings + readings)
                if matched:
                    common = bool(
                        entry.findall("k_ele/ke_pri") or entry.findall("r_ele/re_pri")
                    )
                    senses = []
                    for sense in entry.findall("sense"):
                        glosses = []
                        for gloss in sense.findall("gloss"):
                            language = gloss.attrib.get(
                                "{http://www.w3.org/XML/1998/namespace}lang", "eng"
                            )
                            if language == "eng" and gloss.text:
                                glosses.append(gloss.text.strip())
                        if not glosses:
                            continue
                        senses.append(
                            {
                                "pos": self._text_list(sense, "pos"),
                                "glosses": glosses[:4],
                                "misc": self._text_list(sense, "misc"),
                                "field": self._text_list(sense, "field"),
                            }
                        )
                    record = {
                        "spellings": spellings,
                        "readings": readings,
                        "common": common,
                        "senses": senses[:3],
                    }
                    for term in matched:
                        results[term].append(record)
                entry.clear()
        return results

    def source_info(self) -> dict:
        return {
            "name": "JMdict/EDICT",
            "publisher": "Electronic Dictionary Research and Development Group",
            "url": "https://www.edrdg.org/jmdict/j_jmdict.html",
            "licenceUrl": "https://www.edrdg.org/edrdg/licence.html",
            "lastModified": self.meta.get("lastModified", ""),
            "stale": bool(self.meta.get("stale")),
        }
