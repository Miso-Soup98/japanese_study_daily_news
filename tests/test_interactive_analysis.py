import unittest

from learning_support import build_interactive_analysis


class InteractiveAnalysisTests(unittest.TestCase):
    def test_selected_words_include_dictionary_and_conjugation_data(self):
        data = build_interactive_analysis("政府は運転を見合わせました。")
        token = next(item for item in data["tokens"] if item["surface"] == "見合わせ")
        self.assertEqual(token["lemma"], "見合わせる")
        self.assertEqual(token["reading"], "みあわせ")
        self.assertEqual(token["pos"], "动词")
        self.assertTrue(token["conjugationForm"])

    def test_article_grammar_candidates_are_collected(self):
        data = build_interactive_analysis(
            "地震を受けて、会社は安全確認を進めるとしています。"
        )
        patterns = {item["pattern"] for item in data["grammar"]}
        self.assertIn("を受けて", patterns)
        self.assertIn("としています", patterns)

    def test_known_chinese_meaning_is_reused(self):
        data = build_interactive_analysis(
            "半導体関連株が上昇した。",
            [{"word": "半導体", "meaning": "半导体", "usage": "名词用法"}],
        )
        token = next(item for item in data["tokens"] if item["lemma"] == "半導体")
        self.assertEqual(token["meaning"], "半导体")
        self.assertEqual(token["usage"], "名词用法")

    def test_exact_dictionary_reading_is_high_confidence(self):
        dictionary_entries = {
            "見合わせる": [
                {
                    "spellings": ["見合わせる"],
                    "readings": ["みあわせる"],
                    "common": True,
                    "senses": [{"pos": ["verb"], "glosses": ["to postpone"]}],
                }
            ]
        }
        data = build_interactive_analysis(
            "運転を見合わせる。", dictionary_entries=dictionary_entries
        )
        token = next(item for item in data["tokens"] if item["lemma"] == "見合わせる")
        self.assertEqual(token["confidence"], "高")
        self.assertEqual(token["dictionary"]["senses"][0]["glosses"], ["to postpone"])

    def test_unknown_word_does_not_claim_a_definition(self):
        data = build_interactive_analysis("未知語です。", dictionary_entries={})
        token = next(item for item in data["tokens"] if "未知" in item["surface"])
        self.assertEqual(token["confidence"], "低")
        self.assertIsNone(token["dictionary"])

    def test_ambiguous_grammar_is_marked_medium(self):
        data = build_interactive_analysis("事故により運転を止めました。")
        grammar = next(item for item in data["grammar"] if item["pattern"] == "により")
        self.assertEqual(grammar["confidence"], "中")
        self.assertIn("前后文", grammar["confidenceReason"])

    def test_multiple_dictionary_entries_are_not_marked_high(self):
        entries = {
            "見合わせ": [
                {
                    "spellings": ["見合わせ"],
                    "readings": ["みあわせ"],
                    "common": False,
                    "senses": [{"pos": ["noun"], "glosses": ["looking at each other"]}],
                },
                {
                    "spellings": ["見合わせ"],
                    "readings": ["みあわせ"],
                    "common": True,
                    "senses": [{"pos": ["noun"], "glosses": ["postponement", "suspension"]}],
                },
            ]
        }
        data = build_interactive_analysis(
            "列車は運転見合わせとなりました。", dictionary_entries=entries
        )
        token = next(item for item in data["tokens"] if item["lemma"] == "見合わせ")
        self.assertEqual(token["confidence"], "中")
        glosses = [
            gloss
            for sense in token["dictionary"]["senses"]
            for gloss in sense["glosses"]
        ]
        self.assertIn("looking at each other", glosses)
        self.assertIn("suspension", glosses)

    def test_token_keeps_sentence_context(self):
        data = build_interactive_analysis(
            "列車は運転を見合わせました。二人は顔を見合わせました。"
        )
        matches = [item for item in data["tokens"] if item["surface"] == "見合わせ"]
        self.assertEqual(len(matches), 2)
        self.assertNotEqual(matches[0]["context"], matches[1]["context"])


if __name__ == "__main__":
    unittest.main()
