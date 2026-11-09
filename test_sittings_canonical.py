import json
import unittest
from pathlib import Path

from sittings.canonical import canonical_bytes, canonical_sha256


class CanonicalTests(unittest.TestCase):
    def test_golden_vectors(self):
        vectors = json.loads(Path("sittings/testdata/canonical-vectors.json").read_text())
        self.assertEqual(vectors["format"], "operant.canonical-vectors/v1")
        for vector in vectors["vectors"]:
            value = json.loads(vector["input_json_text"])
            encoded = canonical_bytes(value)
            self.assertEqual(encoded.decode(), vector["canonical_text"])
            self.assertEqual(canonical_sha256(value), vector["sha256"])

    def test_rejects_nonportable_values(self):
        for value in (
            {"x": 1.0}, {"x": float("nan")}, {"x": float("inf")},
            {"é": "non-ASCII key"}, {"x": 1 << 53}, {"x": -(1 << 53)},
            {1: "non-string key"}, {"nested": {False: "non-string key"}},
            {"nested": [0, {"float": 2.5}]},
            {"nested": [0, {"unsafe": 1 << 53}]},
            {"tuple": (1, 2)},
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                canonical_bytes(value)

    def test_allows_safe_integers_and_non_ascii_values(self):
        value = {"max": (1 << 53) - 1, "min": -((1 << 53) - 1), "text": "café"}
        self.assertEqual(json.loads(canonical_bytes(value)), value)


if __name__ == "__main__":
    unittest.main()
