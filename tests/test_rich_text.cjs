const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const web = path.join(__dirname, "..", "web");
const context = {
  URL,
  window: {
    katex: require(path.join(web, "vendor/katex/katex.min.js")),
    markdownit: require(path.join(web, "vendor/markdown-it/markdown-it.min.js")),
  },
};
const source = fs.readFileSync(path.join(web, "rich-text.js"), "utf8");
const richText = vm.runInNewContext(source + "\nPaperRichText", context);
const node = () => ({ classList: { add() {} }, innerHTML: "" });
const render = (text, inline) => richText.render(node(), text, inline).innerHTML;
const mathCount = html => (html.match(/class="katex"/g) || []).length;

test("行内数学、独立公式和多行环境可在同一段笔记中排版", () => {
  const text = [
    "平均值 $x^2$，标准差 \\(\\sigma\\)。",
    "$$\\frac{1}{2}$$",
    "\\[", "\\begin{aligned}", "a &= b \\\\", "c &= d", "\\end{aligned}", "\\]",
  ].join("\n");
  const html = render(text);
  assert.equal(mathCount(html), 4);
  assert.equal((html.match(/class="math-block"/g) || []).length, 2);
  assert.equal(mathCount(render("方差 \\(\\sigma^2\\)", true)), 1);
});

test("同类矩阵嵌套时匹配到外层结束，保留后续公式", () => {
  const matrix = "\\begin{pmatrix}a&\\begin{pmatrix}b\\\\c\\end{pmatrix}\n\\\\d&e\\end{pmatrix}";
  const html = render(matrix + "\n\n$x$");
  assert.equal(mathCount(html), 2);
  assert.match(html, /<annotation[^>]*>[^<]*\\end\{pmatrix\}[^<]*\\end\{pmatrix\}<\/annotation>/);
  for (const tex of [
    "\\begin{cases}a & x>0\\\\ b & x<0\\end{cases}",
    "\\begin{array}{cc}a & b\\\\c & d\\end{array}",
    "\\begin{bmatrix}a & b\\end{bmatrix}",
  ]) assert.equal(mathCount(render(tex)), 1);
});

test("金额、转义字符、行内及围栏代码保留原样", () => {
  const tick = String.fromCharCode(96);
  const code = tick + "$var$" + tick;
  const text = ["价格 $5 and $10，字面 \\$abc\\$，表达式 $2x$。", code, "~~~text", "$x$ \\(v\\)", "~~~"].join("\n");
  const html = render(text);
  assert.equal(mathCount(html), 1);
  assert.match(html, /价格 \$5 and \$10，字面 \$abc\$/);
  assert.match(html, /<code>\$var\$<\/code>/);
  assert.match(html, /<code class="language-text">\$x\$ \\\(v\\\)/);
});

test("Markdown 保留格式，原始 HTML 与非网页链接以文字呈现", () => {
  const text = "**重点**\n原文 <b>标签</b>。 [本地](file:///draft.md) https://arxiv.org/abs/1706.03762";
  const html = render(text);
  assert.match(html, /<strong>重点<\/strong><br>/);
  assert.match(html, /&lt;b&gt;标签&lt;\/b&gt;/);
  assert.doesNotMatch(html, /href="file:/);
  assert.match(html, /href="https:\/\/arxiv.org\/abs\/1706.03762"/);
  assert.match(html, /rel="noopener noreferrer"/);
});

test("独立公式直接排版，不支持的命令在两条入口保留原文", () => {
  const tex = "\\unknownpapercommand{x}";
  assert.equal(mathCount(richText.formula(node(), "a^2+b^2").innerHTML), 1);
  assert.match(richText.formula(node(), tex).innerHTML, /math-source-fallback[^>]*>\\unknownpapercommand\{x\}<\/code>/);
  assert.match(render("\\(" + tex + "\\)"), /math-source-fallback[^>]*>\\\(\\unknownpapercommand\{x\}\\\)<\/code>/);
});
