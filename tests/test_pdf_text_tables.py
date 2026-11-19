import unittest
from unittest.mock import Mock

from pdf_text_tables import captioned_numeric_tables


def words(y, *cells):
    return [{"text": value, "top": y, "x0": left, "x1": right} for left, right, value in cells]


def layout(shift=0):
    result = words(445, (162, 180, "plain"), (209, 235, "ResNet"), (310, 345, "unrelated"))
    for y, layer, a, b in [(457, 18, "27.94", "27.88"), (469, 34, "28.54", "25.03")]:
        dx = shift if layer == 34 else 0
        result += words(y, (101, 111, str(layer)), (113, 134, "layers"), (161, 181, a),
                        (212 + dx, 232 + dx, b), (310, 345, "other"))
    return result + words(485, (50, 70, "Table"), (72, 81, "2."), (83, 104, "Top-1"),
                          (107, 140, "error"), (144, 190, "ImageNet"), (195, 250, "validation."))


class BorderlessTableTests(unittest.TestCase):
    def test_caption_header_and_two_models_with_neighbouring_column(self):
        page = Mock()
        page.extract_words.return_value = layout()
        label, caption, cells = list(captioned_numeric_tables(page))[0]
        self.assertEqual(label, "Table 2")
        self.assertIn("Top-1", caption)
        self.assertEqual(cells, [["", "plain", "ResNet"], ["18 layers", "27.94", "27.88"],
                                 ["34 layers", "28.54", "25.03"]])

    def test_displaced_or_missing_header_does_not_get_a_value(self):
        for content in [layout(12), [w for w in layout() if w["text"] != "plain"]]:
            page = Mock()
            page.extract_words.return_value = content
            self.assertFalse(list(captioned_numeric_tables(page)))
