from __future__ import annotations

import json
import re
import urllib.request
from collections import Counter


STOPWORDS = {
    "こと", "もの", "ため", "よう", "これ", "それ", "今回", "今後", "情報", "記事",
    "ニュース", "必要", "大切", "関係", "影響", "動き", "発表", "確認", "可能性",
    "注目", "対応", "日本", "社会", "政府", "正式", "最新", "中心", "報道",
    "為る", "有る", "居る", "成る", "見る", "考える", "分ける", "広がる",
}

GRAMMAR_LIBRARY = [
    (
        "によると",
        "消息来源表达",
        "表示“根据……”。新闻中常用于说明信息来源。",
        "気象庁によると、強い揺れが観測されました。",
    ),
    (
        "を受けて",
        "原因・契机",
        "表示“受到……影响／鉴于……”。前接名词，说明后续行动的契机。",
        "地震を受けて、鉄道会社は運転を見合わせました。",
    ),
    (
        "について",
        "主题",
        "表示“关于……”。比「は」更明确地限定讨论对象。",
        "政府は今後の対応について説明しました。",
    ),
    (
        "とみられます",
        "推测",
        "新闻常用的客观推测表达，意思是“被认为……／预计……”。语气比断言柔和。",
        "市場への影響は続くとみられます。",
    ),
    (
        "必要があります",
        "必要性",
        "表示“有必要……”。动词辞书形后接「必要がある」。",
        "最新の情報を確認する必要があります。",
    ),
    (
        "かどうか",
        "间接疑问",
        "表示“是否……”。把肯定和否定两种可能作为一个整体。",
        "運転が再開されるかどうかは、まだ分かりません。",
    ),
    (
        "に向けて",
        "目标・方向",
        "表示“面向……／为了……”。常接目标、比赛、制度实施等。",
        "次の試合に向けて、選手たちは調整を続けています。",
    ),
]


def _github_model_rewrite(prompt: str, token: str) -> str:
    request = urllib.request.Request(
        "https://models.github.ai/inference/chat/completions",
        data=json.dumps(
            {
                "model": "openai/gpt-4o-mini",
                "temperature": 0.15,
                "max_tokens": 1600,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "あなたは正確さを最優先する日本語ニュース編集者です。"
                            "与えられた資料だけを使い、事実を追加・推測しません。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github+json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload["choices"][0]["message"]["content"].strip()


def build_detailed_japanese(item, source_article: str, github_token: str = "") -> str:
    if source_article and github_token:
        prompt = f"""次のニュース資料を、学習者向けの詳しい日本語記事に書き直してください。

必須条件:
- 600～1000字程度、4～7段落。
- 原資料にある人物、組織、日付、金額、数値、場所、経緯、発言、各当事者の反応をできる限り残す。
- ニュースごとに固有の事実を中心にする。「社会への影響が注目される」「今後の動向が焦点」のような中身のない定型文は禁止。
- 原資料にない背景、評価、因果関係、将来予測を加えない。
- 原文を長く連続コピーせず、文章構造と表現を全面的に組み替える。
- 見出し記号や箇条書きは使わず、自然な報道文にする。
- 「告発」「起訴」「逮捕」「容疑」など法的に異なる言葉を絶対に混同しない。
- 出力は記事本文だけ。

カテゴリ: {item.category}
見出し: {item.title}
媒体: {item.source}

原資料:
{source_article}
"""
        try:
            rewritten = _github_model_rewrite(prompt, github_token)
            if len(rewritten) >= 250:
                return rewritten
        except Exception as exc:
            print(f"GitHub Models 改写失败：{item.category} - {exc}")

    if source_article:
        # Local/manual runs have no GitHub Models token. Keep a concise,
        # source-specific fallback instead of returning generic boilerplate.
        sentences = [
            sentence.strip()
            for sentence in re.split(r"(?<=[。！？])", source_article)
            if len(sentence.strip()) >= 18
        ]
        facts = sentences[:3]
        return (
            f"{item.source}は「{item.title}」と報じました。"
            + "".join(facts)
            + "\n\n自動改写はGitHub Actionsで実行され、公開版ではより詳しい記事に更新されます。"
        )
    return (
        f"{item.source}は「{item.title}」と報じました。"
        "元記事から十分な本文を取得できなかったため、詳細は出典リンクで確認してください。"
    )


def _hiragana(reading: str | None) -> str:
    if not reading:
        return ""
    return "".join(
        chr(ord(char) - 0x60) if "\u30a1" <= char <= "\u30f6" else char
        for char in reading
    )


def extract_vocabulary(text: str, translator, limit: int = 6) -> list[dict]:
    from fugashi import Tagger

    tagger = Tagger()
    candidates = []
    counts = Counter()
    token_data = {}
    for token in tagger(text):
        feature = token.feature
        pos = getattr(feature, "pos1", "")
        lemma = getattr(feature, "lemma", None) or token.surface
        reading = getattr(feature, "kana", None) or getattr(feature, "pron", None)
        if pos not in {"名詞", "動詞", "形容詞"}:
            continue
        if lemma in STOPWORDS or len(lemma) < 2:
            continue
        if not re.search(r"[\u3400-\u9fff々ァ-ヶ]", lemma):
            continue
        counts[lemma] += 1
        token_data[lemma] = (token.surface, _hiragana(reading), pos, getattr(feature, "cType", "*"))

    for lemma, count in counts.most_common():
        surface, reading, pos, conjugation_type = token_data[lemma]
        score = count * 3 + len(lemma) + (3 if re.search(r"[\u3400-\u9fff]", lemma) else 0)
        candidates.append((score, lemma, surface, reading, pos, conjugation_type))
    candidates.sort(reverse=True)

    result = []
    sentences = [part.strip() for part in re.split(r"(?<=[。！？])", text) if part.strip()]
    for _, lemma, surface, reading, pos, conjugation_type in candidates[:limit]:
        meaning = translator(lemma)
        pos_zh = {"名詞": "名词", "動詞": "动词", "形容詞": "形容词"}.get(pos, pos)
        if pos == "動詞":
            usage = f"原形是「{lemma}」，文中形式是「{surface}」。活用类型：{conjugation_type}。"
        elif pos == "形容詞":
            usage = f"原形是「{lemma}」，可按语境变为过去式、否定式或副词形式。"
        else:
            usage = f"本文中作为名词使用；可以用「{lemma}の＋名词」构成修饰关系。"
        example = next(
            (sentence for sentence in sentences if lemma in sentence or surface in sentence),
            "",
        )
        result.append(
            {
                "word": lemma,
                "reading": reading,
                "meaning": meaning,
                "pos": pos_zh,
                "usage": usage,
                "example": example[:70],
            }
        )
    return result


def extract_grammar(text: str, limit: int = 3) -> list[dict]:
    found = []
    for pattern, label, explanation, example in GRAMMAR_LIBRARY:
        if pattern in text:
            found.append(
                {
                    "pattern": pattern,
                    "label": label,
                    "explanation": explanation,
                    "example": example,
                }
            )
    return found[:limit]
