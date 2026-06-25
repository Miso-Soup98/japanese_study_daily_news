from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from itertools import zip_longest
from pathlib import Path

from learning_support import (
    build_detailed_japanese,
    build_interactive_analysis,
    extract_grammar,
    extract_vocabulary,
)
from dictionary_support import JMDictLookup


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
CONFIG_PATH = ROOT / "news_config.json"
JST = timezone(timedelta(hours=9))
KANJI_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff々〆ヶ]")
TAG_RE = re.compile(r"<[^>]+>")

SOURCE_SCORES = {
    "NHK": 35,
    "共同通信": 33,
    "時事通信": 33,
    "日本経済新聞": 32,
    "朝日新聞": 30,
    "読売新聞": 30,
    "毎日新聞": 30,
    "産経新聞": 28,
    "TBS NEWS DIG": 27,
    "テレ朝news": 27,
    "FNNプライムオンライン": 27,
    "日テレNEWS": 27,
    "ロイター": 32,
    "Reuters": 32,
    "Bloomberg": 30,
    "ITmedia": 26,
    "Impress Watch": 26,
    "ORICON NEWS": 25,
    "スポーツ報知": 24,
    "防衛省": 35,
    "気象庁": 35
}


@dataclass
class NewsItem:
    category: str
    title: str
    source: str
    url: str
    published: str
    japanese_summary: str
    source_url: str = ""
    chinese_title: str = ""
    chinese_summary: str = ""
    detailed_japanese: str = ""
    detailed_chinese: str = ""
    vocabulary: list[dict] | None = None
    grammar: list[dict] | None = None
    score: float = 0


def fetch(url: str, timeout: int = 20) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/125 Safari/537.36"
            )
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def clean_text(value: str) -> str:
    value = html.unescape(TAG_RE.sub(" ", value or ""))
    value = re.sub(r"\s+", " ", value).strip()
    return value


def split_google_title(title: str) -> tuple[str, str]:
    if " - " not in title:
        return title, ""
    headline, source = title.rsplit(" - ", 1)
    return headline.strip(), source.strip()


def google_news_candidates(category: str, query: str, target_date: datetime) -> list[NewsItem]:
    date_text = target_date.strftime("%Y-%m-%d")
    if target_date.date() == datetime.now(JST).date():
        # Google News evaluates explicit date operators near UTC boundaries,
        # which can hide early-morning JST stories. Fetch 48 hours and filter in JST.
        full_query = f"{query} when:2d"
    else:
        full_query = (
            f"{query} after:{(target_date - timedelta(days=1)).strftime('%Y-%m-%d')} "
            f"before:{(target_date + timedelta(days=1)).strftime('%Y-%m-%d')}"
        )
    rss_url = (
        "https://news.google.com/rss/search?"
        + urllib.parse.urlencode(
            {"q": full_query, "hl": "ja", "gl": "JP", "ceid": "JP:ja"}
        )
    )
    root = ET.fromstring(fetch(rss_url))
    same_day_items: list[NewsItem] = []
    nearby_items: list[NewsItem] = []
    for node in root.findall("./channel/item"):
        raw_title = clean_text(node.findtext("title", ""))
        title, source = split_google_title(raw_title)
        source = clean_text(node.findtext("source", "")) or source
        link = clean_text(node.findtext("link", ""))
        description = clean_text(node.findtext("description", ""))
        pub_text = clean_text(node.findtext("pubDate", ""))
        try:
            published_dt = parsedate_to_datetime(pub_text).astimezone(JST)
            published = published_dt.strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            published_dt = target_date
            published = date_text
        summary = description
        # Google descriptions often repeat the title and source. Keep a compact,
        # auditable learning excerpt instead of scraping copyrighted article text.
        summary = re.sub(re.escape(raw_title), "", summary, flags=re.I).strip(" -")
        if not summary or len(summary) < 15:
            summary = (
                f"{source or '報道機関'}によると、{title}。"
                "社会への影響や今後の動きが注目されています。"
            )
        item = NewsItem(
            category=category,
            title=title,
            source=source or "Google ニュース掲載媒体",
            url=link,
            published=published,
            japanese_summary=summary[:420],
        )
        if published_dt.date() == target_date.date():
            same_day_items.append(item)
        elif abs((target_date - published_dt).total_seconds()) <= 36 * 3600:
            nearby_items.append(item)
    return same_day_items or nearby_items


def score_item(item: NewsItem, keywords: list[str], target_date: datetime) -> float:
    score = SOURCE_SCORES.get(item.source, 15)
    combined = f"{item.title} {item.japanese_summary}"
    score += sum(7 for keyword in keywords if keyword.lower() in combined.lower())
    if any(word in combined for word in ("速報", "発表", "決定", "成立", "震度6", "優勝", "首脳")):
        score += 9
    if any(word in combined for word in ("まとめ", "コラム", "予想", "オーディション開催")):
        score -= 7
    if item.category == "娱乐" and any(
        word in combined
        for word in ("ホームルーター", "顧客満足度", "キャンペーン商品", "通信サービス")
    ):
        score -= 30
    if item.category == "军事" and any(word in combined for word in ("地震", "避難場所", "災害派遣")):
        score -= 18
    try:
        published = datetime.strptime(item.published, "%Y-%m-%d %H:%M").replace(tzinfo=JST)
        age_hours = max(0, (target_date - published).total_seconds() / 3600)
        score += max(0, 18 - age_hours / 2)
    except ValueError:
        pass
    return score


def choose_news(config: dict, target_date: datetime) -> list[NewsItem]:
    chosen: list[NewsItem] = []
    for category, settings in config.items():
        candidates = google_news_candidates(category, settings["query"], target_date)
        if not candidates:
            continue
        for item in candidates:
            item.score = score_item(item, settings["keywords"], target_date)
        candidates.sort(key=lambda item: item.score, reverse=True)
        selected = candidates[0]
        for candidate in candidates[:8]:
            source_article = extract_source_article(candidate)
            if len(source_article) >= 200:
                candidate._source_article = source_article
                selected = candidate
                break
        chosen.append(selected)
    return chosen


def extract_source_article(item: NewsItem) -> str:
    """Resolve a Google News URL and extract the source article for summarization."""
    try:
        from googlenewsdecoder import gnewsdecoder
        from trafilatura import extract, fetch_url

        decoded = gnewsdecoder(item.url)
        if decoded.get("status") and decoded.get("decoded_url"):
            item.source_url = decoded["decoded_url"]
        else:
            item.source_url = item.url
        document = fetch_url(item.source_url)
        text = extract(
            document,
            url=item.source_url,
            include_comments=False,
            include_tables=False,
            favor_recall=True,
        )
        if not text:
            return ""
        blocked_markers = (
            "この記事の続きを読む",
            "今すぐ登録",
            "会員登録",
            "ログインして",
            "無断転載",
            "関連記事",
        )
        lines = []
        for line in text.splitlines():
            line = clean_text(line)
            if len(line) < 8 or any(marker in line for marker in blocked_markers):
                continue
            if line == item.title or line == item.source:
                continue
            lines.append(line)
        return "\n".join(lines)[:7000]
    except Exception as exc:
        print(f"正文提取失败：{item.category} - {exc}", file=sys.stderr)
        item.source_url = item.url
        return ""


class Translator:
    def __init__(self) -> None:
        self.translation = None
        try:
            import argostranslate.translate

            self.translation = argostranslate.translate.get_translation_from_codes(
                "ja", "zh"
            )
        except ImportError:
            pass

    def __call__(self, text: str) -> str:
        # News text is public, so prefer the higher-quality online translation.
        # If the service is unavailable, retain a fully offline fallback.
        try:
            url = "https://translate.googleapis.com/translate_a/single?" + urllib.parse.urlencode(
                {
                    "client": "gtx",
                    "sl": "ja",
                    "tl": "zh-CN",
                    "dt": "t",
                    "q": text,
                }
            )
            payload = json.loads(fetch(url).decode("utf-8"))
            translated = "".join(part[0] for part in payload[0] if part and part[0])
            if translated.strip():
                return translated.strip()
        except Exception:
            pass
        if self.translation is not None:
            return self.translation.translate(text).strip()
        return "（在线翻译暂时不可用，请稍后重新生成。）"


class Furigana:
    def __init__(self) -> None:
        from sudachipy import dictionary, tokenizer

        self.tokenizer = tokenizer
        self.tagger = dictionary.Dictionary().create()

    RICE_CONTEXT = re.compile(
        r"(新米|玄米|白米|精米|炊飯|田んぼ|稲|収穫|農家|作付|コメ|ご飯|食べ|炊く)"
    )

    CONTEXT_READINGS = {
        "米": {
            "country": "べい",
            "rice": "こめ",
        }
    }

    NUMBER_UNIT_READINGS = {
        "十": "じゅう",
        "百": "ひゃく",
        "千": "せん",
        "万": "まん",
        "億": "おく",
        "兆": "ちょう",
    }

    MIXED_TOKEN_READINGS = {
        "W杯": {"杯": "かっぷ"},
    }

    @staticmethod
    def katakana_to_hiragana(text: str) -> str:
        return "".join(
            chr(ord(char) - 0x60) if "\u30a1" <= char <= "\u30f6" else char
            for char in text
        )

    @staticmethod
    def is_kanji_text(text: str) -> bool:
        return bool(text) and all(KANJI_RE.fullmatch(char) for char in text)

    def annotate_kana_kanji_mix(self, surface: str, reading: str) -> str:
        """Keep visible kana unchanged and put ruby only above kanji runs."""
        runs = [
            match.group(0)
            for match in re.finditer(
                r"[\u3400-\u4dbf\u4e00-\u9fff々〆ヶ]+|[^\u3400-\u4dbf\u4e00-\u9fff々〆ヶ]+",
                surface,
            )
        ]
        result: list[str] = []
        reading_offset = 0

        for index, run in enumerate(runs):
            if not self.is_kanji_text(run):
                visible_reading = self.katakana_to_hiragana(run)
                if reading.startswith(visible_reading, reading_offset):
                    reading_offset += len(visible_reading)
                result.append(html.escape(run))
                continue

            next_visible_reading = ""
            if index + 1 < len(runs) and not self.is_kanji_text(runs[index + 1]):
                next_visible_reading = self.katakana_to_hiragana(runs[index + 1])

            if next_visible_reading:
                next_offset = reading.find(next_visible_reading, reading_offset)
                run_reading = (
                    reading[reading_offset:next_offset]
                    if next_offset >= reading_offset
                    else ""
                )
            else:
                run_reading = reading[reading_offset:]

            if run_reading:
                result.append(
                    f'<ruby data-reading="{html.escape(run_reading, quote=True)}">'
                    f"{html.escape(run)}<rt>{html.escape(run_reading)}</rt></ruby>"
                )
                reading_offset += len(run_reading)
            else:
                result.append(html.escape(run))

        return "".join(result)

    def annotate(self, text: str) -> str:
        result: list[str] = []
        words = list(self.tagger.tokenize(text, self.tokenizer.Tokenizer.SplitMode.C))
        offset = 0
        for word in words:
            surface = word.surface()
            reading = word.reading_form()
            if surface == "米":
                nearby = text[max(0, offset - 10) : offset + len(surface) + 14]
                reading = (
                    self.CONTEXT_READINGS["米"]["rice"]
                    if self.RICE_CONTEXT.search(nearby)
                    else self.CONTEXT_READINGS["米"]["country"]
                )
            if reading and KANJI_RE.search(surface):
                reading = self.katakana_to_hiragana(reading)
                if re.search(r"\d", surface):
                    # Sudachi may group numeric expressions such as "7万2000".
                    # Keep every digit outside ruby and annotate only known kanji units.
                    for part in re.findall(r"\d+|[^\d]+", surface):
                        if part.isdigit():
                            result.append(html.escape(part))
                            continue
                        unit_reading = "".join(
                            self.NUMBER_UNIT_READINGS.get(char, "") for char in part
                        )
                        if unit_reading and len(unit_reading) > 0:
                            result.append(
                                f'<ruby data-reading="{html.escape(unit_reading, quote=True)}">'
                                f"{html.escape(part)}<rt>{html.escape(unit_reading)}</rt></ruby>"
                            )
                        else:
                            result.append(html.escape(part))
                elif re.search(r"[A-Za-z]", surface):
                    # Keep Latin letters outside ruby, including mixed forms such as W杯.
                    mixed_readings = self.MIXED_TOKEN_READINGS.get(surface, {})
                    for part in re.findall(r"[A-Za-z]+|[^A-Za-z]+", surface):
                        if re.fullmatch(r"[A-Za-z]+", part):
                            result.append(html.escape(part))
                            continue
                        part_reading = mixed_readings.get(part)
                        if not part_reading and KANJI_RE.search(part):
                            part_words = list(
                                self.tagger.tokenize(
                                    part, self.tokenizer.Tokenizer.SplitMode.C
                                )
                            )
                            part_reading = "".join(
                                self.katakana_to_hiragana(item.reading_form())
                                for item in part_words
                                if item.reading_form()
                            )
                        if part_reading:
                            result.append(
                                f'<ruby data-reading="{html.escape(part_reading, quote=True)}">'
                                f"{html.escape(part)}<rt>{html.escape(part_reading)}</rt></ruby>"
                            )
                        else:
                            result.append(html.escape(part))
                elif re.search(r"[\u3041-\u3096\u30a1-\u30faー]", surface):
                    result.append(self.annotate_kana_kanji_mix(surface, reading))
                else:
                    result.append(
                        f'<ruby data-reading="{html.escape(reading, quote=True)}">'
                        f"{html.escape(surface)}<rt>{html.escape(reading)}</rt></ruby>"
                    )
            else:
                result.append(html.escape(surface))
            offset += len(surface)
        return "".join(result)


def split_paragraphs(text: str) -> list[str]:
    return [paragraph.strip() for paragraph in re.split(r"\n\s*\n", text) if paragraph.strip()]


def render_bilingual_paragraphs(
    furigana: Furigana, japanese: str, chinese: str
) -> str:
    japanese_paragraphs = split_paragraphs(japanese)
    chinese_paragraphs = split_paragraphs(chinese)
    blocks = []
    for index, (ja_text, zh_text) in enumerate(
        zip_longest(japanese_paragraphs, chinese_paragraphs, fillvalue=""), 1
    ):
        if not ja_text and zh_text and blocks:
            blocks[-1] = blocks[-1].replace(
                "</div>",
                f" {html.escape(zh_text)}</div>",
                1,
            )
            continue
        blocks.append(
            f"""
            <div class="bilingual-block">
              <p class="ja">{furigana.annotate(ja_text)}</p>
              <div class="zh-reveal" tabindex="0" role="button"
                   aria-label="中文翻译，鼠标悬停或键盘聚焦显示">
                <span>{html.escape(zh_text)}</span>
              </div>
            </div>
            """
        )
    return "".join(blocks)


def add_general_news(items: list[NewsItem]) -> list[NewsItem]:
    # “新闻” is the cross-category lead story: select the highest-impact item,
    # then keep all seven specialist categories.
    if not items:
        return items
    disaster = next(
        (
            item
            for item in items
            if item.category == "社会"
            and any(word in item.title for word in ("震度6", "津波", "特別警報", "大規模"))
        ),
        None,
    )
    lead = disaster or max(items, key=lambda item: item.score)
    general = NewsItem(**{**asdict(lead), "category": "新闻"})
    return [general, *items]


def render_report(items: list[NewsItem], config: dict, target_date: datetime) -> str:
    furigana = Furigana()
    cards = []
    icons = {"新闻": "📰", **{k: v["icon"] for k, v in config.items()}}
    section_ids = {
        "新闻": "headline",
        "政治": "politics",
        "经济": "economy",
        "娱乐": "entertainment",
        "体育": "sports",
        "社会": "society",
        "数码科技": "technology",
        "军事": "defense",
    }
    navigation = "".join(
        f'<a href="#{section_ids.get(item.category, f"section-{index}")}" '
        f'data-section="{section_ids.get(item.category, f"section-{index}")}" '
        f'role="tab" aria-controls="{section_ids.get(item.category, f"section-{index}")}">'
        f'{icons.get(item.category, "📰")} {html.escape(item.category)}</a>'
        for index, item in enumerate(items, 1)
    )
    article_texts = [
        f"{item.title}\n{item.detailed_japanese or item.japanese_summary}"
        for item in items
    ]
    initial_analyses = [
        build_interactive_analysis(text, item.vocabulary)
        for text, item in zip(article_texts, items)
    ]
    dictionary_terms = {
        token["lemma"]
        for analysis in initial_analyses
        for token in analysis["tokens"]
    }
    dictionary_lookup = JMDictLookup(ROOT / ".cache" / "jmdict")
    dictionary_entries = dictionary_lookup.lookup_many(dictionary_terms)
    interactive_analyses = [
        build_interactive_analysis(text, item.vocabulary, dictionary_entries)
        for text, item in zip(article_texts, items)
    ]
    dictionary_source_json = json.dumps(
        dictionary_lookup.source_info(), ensure_ascii=False
    ).replace("</", "<\\/")
    for index, (item, interactive_analysis) in enumerate(
        zip(items, interactive_analyses), 1
    ):
        article_japanese = item.detailed_japanese or item.japanese_summary
        bilingual_body = render_bilingual_paragraphs(
            furigana,
            article_japanese,
            item.detailed_chinese or item.chinese_summary,
        )
        interactive_json = json.dumps(
            interactive_analysis, ensure_ascii=False
        ).replace("</", "<\\/")
        vocabulary_html = "".join(
            f"""
            <div class="vocab-item">
              <div class="term">{html.escape(entry["word"])}
                <span class="reading">{html.escape(entry["reading"])}</span>
              </div>
              <div class="meaning">{html.escape(entry["meaning"])} · {html.escape(entry["pos"])}</div>
              <div class="usage">{html.escape(entry["usage"])}</div>
              <div class="example">{furigana.annotate(entry.get("example", ""))}</div>
            </div>
            """
            for entry in (item.vocabulary or [])
        )
        grammar_html = "".join(
            f"""
            <div class="grammar-item">
              <div class="term">{html.escape(entry["pattern"])}
                <span class="grammar-label">{html.escape(entry["label"])}</span>
              </div>
              <div class="usage">{html.escape(entry["explanation"])}</div>
              <div class="example">{furigana.annotate(entry["example"])}</div>
            </div>
            """
            for entry in (item.grammar or [])
        )
        cards.append(
            f"""
            <article class="card news-section" id="{section_ids.get(item.category, f"section-{index}")}" role="tabpanel">
              <div class="article-main">
                <div class="category">{icons.get(item.category, "📰")} {html.escape(item.category)}</div>
                <h3>日文原文标题</h3>
                <h2>{furigana.annotate(item.title)}</h2>
                <div class="zh-reveal title-translation" tabindex="0" role="button"
                     aria-label="标题中文翻译，鼠标悬停或键盘聚焦显示">
                  <span>{html.escape(item.chinese_title)}</span>
                </div>
                <div class="meta">{html.escape(item.source)} · {html.escape(item.published)}</div>
                <section>
                  <h3>日文正文（每段下方可查看中文）</h3>
                  {bilingual_body}
                </section>
                <a class="source" href="{html.escape(item.source_url or item.url, quote=True)}" target="_blank" rel="noreferrer">
                  阅读来源报道 ↗
                </a>
              </div>
              <aside class="study-panel">
                <div class="study-panel-inner">
                  <div class="study-title">语言学习笔记</div>
                  <section class="selection-analysis" aria-live="polite">
                    <div class="selection-hint">
                      <strong>鼠标选词・选句解析</strong>
                      <span>在左侧日文中选中文字，这里会显示对应词汇、原形、活用和语法。</span>
                    </div>
                    <div class="selection-result" hidden></div>
                  </section>
                  <div class="recommended-notes">
                    <h3>系统推荐词汇</h3>
                    {vocabulary_html or '<p class="empty">本条暂未提取到合适词汇。</p>'}
                    <h3>系统推荐语法</h3>
                    {grammar_html or '<p class="empty">本条暂未识别到重点语法。</p>'}
                  </div>
                  <div class="dictionary-licence">
                    词典数据：<a href="https://www.edrdg.org/jmdict/j_jmdict.html"
                    target="_blank" rel="noreferrer">JMdict/EDICT（EDRDG）</a>，
                    按其<a href="https://www.edrdg.org/edrdg/licence.html"
                    target="_blank" rel="noreferrer">词典许可</a>使用。
                  </div>
                  <script type="application/json" class="article-analysis">{interactive_json}</script>
                  <script type="application/json" class="dictionary-source">{dictionary_source_json}</script>
                </div>
              </aside>
            </article>
            """
        )
    date_label = target_date.strftime("%Y年%m月%d日")
    rendered = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{date_label} 每日新闻日语学习简报</title>
  <style>
    :root {{ color-scheme: light; --ink:#172033; --muted:#677085; --blue:#225bc7; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; background:#f2f5fa; color:var(--ink); font-family:"Microsoft YaHei UI","Yu Gothic UI",sans-serif; }}
    header {{ padding:38px 22px 28px; color:white; background:linear-gradient(125deg,#12284f,#2962c7); }}
    header .inner, main {{ width:min(1420px,calc(100% - 30px)); margin:auto; }}
    h1 {{ margin:0 0 9px; font-size:clamp(26px,4vw,42px); }}
    header p {{ margin:0; opacity:.86; }}
    main {{ padding:24px 0 50px; display:grid; gap:18px; }}
    .category-nav {{ position:sticky; top:0; z-index:20; background:rgba(255,255,255,.96); backdrop-filter:blur(12px); border-bottom:1px solid #dfe5ef; box-shadow:0 4px 18px rgba(27,42,72,.08); }}
    .category-nav .nav-inner {{ width:min(1420px,calc(100% - 30px)); margin:auto; display:grid; grid-template-columns:repeat(8,1fr); gap:7px; padding:10px 0; }}
    .category-nav a {{ color:#28426f; text-decoration:none; text-align:center; padding:9px 6px; border-radius:10px; font-weight:750; white-space:nowrap; }}
    .category-nav a:hover, .category-nav a.active {{ color:white; background:var(--blue); }}
    .notice {{ background:#fff7d9; border:1px solid #ecd98d; border-radius:12px; padding:13px 16px; font-size:14px; }}
    .card {{ background:white; border-radius:18px; padding:0; box-shadow:0 8px 28px rgba(31,45,75,.08); display:grid; grid-template-columns:minmax(0,1fr) 400px; overflow:visible; }}
    .news-section[hidden] {{ display:none; }}
    .article-main {{ padding:26px 30px 30px; min-width:0; }}
    .study-panel {{ background:#f7f9fd; border-left:1px solid #e3e8f2; padding:24px 22px; }}
    .study-panel-inner {{ position:sticky; top:76px; max-height:calc(100vh - 92px); overflow:auto; padding-right:4px; scrollbar-width:thin; }}
    .study-title {{ color:var(--blue); font-weight:800; font-size:18px; margin-bottom:12px; }}
    .selection-analysis {{ margin-bottom:18px; }}
    .selection-hint, .selection-result {{ border:1px solid #b9ccef; border-radius:13px; padding:13px; background:#eef4ff; }}
    .selection-hint {{ display:grid; gap:5px; color:#294a86; }}
    .selection-hint span {{ color:#64718a; font-size:13px; line-height:1.6; }}
    .selection-result {{ background:white; box-shadow:0 8px 22px rgba(34,91,199,.12); animation:selection-pop .18s ease-out; }}
    .selected-text {{ border-left:4px solid var(--blue); padding:7px 10px; margin-bottom:10px; background:#f3f6fb; color:#263b64; font-weight:700; line-height:1.6; overflow-wrap:anywhere; }}
    .analysis-heading {{ margin:12px 0 6px; color:#63718a; font-size:12px; font-weight:800; letter-spacing:.08em; }}
    .analysis-token, .analysis-grammar {{ border-top:1px solid #e7ebf2; padding:9px 0; }}
    .analysis-token:first-of-type {{ border-top:0; }}
    .token-meta {{ color:#68758c; font-size:12px; margin-top:3px; }}
    .confidence {{ display:inline-flex; align-items:center; border-radius:999px; padding:2px 7px; margin-left:6px; font-size:11px; font-weight:750; }}
    .confidence-high {{ color:#17633a; background:#dff5e8; }}
    .confidence-medium {{ color:#805900; background:#fff0c6; }}
    .confidence-low {{ color:#8b3a3a; background:#fde5e5; }}
    .dictionary-senses {{ margin-top:7px; color:#34415a; font-size:13px; line-height:1.65; }}
    .dictionary-senses ol {{ margin:4px 0 0; padding-left:20px; }}
    .dictionary-source-note {{ color:#788296; font-size:11px; line-height:1.55; margin-top:8px; }}
    .lookup-links {{ display:flex; flex-wrap:wrap; gap:7px; margin-top:7px; }}
    .lookup-links a {{ color:#225bc7; background:#edf3ff; border-radius:7px; padding:4px 7px; font-size:12px; text-decoration:none; }}
    .analysis-note {{ color:#6d778a; font-size:12px; line-height:1.65; margin-top:7px; }}
    .recommended-notes {{ border-top:1px solid #dde4ef; padding-top:5px; }}
    .dictionary-licence {{ margin-top:16px; color:#7b8495; font-size:11px; line-height:1.6; }}
    .dictionary-licence a {{ color:#6478a0; }}
    @keyframes selection-pop {{ from {{ transform:translateY(4px); opacity:.5; }} to {{ transform:none; opacity:1; }} }}
    .category {{ color:var(--blue); font-weight:700; letter-spacing:.08em; }}
    h2 {{ font-family:"Yu Mincho","Yu Gothic UI",serif; font-size:27px; line-height:2.15; letter-spacing:.025em; margin:12px 0 8px; }}
    .meta {{ color:var(--muted); font-size:13px; margin-bottom:18px; }}
    h3 {{ margin:14px 0 6px; font-size:14px; color:var(--muted); }}
    p {{ margin:0; line-height:1.9; }}
    .bilingual-block {{ padding:13px 0 16px; border-bottom:1px solid #edf0f5; }}
    .bilingual-block:last-child {{ border-bottom:0; }}
    .ja {{ font-family:"Yu Gothic UI",sans-serif; font-size:20px; line-height:2.45; letter-spacing:.022em; word-spacing:.08em; }}
    .zh-reveal {{ position:relative; margin-top:8px; padding:10px 13px; border-radius:9px; background:#c9ced6; color:transparent; line-height:1.85; cursor:help; outline:none; transition:background .18s ease,color .18s ease,box-shadow .18s ease; user-select:none; }}
    .zh-reveal::after {{ content:"悬停查看中文翻译"; position:absolute; inset:0; display:grid; place-items:center; color:#687180; font-size:13px; letter-spacing:.08em; pointer-events:none; }}
    .zh-reveal:hover, .zh-reveal:focus, .zh-reveal.revealed {{ background:#f0f2f5; color:#303846; box-shadow:inset 0 0 0 1px #d9dde4; user-select:text; }}
    .zh-reveal:hover::after, .zh-reveal:focus::after, .zh-reveal.revealed::after {{ display:none; }}
    .title-translation {{ margin:2px 0 12px; font-size:16px; }}
    .vocab-item, .grammar-item {{ background:white; border:1px solid #e2e7f0; border-radius:11px; padding:11px 12px; margin:8px 0; }}
    .term {{ font-weight:800; color:#1b315d; }}
    .reading {{ font-size:12px; font-weight:500; color:#69758c; margin-left:7px; }}
    .meaning {{ color:#27344d; font-size:14px; margin-top:4px; }}
    .usage {{ color:#606b80; font-size:13px; line-height:1.65; margin-top:4px; }}
    .grammar-label {{ color:#3165c5; background:#e8efff; border-radius:20px; padding:2px 7px; font-size:11px; margin-left:5px; }}
    .example {{ margin-top:7px; color:#263b64; font-size:14px; line-height:1.8; border-top:1px dashed #d9dfeb; padding-top:6px; }}
    .empty {{ color:var(--muted); font-size:13px; }}
    ruby {{ ruby-position:over; ruby-align:center; margin:0 .055em; }}
    rt {{ font-size:.48em; line-height:1; letter-spacing:.015em; color:#5e6b82; font-weight:500; }}
    .source {{ display:inline-block; margin-top:6px; color:var(--blue); text-decoration:none; font-weight:650; }}
    footer {{ text-align:center; color:var(--muted); padding:0 20px 34px; font-size:13px; }}
    @media (max-width:980px) {{ .card {{ grid-template-columns:1fr; }} .study-panel {{ border-left:0; border-top:1px solid #e3e8f2; }} .study-panel-inner {{ position:static; max-height:none; overflow:visible; }} .category-nav .nav-inner {{ display:flex; overflow-x:auto; }} .category-nav a {{ min-width:104px; }} h2 {{ font-size:24px; line-height:2.2; }} .ja {{ font-size:18px; line-height:2.5; }} }}
    @media print {{ body {{ background:white; }} .card {{ box-shadow:none; border:1px solid #ddd; break-inside:avoid; grid-template-columns:1fr; }} .study-panel {{ border-left:0; border-top:1px solid #ddd; }} }}
  </style>
</head>
<body>
  <header><div class="inner">
    <h1>每日新闻 · 日语学习简报</h1>
    <p>{date_label}　日文摘要 / 中文翻译 / 汉字振假名</p>
  </div></header>
  <nav class="category-nav" aria-label="新闻栏目"><div class="nav-inner">{navigation}</div></nav>
  <main>
    <div class="notice">内容为学习用途的自动摘要，不是新闻正文转载。关键新闻由时效、来源权威度、影响范围和栏目关键词综合筛选。</div>
    {''.join(cards)}
  </main>
  <footer>生成时间：{datetime.now(JST):%Y-%m-%d %H:%M JST}</footer>
  <script>
    (() => {{
      const tabs = [...document.querySelectorAll('.category-nav a[data-section]')];
      const sections = [...document.querySelectorAll('.news-section')];
      const validIds = new Set(sections.map(section => section.id));

      function showSection(id, updateHistory = false) {{
        const targetId = validIds.has(id) ? id : sections[0]?.id;
        if (!targetId) return;
        sections.forEach(section => {{
          const active = section.id === targetId;
          section.hidden = !active;
          section.setAttribute('aria-hidden', String(!active));
        }});
        tabs.forEach(tab => {{
          const active = tab.dataset.section === targetId;
          tab.classList.toggle('active', active);
          tab.setAttribute('aria-selected', String(active));
          tab.tabIndex = active ? 0 : -1;
        }});
        if (updateHistory) history.pushState({{ section: targetId }}, '', `#${{targetId}}`);
        document.title = `${{tabs.find(tab => tab.dataset.section === targetId)?.textContent.trim() || ''}} · {date_label} 每日新闻日语学习简报`;
        window.scrollTo({{ top: 0, behavior: 'instant' }});
      }}

      tabs.forEach(tab => tab.addEventListener('click', event => {{
        event.preventDefault();
        showSection(tab.dataset.section, true);
      }}));
      window.addEventListener('popstate', () => showSection(location.hash.slice(1), false));
      document.addEventListener('click', event => {{
        const reveal = event.target.closest('.zh-reveal');
        if (reveal) reveal.classList.toggle('revealed');
      }});

      const escapeHtml = value => String(value).replace(/[&<>"']/g, char => ({{
        '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
      }})[char]);

      function selectionTextWithoutRuby(range) {{
        const fragment = range.cloneContents();
        fragment.querySelectorAll?.('rt, rp').forEach(node => node.remove());
        return (fragment.textContent || '').replace(/\\s+/g, '').trim();
      }}

      function renderSelectionAnalysis(card, selected, selectedContext = '') {{
        const result = card.querySelector('.selection-result');
        const hint = card.querySelector('.selection-hint');
        const dataNode = card.querySelector('.article-analysis');
        const sourceNode = card.querySelector('.dictionary-source');
        if (!result || !hint || !dataNode || !sourceNode) return;
        const data = JSON.parse(dataNode.textContent);
        const source = JSON.parse(sourceNode.textContent);
        const exact = selected.length <= 14;
        const matchingTokens = data.tokens.filter(token =>
          selected.includes(token.surface) ||
          selected.includes(token.lemma) ||
          (exact && (token.surface.includes(selected) || token.lemma.includes(selected)))
        );
        const contextualTokens = matchingTokens.filter(token =>
          selectedContext && (
            selectedContext.includes(token.context) ||
            token.context.includes(selectedContext)
          )
        );
        const tokens = (contextualTokens.length ? contextualTokens : matchingTokens)
          .filter((token, index, list) =>
            list.findIndex(item =>
              item.surface === token.surface &&
              item.lemma === token.lemma &&
              item.pos === token.pos
            ) === index
          ).slice(0, 12);
        const grammar = data.grammar.filter(entry =>
          selected.includes(entry.pattern) ||
          (selected.length >= 2 && entry.pattern.includes(selected))
        ).slice(0, 6);

        const tokenHtml = tokens.map(token => {{
          const query = encodeURIComponent(token.lemma || token.surface);
          const detail = [
            token.pos,
            token.lemma !== token.surface ? `原形：${{token.lemma}}` : '',
            token.conjugationType,
            token.conjugationForm
          ].filter(Boolean).join(' · ');
          const confidenceClass = {{
            '高':'confidence-high', '中':'confidence-medium', '低':'confidence-low'
          }}[token.confidence] || 'confidence-low';
          const senses = token.dictionary?.senses?.flatMap(sense => sense.glosses || []).slice(0, 10) || [];
          const dictionaryHtml = senses.length
            ? `<div class="dictionary-senses"><strong>JMdict 候选英文义项（未自动替你选义）</strong><ol>${{
                senses.map(sense => `<li>${{escapeHtml(sense)}}</li>`).join('')
              }}</ol></div>`
            : '<div class="analysis-note">权威词典中未找到可确认的完全一致释义，因此不自动给出确定词义。</div>';
          return `<div class="analysis-token">
            <div class="term">${{escapeHtml(token.surface)}}
              <span class="reading">${{escapeHtml(token.reading)}}</span>
              <span class="confidence ${{confidenceClass}}">${{escapeHtml(token.confidence)}}可信</span>
            </div>
            <div class="token-meta">${{escapeHtml(detail)}}</div>
            <div class="analysis-note">${{escapeHtml(token.confidenceReason)}}</div>
            ${{dictionaryHtml}}
            ${{token.meaning ? `<div class="meaning">中文辅助：${{escapeHtml(token.meaning)}}（自动翻译，仅供对照）</div>` : ''}}
            ${{token.usage ? `<div class="usage">${{escapeHtml(token.usage)}}</div>` : ''}}
            <div class="lookup-links">
              <a href="https://translate.google.com/?sl=ja&tl=zh-CN&text=${{query}}&op=translate" target="_blank" rel="noreferrer">查中文释义 ↗</a>
              <a href="https://www.weblio.jp/content/${{query}}" target="_blank" rel="noreferrer">日语词典 ↗</a>
            </div>
            <div class="dictionary-source-note">词典来源：JMdict/EDICT（EDRDG）${{
              source.lastModified ? ` · 数据日期：${{escapeHtml(source.lastModified)}}` : ''
            }}${{source.stale ? ' · 当前使用缓存版本' : ''}}</div>
          </div>`;
        }}).join('');

        const grammarHtml = grammar.map(entry => `
          <div class="analysis-grammar">
            <div class="term">${{escapeHtml(entry.pattern)}}
              <span class="grammar-label">${{escapeHtml(entry.label)}}</span>
              <span class="confidence ${{entry.confidence === '高' ? 'confidence-high' : 'confidence-medium'}}">${{
                escapeHtml(entry.confidence)
              }}匹配</span>
            </div>
            <div class="usage">${{escapeHtml(entry.explanation)}}</div>
            <div class="analysis-note">${{escapeHtml(entry.confidenceReason)}}；正文依据：「${{
              escapeHtml(entry.evidence)
            }}」</div>
          </div>
        `).join('');

        result.innerHTML = `
          <div class="selected-text">「${{escapeHtml(selected)}}」</div>
          ${{tokens.length ? `<div class="analysis-heading">词汇与变形</div>${{tokenHtml}}` :
            '<div class="analysis-note">没有识别出独立词汇，请把选择范围稍微扩大。</div>'}}
          ${{grammar.length ? `<div class="analysis-heading">匹配到的语法</div>${{grammarHtml}}` :
            '<div class="analysis-note">暂未命中内置语法。选语法时建议连同前后助词、助动词一起选中。</div>'}}
        `;
        hint.hidden = true;
        result.hidden = false;
        result.classList.remove('selection-result');
        void result.offsetWidth;
        result.classList.add('selection-result');
        if (matchMedia('(max-width:980px)').matches) {{
          result.scrollIntoView({{ behavior:'smooth', block:'start' }});
        }}
      }}

      function inspectSelection() {{
        const selection = window.getSelection();
        if (!selection || selection.isCollapsed || !selection.rangeCount) return;
        const range = selection.getRangeAt(0);
        const container = range.commonAncestorContainer.nodeType === 1
          ? range.commonAncestorContainer
          : range.commonAncestorContainer.parentElement;
        const articleMain = container?.closest?.('.article-main');
        const card = articleMain?.closest('.card');
        if (!card) return;
        const selected = selectionTextWithoutRuby(range).slice(0, 120);
        const contextElement = container?.closest?.('.ja, h2');
        let selectedContext = '';
        if (contextElement) {{
          const contextClone = contextElement.cloneNode(true);
          contextClone.querySelectorAll('rt, rp').forEach(node => node.remove());
          selectedContext = (contextClone.textContent || '').replace(/\\s+/g, '').trim();
        }}
        if (selected) renderSelectionAnalysis(card, selected, selectedContext);
      }}

      document.addEventListener('mouseup', () => setTimeout(inspectSelection, 0));
      document.addEventListener('keyup', event => {{
        if (event.key === 'Shift' || event.key.startsWith('Arrow')) {{
          setTimeout(inspectSelection, 0);
        }}
      }});
      showSection(location.hash.slice(1), false);
    }})();
  </script>
</body>
</html>"""
    return "\n".join(line.rstrip() for line in rendered.splitlines()) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="生成带振假名和中文翻译的每日新闻简报")
    parser.add_argument("--date", help="目标日期，格式 YYYY-MM-DD，默认今天")
    parser.add_argument("--no-open", action="store_true", help="生成后不打开浏览器")
    parser.add_argument(
        "--render-existing",
        action="store_true",
        help="使用 output 中已有的 JSON 重新渲染本地页面，不抓取或翻译新闻",
    )
    args = parser.parse_args()

    now = datetime.now(JST)
    if args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").replace(
            hour=23, minute=59, tzinfo=JST
        )
    else:
        target_date = now

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    date_key = target_date.strftime("%Y-%m-%d")
    json_path = OUTPUT_DIR / f"daily_news_{date_key}.json"
    html_path = OUTPUT_DIR / f"daily_news_{date_key}.html"
    if args.render_existing:
        raw_items = json.loads(json_path.read_text(encoding="utf-8"))
        items = [NewsItem(**item) for item in raw_items]
        rendered = render_report(items, config, target_date)
        html_path.write_text(rendered, encoding="utf-8")
        (ROOT / "index.html").write_text(rendered, encoding="utf-8")
        print(f"已从现有 JSON 重新渲染：{html_path}")
        if not args.no_open:
            webbrowser.open(html_path.as_uri())
        return 0

    print(f"正在获取 {target_date:%Y-%m-%d} 的新闻候选…")
    items = choose_news(config, target_date)
    if len(items) < len(config):
        print(f"警告：只找到 {len(items)}/{len(config)} 个栏目。", file=sys.stderr)

    translator = Translator()
    for item in items:
        print(f"翻译：{item.category} - {item.title}")
        item.chinese_title = translator(item.title)
        item.chinese_summary = translator(item.japanese_summary)
        source_article = getattr(item, "_source_article", None)
        if source_article is None:
            source_article = extract_source_article(item)
        item.detailed_japanese = build_detailed_japanese(
            item,
            source_article,
            github_token=os.environ.get("GITHUB_TOKEN", ""),
        )
        item.detailed_chinese = "\n\n".join(
            translator(paragraph)
            for paragraph in split_paragraphs(item.detailed_japanese)
        )
        item.vocabulary = extract_vocabulary(
            item.detailed_japanese, translator
        )
        item.grammar = extract_grammar(item.detailed_japanese)

    items = add_general_news(items)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps([asdict(item) for item in items], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    html_path.write_text(render_report(items, config, target_date), encoding="utf-8")
    (ROOT / "index.html").write_text(
        render_report(items, config, target_date), encoding="utf-8"
    )
    print(f"已生成：{html_path}")
    if not args.no_open:
        webbrowser.open(html_path.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
