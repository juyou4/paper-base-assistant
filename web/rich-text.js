"use strict";

// Markdown 负责分辨代码、转义与正文；只有正文中的数学标记才交给 KaTeX。
const PaperRichText = (() => {
  const md = window.markdownit({ html: false, linkify: true, breaks: true, typographer: false });
  md.linkify.set({ fuzzyLink: false, fuzzyEmail: false, fuzzyIP: false });
  md.disable("image");
  md.validateLink = value => {
    try {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password;
    } catch {
      return false;
    }
  };
  md.renderer.rules.link_open = (tokens, index, options, env, renderer) => {
    tokens[index].attrSet("target", "_blank");
    tokens[index].attrSet("rel", "noopener noreferrer");
    return renderer.renderToken(tokens, index, options);
  };

  const maxFormula = 8000;
  function nextMarker(source, marker, start) {
    let position = source.indexOf(marker, start);
    while (position >= 0) {
      let before = position;
      while (source[before - 1] === "\\") before--;
      if ((position - before) % 2 === 0) break;
      position = source.indexOf(marker, position + marker.length);
    }
    return position;
  }

  function mathAt(source, start, limit) {
    const rest = source.slice(start, Math.min(limit, start + maxFormula + 64));
    let open, close, display = false, environment = false;
    if (rest.startsWith("$$")) {
      open = close = "$$";
      display = true;
    } else if (rest.startsWith("\\[")) {
      open = "\\["; close = "\\]"; display = true;
    } else if (rest.startsWith("\\(")) {
      open = "\\("; close = "\\)";
    } else if (rest.startsWith("$")) {
      if (!rest[1] || /\s/.test(rest[1])) return null;
      open = close = "$";
    } else {
      const match = /^\\begin\{((?:equation|align(?:at)?|gather|multline|[pbBvV]?matrix)\*?|aligned(?:at)?|gathered|split|[dr]?cases|array|smallmatrix)\}/.exec(rest);
      if (!match) return null;
      open = match[0]; close = "\\end{" + match[1] + "}";
      display = environment = true;
    }
    let end = nextMarker(rest, close, open.length);
    if (environment) {
      let nested = nextMarker(rest, open, open.length);
      while (nested !== -1 && nested < end) {
        nested = nextMarker(rest, open, nested + open.length);
        end = nextMarker(rest, close, end + close.length);
      }
    }
    if (end === -1) return null;
    const inside = rest.slice(open.length, end);
    if (!inside.trim()) return null;
    if (open === "$" && (/\n|\s$/.test(inside) || /[\d$]/.test(rest[end + 1] || "") ||
        /^\d[\d.,]*\s+(?:[A-Za-z]{2,}\b|[\u3400-\u9fff])/.test(inside))) return null;
    const raw = rest.slice(0, end + close.length);
    return { latex: environment ? raw : inside, raw, display, end: start + raw.length };
  }

  function formulaHtml(latex, display, original = latex) {
    if (latex.length <= maxFormula) {
      try {
        const html = window.katex.renderToString(latex, {
          displayMode: display, output: "htmlAndMathml", trust: false,
          throwOnError: true, strict: "ignore", maxSize: 16, maxExpand: 800
        });
        return '<span class="' + (display ? "math-block" : "math-inline") + '">' + html + "</span>";
      } catch {
        // 超出渲染器支持范围时，保留原始表达式供核对和复制。
      }
    }
    return '<code class="math-source-fallback" title="保留公式原文">' + md.utils.escapeHtml(original) + "</code>";
  }

  md.inline.ruler.before("escape", "paper_math", (state, silent) => {
    if (state.src[state.pos] !== "$" && state.src[state.pos] !== "\\") return false;
    const math = mathAt(state.src, state.pos, state.posMax);
    if (!math) return false;
    if (!silent) state.push("paper_math", "", 0).meta = math;
    state.pos = math.end;
    return true;
  });
  md.block.ruler.before("paragraph", "paper_display_math", (state, start, end, silent) => {
    const offset = state.bMarks[start] + state.tShift[start];
    const prefix = state.src.slice(offset, offset + 7);
    if (!prefix.startsWith("$$") && !prefix.startsWith("\\[") && !prefix.startsWith("\\begin{")) return false;
    const math = mathAt(state.src, offset, state.eMarks[end - 1]);
    if (!math || !math.display) return false;
    let last = start;
    while (last + 1 < end && state.eMarks[last] < math.end) last++;
    if (state.src.slice(math.end, state.eMarks[last]).trim()) return false;
    if (!silent) {
      const token = state.push("paper_math", "", 0);
      token.map = [start, last + 1];
      token.meta = math;
      state.line = last + 1;
    }
    return true;
  }, { alt: ["paragraph"] });
  md.renderer.rules.paper_math = (tokens, index) => {
    const { latex, display, raw } = tokens[index].meta;
    return formulaHtml(latex, display, raw);
  };

  function render(node, value, inline = false) {
    const text = String(value ?? "");
    node.classList.add("paper-rich");
    node.innerHTML = inline ? md.renderInline(text) : md.render(text);
    return node;
  }
  function formula(node, latex, display = true) {
    node.classList.add("paper-rich", "formula-preview");
    node.innerHTML = formulaHtml(latex, display);
    return node;
  }
  return { render, formula };
})();
