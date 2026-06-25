import unittest

from generate_daily_news import Furigana


class FuriganaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.furigana = Furigana()

    def assert_reading(self, text, surface, reading):
        rendered = self.furigana.annotate(text)
        expected = f'data-reading="{reading}">{surface}<rt>{reading}</rt>'
        self.assertIn(expected, rendered)

    def test_america_abbreviation_before_company(self):
        self.assert_reading("米「マイクロン・テクノロジー」", "米", "べい")

    def test_america_abbreviation_before_government(self):
        self.assert_reading("米政府が発表した", "米", "べい")

    def test_rice_harvest_context(self):
        self.assert_reading("米を収穫する農家", "米", "こめ")

    def test_compound_readings(self):
        self.assert_reading("米中関係と新米", "米中", "べいちゅう")
        self.assert_reading("米中関係と新米", "新米", "しんまい")

    def test_digits_are_never_inside_ruby(self):
        rendered = self.furigana.annotate(
            "日経平均は2800円上昇し、7万2000円台になった。2026年6月25日。"
        )
        self.assertNotRegex(rendered, r"<ruby[^>]*>[^<]*\d")
        self.assertIn("2800<ruby", rendered)
        self.assertIn('data-reading="まん">万<rt>まん</rt>', rendered)

    def test_latin_letters_are_never_inside_ruby(self):
        rendered = self.furigana.annotate("AI半導体とC130H輸送機")
        self.assertNotRegex(rendered, r"<ruby[^>]*>[^<]*[A-Za-z]")
        rendered = self.furigana.annotate("W杯")
        self.assertNotRegex(rendered, r"<ruby[^>]*>[^<]*[A-Za-z]")
        self.assertIn('W<ruby data-reading="かっぷ">杯', rendered)

    def test_visible_kana_is_not_repeated_in_ruby(self):
        rendered = self.furigana.annotate("けが人はいない")
        self.assertIn('けが<ruby data-reading="にん">人<rt>にん</rt></ruby>', rendered)
        self.assertNotIn('data-reading="けがにん">けが人', rendered)

    def test_okurigana_stays_outside_ruby(self):
        rendered = self.furigana.annotate("支払われた物を取り組む")
        self.assertIn('<ruby data-reading="しはら">支払<rt>しはら</rt></ruby>わ', rendered)
        self.assertIn(
            '<ruby data-reading="と">取<rt>と</rt></ruby>り'
            '<ruby data-reading="く">組<rt>く</rt></ruby>む',
            rendered,
        )


if __name__ == "__main__":
    unittest.main()
