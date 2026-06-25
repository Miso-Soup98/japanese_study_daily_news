from __future__ import annotations

import re
from collections import Counter


CATEGORY_GUIDANCE = {
    "政治": (
        "政治ニュースでは、発表した人物や組織だけでなく、法的な手続き、与野党の反応、"
        "政策への影響を分けて考えることが大切です。",
        "今後は、関係機関の正式な判断と、国会や世論の反応が焦点になるとみられます。",
    ),
    "经济": (
        "経済ニュースでは、一日の数字だけで結論を出さず、金利、為替、企業業績、海外市場との関係を見る必要があります。",
        "今回の動きを受けて、投資家の判断や企業活動、家計への波及が続くかどうかが注目されます。",
    ),
    "娱乐": (
        "エンタメニュースでは、作品や出演者の情報に加え、公開時期、制作側の狙い、視聴者の反応も重要です。",
        "今後は、追加情報の発表と、作品がどのように受け止められるかに関心が集まりそうです。",
    ),
    "体育": (
        "スポーツニュースでは、結果だけでなく、選手の状態、戦術、対戦相手、次の試合への影響を確認する必要があります。",
        "今後の試合や大会に向けて、今回示された課題がどのように改善されるかが注目されます。",
    ),
    "社会": (
        "社会ニュースでは、被害や影響の範囲、公的機関の対応、生活に必要な情報を優先して確認することが大切です。",
        "状況は変化する可能性があるため、自治体や関係機関が発表する最新情報に注意する必要があります。",
    ),
    "数码科技": (
        "技術ニュースでは、新しさだけでなく、実用化の時期、性能、費用、安全性、既存サービスへの影響を考える必要があります。",
        "今後は、実際の製品やサービスへの採用と、競合企業の対応が焦点になるとみられます。",
    ),
    "军事": (
        "安全保障ニュースでは、政府の公式説明、予算、法制度、同盟国との関係、周辺国の反応を分けて読むことが重要です。",
        "今後は、具体的な制度設計や運用方法について、政府がどのように説明するかが注目されます。",
    ),
    "新闻": (
        "このニュースは、当日の各分野の中でも生活や社会への影響が特に大きいものとして選ばれました。",
        "続報によって内容が更新される可能性があるため、出典の最新情報も確認してください。",
    ),
}

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


def build_detailed_japanese(item) -> str:
    background, outlook = CATEGORY_GUIDANCE.get(
        item.category, CATEGORY_GUIDANCE["新闻"]
    )
    headline = item.title.rstrip("。")
    return (
        f"【概要】{item.source}によると、{headline}。この報道の中心は、見出しに示された出来事が、"
        f"どのような影響を与えるかという点です。\n\n"
        f"【背景と読み方】{background} 数字や発言だけを見るのではなく、誰が、いつ、何を決めたのか、"
        f"または何が起きたのかについて整理すると、内容を理解しやすくなります。\n\n"
        f"【影響】この出来事を受けて、関係する組織や人々がどのように対応するかが重要になります。"
        f"短期的な反応だけでなく、制度、暮らし、産業、国際関係などに影響が広がるかどうかを見る必要があります。\n\n"
        f"【今後の注目点】{outlook}"
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
