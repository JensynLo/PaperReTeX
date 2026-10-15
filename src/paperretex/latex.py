"""LaTeX emission with a deliberately restricted math command vocabulary."""

import re
import unicodedata

ESCAPES = dict(
    zip(
        "\\{}$&#%_~^",
        [
            r"\textbackslash{}",
            r"\{",
            r"\}",
            r"\$",
            r"\&",
            r"\#",
            r"\%",
            r"\_",
            r"\textasciitilde{}",
            r"\textasciicircum{}",
        ],
    )
)
MATH_COMMANDS = set(
    """mathring mid mathfrak mathscr checkmark sf AA prime bigcup bigcap bigoplus bigotimes bigvee bigwedge uparrow downarrow dagger ddagger lesssim gtrsim odot wedge vee symbf symbfit symcal symbb symup alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi pi varpi rho varrho sigma varsigma tau upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega frac dfrac tfrac cfrac sqrt left right middle big Big bigg Bigg bigl bigr Bigl Bigr biggl biggr Biggl Biggr sum prod coprod int iint iiint oint limits nolimits infty partial nabla pm mp times div cdot cdots dots ldots vdots ddots ast star circ bullet oplus otimes cap cup subset supset subseteq supseteq in notin ni setminus emptyset varnothing forall exists neg land lor le leq ge geq neq ne approx sim simeq cong equiv propto doteq to mapsto gets rightarrow leftarrow Rightarrow Leftarrow Leftrightarrow leftrightarrow longrightarrow Longrightarrow iff implies perp parallel angle triangle triangleright triangleleft top bot ell hbar Re Im aleph lvert rvert vert lVert rVert Vert lceil rceil lfloor rfloor langle rangle backslash mathbf boldsymbol mathcal mathbb mathrm mathit mathsf mathtt mathnormal operatorname text textrm textit textbf hat widehat bar overline underline vec dot ddot tilde widetilde breve check acute grave overbrace underbrace overset underset stackrel phantom vphantom hphantom strut displaystyle textstyle scriptstyle scriptscriptstyle quad qquad enspace thinspace medspace thickspace sin cos tan cot sec csc arcsin arccos arctan sinh cosh tanh log ln exp min max arg argmin argmax lim limsup liminf sup inf det dim ker gcd Pr bmod pmod mod binom dbinom tbinom choose atop begin end label tag nonumber notag cr cases substack mathop mathrel mathbin mathord mathopen mathclose mathpunct mkern kern space pmatrix bmatrix matrix Bmatrix vmatrix Vmatrix array gathered aligned split smallmatrix hdotsfor hline multicolumn textcolor color vspace small footnotesize tiny rm cal bf it ensuremath mathstrut not hfill colon coloneqq""".split()
)
ENVIRONMENTS = {
    "gathered",
    "aligned",
    "split",
    "matrix",
    "pmatrix",
    "bmatrix",
    "Bmatrix",
    "vmatrix",
    "Vmatrix",
    "smallmatrix",
    "cases",
    "array",
}


def escape(text):
    text = (
        unicodedata
        .normalize("NFC", text)
        .replace("\ufb01", "fi")
        .replace("\ufb02", "fl")
        .replace("\ufb00", "ff")
    )
    out = []
    for c in text:
        if c in ("\ufe00", "\ufe01"):
            continue
        if c == "✓":
            out.append(r"\ding{51}")
            continue
        if c == "✗":
            out.append(r"\ding{55}")
            continue
        if c in ESCAPES:
            out.append(ESCAPES[c])
        elif c == "\ufffd" or (ord(c) < 32 and c not in "\n\t"):
            out.append("[?]")
        elif (
            unicodedata.category(c) == "Sm"
            or "\u0370" <= c <= "\u03ff"
            or 0x1D400 <= ord(c) <= 0x1D7FF
        ):
            out.append(r"\ensuremath{" + c + "}")
        else:
            out.append(c)
    return "".join(out)


def safe_math(value):
    value = value.strip()
    value = re.sub(r"\\AA\b", lambda _: r"\mathring{A}", value)
    value = re.sub(r"\\(?:mathbf|boldsymbol|pmb|bm)\b", lambda _: r"\symbf", value)
    if value.startswith("$$") and value.endswith("$$"):
        value = value[2:-2]
    elif value.startswith("$") and value.endswith("$"):
        value = value[1:-1]
    elif value.startswith(r"\[") and value.endswith(r"\]"):
        value = value[2:-2]
    value = re.sub(r"\\label\s*\{[^{}]*\}", "", value)
    for command in re.findall(r"\\([a-zA-Z]+)", value):
        if command not in MATH_COMMANDS:
            raise ValueError("Unsupported math command: \\" + command)
    for env in re.findall(r"\\(?:begin|end)\s*\{([^{}]+)\}", value):
        if env not in ENVIRONMENTS:
            raise ValueError("Unsupported math environment: " + env)
    # Prevent TeX comments / ^^ control-character syntax and unbalanced grouping.
    if re.search(r"(?<!\\)(?:\\\\)*%", value) or "^^" in value:
        raise ValueError("Unsafe math syntax")
    depth = 0
    for token in re.findall(r"\\.|[{}]", value):
        if token == "{":
            depth += 1
        if token == "}":
            depth -= 1
        if depth < 0:
            raise ValueError("Unbalanced math braces")
    if depth:
        raise ValueError("Unbalanced math braces")
    return value


def rich_text(value):
    """Preserve only validated dollar-delimited inline math from model exports."""
    parts = re.split(r"(?<!\\)(\$\$.*?\$\$|\$[^$\n]+\$)", value, flags=re.S)
    out = []
    for part in parts:
        if part.startswith("$") and part.endswith("$"):
            try:
                out.append("$" + safe_math(part) + "$")
            except ValueError:
                out.append(escape(part))
        else:
            out.append(escape(part))
    return "".join(out)


PREAMBLE = r"""\documentclass[10pt]{article}
\usepackage[a4paper,margin=22mm]{geometry}
\usepackage{fontspec}
\usepackage{amsmath,amssymb}
\usepackage{unicode-math}
\usepackage{graphicx,xcolor,longtable,booktabs,array,pifont,multirow}
\definecolor{sourceblue}{rgb}{0.21,0.49,0.74}
\setmainfont{texgyretermes}[Extension=.otf,UprightFont=*-regular,BoldFont=*-bold,ItalicFont=*-italic,BoldItalicFont=*-bolditalic]
\setmathfont{texgyretermes-math.otf}
\providecommand{\argmin}{\mathop{\mathrm{arg\,min}}}
\providecommand{\argmax}{\mathop{\mathrm{arg\,max}}}
\setlength{\parindent}{0pt}
\setlength{\parskip}{5pt}
\setlength{\emergencystretch}{4em}
\newcommand{\fitdisplay}[1]{\begingroup\sbox0{$\displaystyle #1$}\ifdim\wd0>\dimexpr\linewidth-2em\relax\resizebox{\dimexpr\linewidth-2em\relax}{!}{\usebox0}\else\usebox0\fi\endgroup}
\newcommand{\fitmath}[1]{\par\begingroup\sbox0{$\displaystyle #1$}%
\noindent\makebox[\linewidth][c]{\ifdim\wd0>\linewidth\resizebox{\linewidth}{!}{\usebox0}\else\usebox0\fi}\par\endgroup}
\newcommand{\asset}[2]{\par\begingroup\centering\includegraphics[width=#1\linewidth,keepaspectratio]{#2}\par\endgroup}
\begin{document}
"""


# Conventional flow layout. No glyph coordinates or source-PDF backgrounds.
def source_asset(block):
    """Keep a figure/table at its reading-order position at its measured size."""
    box = block["source_bbox"]
    width = round(box[2] - box[0], 2)
    value = r"\par\noindent\begin{minipage}{\linewidth}\centering" + "\n"
    value += (
        r"\makebox[\linewidth][c]{\includegraphics[width="
        + str(width)
        + r"bp]{"
        + block["asset"]
        + "}}\n"
    )
    for caption in block.get("caption_regions", []):
        gap = max(0, caption["source_bbox"][1] - box[3])
        size = caption.get("font_size", 9)
        caption_width = round(caption["source_bbox"][2] - caption["source_bbox"][0], 2)
        value += (
            r"\par\vspace{"
            + str(round(gap, 2))
            + r"bp}\makebox[\linewidth][c]{\begin{minipage}{"
            + str(caption_width)
            + r"bp}\raggedright\fontsize{"
            + str(size)
            + r"bp}{"
            + str(round(size * 1.2, 2))
            + r"bp}\selectfont "
            + caption.get("tex", rich_text(caption["text"]))
            + r"\par\end{minipage}}\par"
            + "\n"
        )
        box = caption["source_bbox"]
    return value + r"\end{minipage}\par" + "\n"


def asset_gap(block, following):
    bottom = (block.get("caption_regions") or [block])[-1]["source_bbox"][3]
    if not following:
        return 0
    gap = following["source_bbox"][1] - bottom
    # A heading supplies its own before-skip; ordinary paragraphs do not.
    if following["type"] == "heading" and not block.get("wide"):
        gap -= 10
    return max(0, min(40, gap))


def emit_document(blocks, destination, layout=None):
    layout = layout or dict(
        paper_width=595.28,
        paper_height=841.89,
        left=62,
        right=62,
        top=62,
        bottom=62,
        column_gap=18,
        columns=1,
        font_size=10,
    )
    columns = layout["columns"]
    pre = PREAMBLE.replace(
        r"\documentclass[10pt]{article}",
        r"\documentclass[10pt" + (",twocolumn" if columns == 2 else "") + r"]{article}",
    )
    if layout.get("math_font_family") == "computer_modern":
        pre = pre.replace("texgyretermes-math.otf", "latinmodern-math.otf")
    if layout.get("preserve_source_breaks"):
        pre = pre.replace(
            r"\begin{document}",
            r"\usepackage{cuted}"
            + "\n"
            + r"\raggedbottom"
            + "\n"
            + r"\begin{document}",
        )
        pre = pre.replace(
            r"\begin{document}", r"\usepackage{enumitem}" + "\n" + r"\begin{document}"
        )
        boundaries = r"""\makeatletter
\newcommand{\sourcecolumnbreak}{\if@twocolumn\if@firstcolumn\newpage\fi\fi}
\newcommand{\sourcepagebreak}[1]{\ifnum\value{page}<#1\clearpage\fi}
\makeatother
"""
        pre = pre.replace(r"\begin{document}", boundaries + r"\begin{document}")
    geometry = ",".join(
        f"{key}={layout[value]}bp"
        for key, value in [
            ("paperwidth", "paper_width"),
            ("paperheight", "paper_height"),
            ("left", "left"),
            ("right", "right"),
            ("top", "top"),
            ("bottom", "bottom"),
        ]
    )
    pre = pre.replace(
        r"\usepackage[a4paper,margin=22mm]{geometry}",
        r"\usepackage[" + geometry + r"]{geometry}",
    )
    pre = pre.replace(
        r"\setlength{\parindent}{0pt}", r"\setlength{\parindent}{1em}"
    ).replace(r"\setlength{\parskip}{5pt}", r"\setlength{\parskip}{0pt}")
    if "paragraph_indent" in layout:
        pre = pre.replace(
            r"\setlength{\parindent}{1em}",
            r"\setlength{\parindent}{" + str(layout["paragraph_indent"]) + "bp}",
        )
    if layout.get("indent_after_heading"):
        pre = pre.replace(
            r"\begin{document}",
            r"\usepackage{indentfirst}" + "\n" + r"\begin{document}",
        )
    spacing = []
    for side in ("above", "below"):
        if "display_" + side in layout:
            for suffix in ("displayskip", "displayshortskip"):
                spacing.append(
                    "\\setlength{\\"
                    + side
                    + suffix
                    + "}{"
                    + str(layout["display_" + side])
                    + "bp}"
                )
    if spacing:
        pre = pre.replace(
            r"\begin{document}", r"\begin{document}" + "\n" + "\n".join(spacing)
        )
    body_leading = layout.get("line_height", round(layout["font_size"] * 1.2, 2))
    pre = pre.replace(
        r"\begin{document}",
        r"\setlength{\columnsep}{"
        + str(layout["column_gap"])
        + r"bp}"
        + "\n"
        + r"\usepackage{caption}"
        + "\n"
        + r"\captionsetup{font=small}"
        + "\n"
        + r"\begin{document}"
        + "\n"
        + r"\fontsize{"
        + str(layout["font_size"])
        + "}{"
        + str(body_leading)
        + r"}\selectfont",
    )
    if "Libertine" in layout.get("body_font", ""):
        pre = pre.replace(
            r"\setmainfont{texgyretermes}[Extension=.otf,UprightFont=*-regular,BoldFont=*-bold,ItalicFont=*-italic,BoldItalicFont=*-bolditalic]",
            r"\setmainfont{LinLibertine_R.otf}[BoldFont=LinLibertine_RB.otf,ItalicFont=LinLibertine_RI.otf,BoldItalicFont=LinLibertine_RBI.otf]"
            + "\n"
            + r"\setsansfont{LinBiolinum_R.otf}",
        )
    elif layout.get("body_font", "").startswith(("CMR", "LMRoman")):
        pre = pre.replace("texgyretermes}", "lmroman10}")
    pre = pre.replace(
        r"\begin{document}",
        r"\usepackage{titlesec}"
        + "\n"
        + r"\titleformat{\section}{\normalsize\bfseries}{\thesection.}{.5em}{}"
        + "\n"
        + r"\titleformat{\subsection}{\normalsize\bfseries}{\thesubsection.}{.5em}{}"
        + "\n"
        + r"\titlespacing*{\section}{0pt}{1em}{.5em}"
        + "\n"
        + r"\titlespacing*{\subsection}{0pt}{.8em}{.4em}"
        + "\n"
        + r"\begin{document}",
    )
    lines = [pre]
    page = None
    source_page_index = 0
    previous_column = None
    body_started = False
    for i, b in enumerate(blocks):
        if b["page"] != page:
            source_page_index += 1
            if page is not None and layout.get("preserve_source_breaks"):
                lines.append(r"\sourcepagebreak{" + str(source_page_index) + "}\n")
            previous_column = None
            body_started = False
            requested = b.get("layout_columns", columns)
            if requested != columns:
                columns = requested
                lines.append(("\\twocolumn" if columns == 2 else "\\onecolumn") + "\n")
            page = b["page"]
            lines.append("\n% Source PDF page " + str(page) + "\n")
        lines.append("% " + b["id"] + " / " + b["type"] + "\n")
        if b.get("unresolved_formulas"):
            lines.append(
                "% Unresolved formulas: " + ", ".join(b["unresolved_formulas"]) + "\n"
            )
        typ = b["type"]
        if b.get("merged_into"):
            lines.append("% Content represented by " + b["merged_into"] + "\n")
            continue
        if b.get("role", "").startswith("frontmatter"):
            continue
        if (
            layout.get("preserve_source_breaks")
            and previous_column == "left"
            and b.get("column") == "right"
        ):
            lines.append("\\sourcecolumnbreak\n")
        previous_column = b.get("column", previous_column)
        if typ == "heading":
            text = b["text"].strip()
            numbered = re.match(r"^(\d+(?:\.\d+)*\.?)[ \t]+(.+)", text, re.S)
            if numbered:
                level = min(3, len(numbered[1].rstrip(".").split(".")))
                text = numbered[2]
            else:
                level = min(int(b.get("level", 1)), 3)
            if (
                i == 0
                and not numbered
                and text.lower() not in ("abstract", "introduction")
            ):
                size = b.get("font_size", 14)
                offset = (
                    max(
                        0,
                        b.get("bbox", [0, 0])[1] * layout["paper_height"] / 1000
                        - layout["top"],
                    )
                    if b.get("bbox")
                    else 0
                )
                title_width = (
                    (b["bbox"][2] - b["bbox"][0]) * layout["paper_width"] / 1000 * 1.015
                    if b.get("bbox")
                    else layout["paper_width"] - layout["left"] - layout["right"]
                )
                face = (
                    r"\sffamily " if "Biolinum" in b.get("font_name", "") else ""
                ) + (r"\bfseries " if b.get("bold", True) else "")
                title = (
                    r"\vspace*{"
                    + str(round(offset, 2))
                    + r"bp}{\centering\begin{minipage}{"
                    + str(round(title_width, 2))
                    + r"bp}\centering\fontsize{"
                    + str(size)
                    + "}{"
                    + str(round(size * 1.2, 2))
                    + r"}\selectfont "
                    + face
                    + rich_text(text)
                    + r"\par\end{minipage}\par}"
                )
                previous = b
                for front in blocks[i + 1 :]:
                    if front.get("merged_into"):
                        continue
                    role = front.get("role", "")
                    if not role.startswith("frontmatter"):
                        break
                    if front.get("bbox") and previous.get("bbox"):
                        gap = max(
                            0,
                            min(
                                45,
                                (front["bbox"][1] - previous["bbox"][3])
                                * layout["paper_height"]
                                / 1000,
                            ),
                        )
                        title += r"\vspace{" + str(round(gap, 2)) + r"bp}"
                    previous = front
                    if role == "frontmatter_figure":
                        if layout.get("preserve_source_breaks") and front.get(
                            "source_bbox"
                        ):
                            title += source_asset(front)
                            if front.get("caption_regions"):
                                previous = front["caption_regions"][-1]
                        else:
                            title += (
                                r"{\centering\includegraphics[width=\linewidth,height=.42\textheight,keepaspectratio]{"
                                + front["asset"]
                                + r"}\par}"
                            )
                            if front.get("caption"):
                                title += (
                                    r"\captionof*{figure}{"
                                    + rich_text(front["caption"])
                                    + "}"
                                )
                        continue
                    if role == "frontmatter_abstract_heading":
                        title += r"\section*{Abstract}"
                        continue
                    if role == "frontmatter_abstract":
                        content = rich_text(front.get("text", ""))
                        if front.get("italic"):
                            content = r"{\itshape " + content + "}"
                        if front.get("bold"):
                            content = r"{\bfseries " + content + "}"
                        bb = front.get("bbox", [0, 0, 1000, 0])
                        width = (bb[2] - bb[0]) * layout["paper_width"] / 1000
                        indent = max(
                            0, bb[0] * layout["paper_width"] / 1000 - layout["left"]
                        )
                        title += (
                            r"\noindent\hspace*{"
                            + str(round(indent, 2))
                            + r"bp}\begin{minipage}{"
                            + str(round(width, 2))
                            + r"bp}"
                            + content
                            + r"\end{minipage}\par\smallskip{}"
                        )
                        continue
                    fsize = front.get("font_size", layout["font_size"])
                    content = r"\\".join(
                        r"\quad ".join(rich_text(t) for t in row)
                        for row in front.get(
                            "frontmatter_rows", [[front.get("text", "")]]
                        )
                    )
                    face = (
                        r"\sffamily "
                        if "Biolinum" in front.get("font_name", "")
                        else ""
                    )
                    if any(
                        f in front.get("font_name", "")
                        for f in ("NimbusMon", "Courier", "CMTT")
                    ):
                        face = r"\ttfamily "
                    width = (
                        (front["bbox"][2] - front["bbox"][0])
                        * layout["paper_width"]
                        / 1000
                        if front.get("bbox")
                        else title_width
                    )
                    title += (
                        r"{\centering\begin{minipage}{"
                        + str(round(width + 2, 2))
                        + r"bp}\centering "
                        + face
                        + r"\fontsize{"
                        + str(fsize)
                        + "}{"
                        + str(round(fsize * 1.2, 2))
                        + r"}\selectfont "
                        + content
                        + r"\par\end{minipage}\par}"
                    )
                following = next(
                    (
                        x
                        for x in blocks[i + 1 :]
                        if not x.get("role", "").startswith("frontmatter")
                        and not x.get("merged_into")
                    ),
                    None,
                )
                gap = 10
                if following and following.get("bbox"):
                    gap = max(
                        0,
                        min(
                            45,
                            (following["bbox"][1] - previous["bbox"][3])
                            * layout["paper_height"]
                            / 1000,
                        ),
                    )
                title += r"\vspace{" + str(round(gap, 2)) + r"bp}"
                if (
                    b.get("bbox")
                    and abs(
                        b["bbox"][0] * layout["paper_width"] / 1000 - layout["left"]
                    )
                    < 10
                ):
                    title = title.replace(r"\centering", r"\raggedright")
                lines.append(
                    (r"\twocolumn[{" + title + "}]" if columns == 2 else title) + "\n"
                )
                for note in blocks:
                    if note["type"] == "footnote" and note["page"] == b["page"]:
                        lines.append(
                            r"\begingroup\renewcommand{\thefootnote}{}\footnotetext{"
                            + rich_text(note["text"])
                            + r"}\endgroup"
                            + "\n"
                        )
                continue
            command = ["section", "subsection", "subsubsection"][level - 1]
            star = "" if numbered else "*"
            if b.get("format") and b.get("font_size"):
                size = b["font_size"]
                face = r"\bfseries" if b.get("bold") else r"\normalfont"
                if b.get("italic"):
                    face += r"\itshape"
                lines.append(
                    "\\titleformat{\\"
                    + command
                    + r"}{\fontsize{"
                    + str(size)
                    + "}{"
                    + str(round(size * 1.2, 2))
                    + r"}\selectfont"
                    + face
                    + r"}{\the"
                    + command
                    + r".}{.5em}{}"
                    + "\n"
                )
            before = (
                str(b["space_before"]) + "bp"
                if "space_before" in b
                else ("1em" if level == 1 else ".8em")
            )
            after = (
                str(b["space_after"]) + "bp"
                if "space_after" in b
                else (".5em" if level == 1 else ".4em")
            )
            lines.append(
                "\\titlespacing*{\\"
                + command
                + "}{0pt}{"
                + before
                + "}{"
                + after
                + "}\n"
            )
            if numbered:
                numbers = [int(n) for n in numbered[1].rstrip(".").split(".")]
                for depth, num in enumerate(numbers[:3]):
                    counter = ["section", "subsection", "subsubsection"][depth]
                    lines.append(
                        r"\setcounter{"
                        + counter
                        + "}{"
                        + str(num - 1 if depth == level - 1 else num)
                        + "}\n"
                    )
            if text.lower() == "abstract":
                text = r"CENTER_ABSTRACT"
            lines.append(
                "\\"
                + command
                + star
                + "{"
                + (
                    r"\makebox[\linewidth][c]{Abstract}"
                    if text == "CENTER_ABSTRACT"
                    else rich_text(text)
                )
                + "}\n"
            )
        elif typ == "footnote":
            if blocks[0]["type"] == "heading" and b["page"] == blocks[0]["page"]:
                continue
            lines.append(
                r"\begingroup\renewcommand{\thefootnote}{}\footnotetext{"
                + rich_text(b["text"])
                + r"}\endgroup"
                + "\n"
            )
        elif typ == "equation":
            if b.get("equation_rows"):
                for row in b["equation_rows"]:
                    lines.append(
                        r"\begin{equation}\fitdisplay{"
                        + safe_math(row["latex"])
                        + r"}\tag{"
                        + row["number"]
                        + r"}\end{equation}"
                        + "\n"
                    )
                continue
            value = safe_math(b["latex"])
            if b.get("number") and r"\tag{" not in value:
                value += r"\tag{" + b["number"] + "}"
            env = "equation" if r"\tag{" in value else "equation*"
            tags = "".join(re.findall(r"\\tag\{[^{}]*\}", value))
            value = re.sub(r"\\tag\{[^{}]*\}", "", value)
            lines.append(
                "\\begin{"
                + env
                + "}\n"
                + r"\fitdisplay{"
                + value
                + "}"
                + tags
                + "\n\\end{"
                + env
                + "}\n"
            )
        elif typ == "table" and b.get("latex"):
            env = "table*" if b.get("wide") and columns == 2 else "table"
            lines.append("\\begin{" + env + "}[tbp]\n\\centering\n")
            if b.get("caption"):
                lines.append(r"\caption*{" + rich_text(b["caption"]) + "}\n")
            lines.append(
                r"\resizebox{\linewidth}{!}{" + b["latex"] + "}\n\\end{" + env + "}\n"
            )
        elif "asset" in b:
            if layout.get("preserve_source_breaks") and b.get("source_bbox"):
                asset = source_asset(b)
                following = next(
                    (
                        x
                        for x in blocks[i + 1 :]
                        if not x.get("merged_into")
                        and x["page"] == b["page"]
                        and (b.get("wide") or x.get("column") == b.get("column"))
                    ),
                    None,
                )
                gap = asset_gap(b, following)
                if b.get("wide") and columns == 2 and not body_started:
                    lines.append(
                        r"\twocolumn[{"
                        + asset
                        + r"\vspace{"
                        + str(round(gap, 2))
                        + r"bp}}]"
                        + "\n"
                    )
                elif b.get("wide") and columns == 2:
                    # Mid-page spanning material uses a full-width strip without
                    # entering the deferred double-column float queue.
                    lines.append(r"\begin{strip}" + asset + r"\end{strip}" + "\n")
                else:
                    lines.append(
                        asset + r"\vspace{" + str(round(gap, 2)) + r"bp}" + "\n"
                    )
                continue
            if typ in ("figure_crop", "image_crop", "table_crop"):
                env = "table" if typ == "table_crop" else "figure"
                if b.get("wide") and columns == 2:
                    env += "*"
                lines.append(
                    "\\begin{"
                    + env
                    + "}[tbp]\n\\centering\n"
                    + r"\includegraphics[width=\linewidth,height=.78\textheight,keepaspectratio]{"
                    + b["asset"]
                    + "}\n"
                )
                if b.get("caption"):
                    lines.append(r"\caption*{" + rich_text(b["caption"]) + "}\n")
                lines.append("\\end{" + env + "}\n")
            else:
                lines.append(
                    "% UNRESOLVED: recognition required; this asset is not editable math.\n"
                    + r"\asset{1}{"
                    + b["asset"]
                    + "}\n"
                )
        else:
            body_started = True
            if b.get("space_before"):
                lines.append(r"\vspace{" + str(b["space_before"]) + r"bp}" + "\n")
            value = b.get("tex") or rich_text(b.get("text", ""))
            if b.get("role") == "numbered_list":
                parts = re.split(r"(?:^|(?<=\s))(\d+)\.\s+", value)
                if len(parts) >= 5 and not parts[0].strip():
                    items = "\n".join(
                        r"\item " + parts[j + 1].strip()
                        for j in range(1, len(parts) - 1, 2)
                    )
                    lines.append(
                        r"\begin{enumerate}[leftmargin=1.7em,label=\arabic*.,labelsep=.4em,itemsep=0pt,topsep=0pt,parsep=0pt]"
                        + "\n"
                        + items
                        + "\n"
                        + r"\end{enumerate}"
                        + "\n"
                    )
                    continue
            if b.get("role") == "bibliography":
                # PDF paragraph segmentation often merges a whole reference
                # column. Restore entry boundaries without touching math text.
                entries = re.split(r"(?=\$\[\d+\]\$)", value)
                size = b.get("font_size", 9)
                items = []
                for entry in entries:
                    if not entry.strip():
                        continue
                    items.append(
                        r"\par\hangindent=1.5em\hangafter=1\noindent " + entry.strip()
                    )
                lines.append(
                    r"\begingroup\fontsize{"
                    + str(size)
                    + "}{"
                    + str(round(size * 1.2, 2))
                    + r"}\selectfont\setlength{\parskip}{2bp}"
                    + "\n"
                    + "\n".join(items)
                    + r"\par\endgroup"
                    + "\n"
                )
                continue
            if b.get("italic"):
                value = r"{\itshape " + value + "}"
            if b.get("bold"):
                value = r"{\bfseries " + value + "}"
            if b.get("format") and not b.get("first_line_indent"):
                value = r"\noindent " + value
            if (
                b.get("format")
                and b.get("font_size")
                and abs(b["font_size"] - layout["font_size"]) > 0.5
            ):
                size = b["font_size"]
                value = (
                    r"{\fontsize{"
                    + str(size)
                    + "}{"
                    + str(round(size * 1.2, 2))
                    + r"}\selectfont "
                    + value
                    + r"\par}"
                )
            # A blank paragraph before display math inserts an empty text line.
            # Keep the display attached to its introducing paragraph.
            following = blocks[i + 1] if i + 1 < len(blocks) else None
            lines.append(
                value
                + ("\n" if following and following["type"] == "equation" else "\n\n")
            )
    lines.append("\\end{document}\n")
    text = "".join(lines)
    if layout.get("source_column_frames"):
        from .pageflow import frame_pages

        text = frame_pages(text, blocks, layout)
    destination.write_text(text, encoding="utf-8")
