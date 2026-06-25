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


def _validated_rewrite(raw_output: str, source_article: str) -> str:
    cleaned = raw_output.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return ""

    paragraphs = payload.get("paragraphs", [])
    accepted = []
    normalize_evidence = lambda value: re.sub(
        r"[\s「」『』（）()、。・：:【】\[\]\"'’“”]", "", value
    )
    normalized_source = normalize_evidence(source_article)
    banned = (
        "期待されています",
        "注目されています",
        "関心が高ま",
        "見守る必要",
        "重要な役割",
        "寄与する",
        "可能性があります",
        "と考えられます",
    )
    for entry in paragraphs:
        text = str(entry.get("text", "")).strip()
        evidence = [
            str(fragment).strip()
            for fragment in entry.get("evidence", [])
            if str(fragment).strip()
        ]
        if len(text) < 35 or not evidence:
            continue
        if any(
            len(normalize_evidence(fragment)) < 6
            or normalize_evidence(fragment) not in normalized_source
            for fragment in evidence
        ):
            continue
        if any(phrase in text and phrase not in source_article for phrase in banned):
            continue
        generated_numbers = set(re.findall(r"\d+(?:[.,]\d+)?", text))
        source_numbers = set(re.findall(r"\d+(?:[.,]\d+)?", source_article))
        if not generated_numbers.issubset(source_numbers):
            continue
        # Reject long copied strings; the detailed article must be a rewrite.
        compact_text = re.sub(r"\s+", "", text)
        compact_source = re.sub(r"\s+", "", source_article)
        if any(
            compact_text[index : index + 55] in compact_source
            for index in range(max(0, len(compact_text) - 54))
        ):
            continue
        accepted.append(text)
    return "\n\n".join(accepted)


def _validated_plain_rewrite(raw_output: str, source_article: str) -> str:
    cleaned = re.sub(r"^```(?:text)?\s*|\s*```$", "", raw_output.strip())
    banned = (
        "期待されています",
        "注目されています",
        "関心が高ま",
        "見守る必要",
        "重要な役割",
        "寄与する",
        "可能性があります",
        "と考えられます",
    )
    source_numbers = set(re.findall(r"\d+(?:[.,]\d+)?", source_article))
    compact_source = re.sub(r"\s+", "", source_article)
    accepted = []
    for paragraph in re.split(r"\n\s*\n", cleaned):
        text = paragraph.strip()
        if len(text) < 30:
            continue
        if any(phrase in text and phrase not in source_article for phrase in banned):
            continue
        generated_numbers = set(re.findall(r"\d+(?:[.,]\d+)?", text))
        if not generated_numbers.issubset(source_numbers):
            continue
        compact_text = re.sub(r"\s+", "", text)
        if any(
            compact_text[index : index + 55] in compact_source
            for index in range(max(0, len(compact_text) - 54))
        ):
            continue
        accepted.append(text)
    return "\n\n".join(accepted)


def build_detailed_japanese(item, source_article: str, github_token: str = "") -> str:
    if source_article and github_token:
        target_max = min(900, max(380, int(len(source_article) * 1.25)))
        prompt = f"""次のニュース資料を、学習者向けの詳しい日本語記事に書き直してください。

必須条件:
- 上限は約{target_max}字。原資料の情報が少なければ短く終え、字数を埋めるために文章を足さない。
- 3～7段落。
- 原資料にある人物、組織、日付、金額、数値、場所、経緯、発言、各当事者の反応をできる限り残す。
- ニュースごとに固有の事実を中心にする。「社会への影響が注目される」「今後の動向が焦点」のような中身のない定型文は禁止。
- 原資料にない背景、評価、因果関係、将来予測を加えない。
- 「期待される」「重要である」「関心が高まる」「見守る必要がある」などの論評は、原資料に同じ内容が明記されていない限り書かない。
- すべての文について、根拠となる記述が原資料内に存在しなければならない。
- 原文を長く連続コピーせず、文章構造と表現を全面的に組み替える。
- 見出し記号や箇条書きは使わず、自然な報道文にする。
- 「告発」「起訴」「逮捕」「容疑」など法的に異なる言葉を絶対に混同しない。
- JSONだけを出力する。形式:
  {{"paragraphs":[{{"text":"書き直した1段落","evidence":["原資料からの完全一致の短い根拠1","根拠2"]}}]}}
- evidenceには、その段落の全事実を裏付ける原資料中の完全一致文字列を入れる。日付・数値・固有名詞の根拠を必ず含める。

カテゴリ: {item.category}
見出し: {item.title}
媒体: {item.source}

原資料:
{source_article}
"""
        try:
            raw_output = _github_model_rewrite(prompt, github_token)
            rewritten = _validated_rewrite(raw_output, source_article)
            if len(rewritten) >= 120:
                return rewritten

            plain_prompt = f"""次のニュース資料だけを使って、日本語の詳しい報道文に全面的に書き直してください。
原資料にない説明、評価、予測、一般論は一切書かないでください。
人物、組織、場所、数値、日時、経緯、発言をできる限り保ち、情報が尽きたら終了してください。
原文の文章を長く連続コピーせず、3～6段落の本文だけを出力してください。

見出し: {item.title}
媒体: {item.source}
原資料:
{source_article}
"""
            raw_output = _github_model_rewrite(plain_prompt, github_token)
            rewritten = _validated_plain_rewrite(raw_output, source_article)
            if len(rewritten) >= 120:
                return rewritten
        except Exception as exc:
            print(f"GitHub Models 改写失败：{item.category} - {exc}")

    if source_article:
        return (
            f"{item.source}は「{item.title}」と報じました。本文の取得には成功しましたが、"
            "事実確認を伴う自動改写が検証を通過しなかったため、誤情報を避けて詳細文の掲載を見送りました。"
            "詳しい内容は出典リンクで確認できます。"
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
