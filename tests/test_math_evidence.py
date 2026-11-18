"""验证公式源码、原表行列及论文位置一起保留。"""

import unittest
from unittest.mock import patch

import evidence_extract
from math_evidence import markdown_formulas


BASE = "https://arxiv.org/html/2005.11401v4"


def from_article(body):
    items = evidence_extract._article_evidence("<article><h1>Paper</h1>" + body + "</article>",
                                              BASE, include_formulas=True)[1]
    return [item for item in items if item["kind"] == "formula"]


class MathEvidenceTests(unittest.TestCase):
    def test_inline_display_and_markdown_code(self):
        source = (r"Cost $5 and $25; \$42. Inline $x_i^2$ and \(\sqrt{y}\)." + "\n"
                  + "\x60$x$\x60\n\x60\x60\x60python\n$$ignore = 1$$\n\x60\x60\x60\n"
                  + r"$$\frac{a+b}{c}$$" + "\n" + r"\[S=\sum_{i=1}^n x_i\]")
        found = list(markdown_formulas(source))
        self.assertEqual([(tex, display) for tex, display, _ in found], [
            ("x_i^2", False), (r"\sqrt{y}", False), (r"\frac{a+b}{c}", True), (r"S=\sum_{i=1}^n x_i", True)])
        for tex, _, raw in found:
            self.assertIn(raw, source)
            self.assertIn(tex, raw)

    def test_markdown_formula_reaches_real_article_entry(self):
        tex = "\\begin{align}\na &= b+c \\\\\nd &= \\frac{e}{f}\\tag{7}\n\\end{align}"
        source = '<article><h1>Actual</h1><p id="method">' + tex + "</p></article>"
        with patch.object(evidence_extract.papers, "_read_html", return_value=source):
            paper = evidence_extract.extract_evidence(BASE, include_formulas=True)
        formula, = paper["items"]
        self.assertEqual((formula["label"], formula["latex"], formula["quote"]), ("公式 (7)", tex, tex))
        self.assertEqual(formula["source_url"], BASE + "#method")

    def test_bare_multiline_environments_skip_code_and_keep_source(self):
        for env in ("aligned", "alignedat", "gathered", "split", "cases", "pmatrix", "bmatrix",
                    "Bmatrix", "vmatrix", "Vmatrix", "matrix", "smallmatrix", "array"):
            with self.subTest(environment=env):
                arg = {"array": "{cc}", "alignedat": "{1}"}.get(env, "")
                tex = "\\begin{" + env + "}" + arg + "\n1 & 2 \\\\\n3 & 4\n\\end{" + env + "}"
                source = "\x60" + tex + "\x60\n~~~tex\n" + tex + "\n~~~\n" + tex
                self.assertEqual(list(markdown_formulas(source)), [(tex, True, tex)])
                equation, = from_article('<p id="method">' + source + "</p>")
                self.assertEqual((equation["latex"], equation["display"], equation["quote"]),
                                 (tex, True, tex))

    def test_nested_bare_matrices_and_next_formula(self):
        matrix = (r"\begin{pmatrix} a & \begin{pmatrix} b \\ c \end{pmatrix}"
                  + "\n" + r"\\ d & e \end{pmatrix}")
        self.assertEqual(list(markdown_formulas(matrix + " $x$")),
                         [(matrix, True, matrix), ("x", False, "$x$")])

    def test_mathml_annotation_and_code_are_not_repeated(self):
        output = from_article(r'<p id="p1">The term <math><semantics><mrow><mi>bad flattened text</mi></mrow>'
                              r'<annotation encoding="application/x-tex">\alpha_{1}</annotation></semantics></math>.'
                              r'<code>\(ignore\)</code><span>\(w^2\)</span></p>')
        self.assertEqual([i["latex"] for i in output], [r"\alpha_{1}", "w^2"])
        self.assertTrue(all(i["source_url"] == BASE + "#p1" for i in output))

    def test_multiline_equation_group_preserves_order_and_origin(self):
        html = (r'<table class="ltx_equationgroup" id="EG"><tr><td><math alttext="x"/>'
                r'</td><td><math alttext="=a^2"/></td></tr><tr><td><math alttext="y"/></td>'
                r'<td><math alttext="=\sqrt{b}"/></td></tr></table>')
        eq, = from_article(html)
        self.assertEqual(eq["latex"], "\\begin{aligned}\nx & =a^2 \\\\\ny & =\\sqrt{b}\n\\end{aligned}")
        self.assertEqual(eq["quote"], "x =a^2\ny =\\sqrt{b}")
        self.assertEqual(eq["source_url"], BASE + "#EG")
        self.assertTrue(eq["display"])

    def test_subnumbered_equations_get_independent_rows(self):
        html = '<table class="ltx_equationgroup" id="G">'
        for number in ("1a", "1b"):
            html += f'<tr id="e{number}"><td><math alttext="x={number}"/></td><td class="ltx_tag_equation">({number})</td></tr>'
        found = from_article(html + "</table>")
        self.assertEqual([i["label"] for i in found], ["公式 (1a)", "公式 (1b)"])
        self.assertEqual([i["source_url"] for i in found], [BASE + "#e1a", BASE + "#e1b"])

    def test_table_mixed_text_and_math_preserves_scalar(self):
        source = (r'<figure id="T1"><figcaption>Table 1: Test</figcaption><table><tr><th>Model</th>'
                  r'<th>Error <math alttext="\sigma^2"><mi>S</mi></math></th></tr><tr><td>Ours</td>'
                  r'<td><math><mn>4.5</mn><annotation encoding="application/x-tex">4.5</annotation></math></td>'
                  r'</tr></table></figure>')
        _, items = evidence_extract._article_evidence(source, BASE)
        self.assertEqual(items[0]["headers"], ["Model", r"Error \(\sigma^2\)"])
        self.assertEqual(items[0]["rows"], [["Ours", "4.5"]])
        self.assertEqual(items[1]["value"], "4.5")

    def test_no_invented_tex_for_pdf_or_incomplete_mathml(self):
        self.assertEqual(from_article('<table class="ltx_equation"><tr><td><math alttext="x"/>'
                                      '</td><td><math><mi>flattened</mi></math></td></tr></table>'), [])
        self.assertEqual(evidence_extract._pdf_items("x 2 / d k", "https://arxiv.org/pdf/2005.11401"), [])

    def test_sample_formulas_have_versioned_source(self):
        with patch.object(evidence_extract.papers, "_read_html", side_effect=OSError):
            for version, anchor, term in (("1706.03762v7", "S3.E1", r"\sqrt{d_{k}}"),
                                          ("2005.11401v4", "S2.Ex2", r"\prod_{i}^{N}")):
                paper = evidence_extract.extract_evidence(version, include_formulas=True)
                eq, = [i for i in paper["items"] if i["kind"] == "formula"]
                self.assertIn(term, eq["latex"])
                self.assertIn("预核", eq["source"])
                self.assertEqual(eq["source_url"], "https://arxiv.org/html/" + version + "#" + anchor)
