"""Recognized HTML cells → editable LaTeX, including row/column spans."""

from html.parser import HTMLParser

from .latex import rich_text


class Cells(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None
        self.span = (1, 1)
        self.invalid = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        if tag in ("td", "th"):
            self.cell = []
            attrs = dict(attrs)
            try:
                self.span = (int(attrs.get("colspan", 1)), int(attrs.get("rowspan", 1)))
                if not all(1 <= x <= 100 for x in self.span):
                    self.invalid = True
            except ValueError:
                self.invalid = True
                self.span = (1, 1)
        if tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_data(self, value):
        if self.cell is not None:
            self.cell.append(value)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            if self.row is not None:
                self.row.append(("".join(self.cell), *self.span))
            self.cell = None
        if tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def table_tex(html):
    p = Cells()
    p.feed(html)
    if p.invalid or not p.rows or not any(p.rows):
        return None
    output = []
    occupied = {}
    n = 0
    for row in p.rows:
        cells = []
        col = 0
        new = {}
        for value, cs, rs in row:
            while occupied.get(col, 0) > 0:
                cells.append((col, 1, ""))
                col += 1
            if any(occupied.get(c, 0) > 0 for c in range(col, col + cs)):
                return None
            tex = rich_text(value)
            if rs > 1:
                tex = r"\multirow{" + str(rs) + r"}{*}{" + tex + "}"
                for c in range(col, col + cs):
                    new[c] = rs - 1
            if cs > 1:
                tex = r"\multicolumn{" + str(cs) + r"}{l}{" + tex + "}"
            cells.append((col, cs, tex))
            col += cs
        n = max(n, col, max(occupied, default=-1) + 1)
        output.append(cells)
        occupied = {c: r - 1 for c, r in occupied.items() if r > 1}
        occupied.update(new)
    lines = []
    for row in output:
        used = sum(cs for _, cs, _ in row)
        lines.append(
            " & ".join([tex for _, _, tex in row] + [""] * (n - used)) + r" \\"
        )
    return (
        "\\begin{tabular}{"
        + "l" * n
        + "}\n\\toprule\n"
        + "\n".join(lines)
        + "\n\\bottomrule\n\\end{tabular}"
    )
