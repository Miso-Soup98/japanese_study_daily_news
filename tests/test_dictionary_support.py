import gzip
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from dictionary_support import JMDictLookup


class LocalJMDictLookup(JMDictLookup):
    def _ensure_dictionary(self):
        self.meta = {
            "checkedAt": datetime.now(timezone.utc).isoformat(),
            "lastModified": "test-date",
        }
        return True


class JMDictLookupTests(unittest.TestCase):
    def test_exact_entries_and_glosses_are_extracted(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<JMdict>
  <entry>
    <ent_seq>1</ent_seq>
    <k_ele><keb>見合わせる</keb><ke_pri>news1</ke_pri></k_ele>
    <r_ele><reb>みあわせる</reb></r_ele>
    <sense><pos>verb</pos><gloss>to postpone</gloss><gloss>to suspend</gloss></sense>
  </entry>
</JMdict>"""
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            with gzip.open(cache_dir / "JMdict_e.gz", "wb") as target:
                target.write(xml.encode("utf-8"))
            lookup = LocalJMDictLookup(cache_dir)
            result = lookup.lookup_many({"見合わせる", "存在しない"})
            self.assertEqual(result["存在しない"], [])
            self.assertTrue(result["見合わせる"][0]["common"])
            self.assertEqual(
                result["見合わせる"][0]["senses"][0]["glosses"],
                ["to postpone", "to suspend"],
            )


if __name__ == "__main__":
    unittest.main()
