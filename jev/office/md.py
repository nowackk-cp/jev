"""Rapor ve plan için küçük, güvenli markdown → HTML dönüştürücü (belirtim §7).

Her metin önce kaçışlanır: ham HTML, bağlantı ve resim üretilmez; öznitelik içine kullanıcı metni yazılmaz.
Desteklenenler: başlıklar, yatay çizgi, iç içe madde ve numaralı listeler, alıntı, ```kod blokları```,
`satır içi kod`, **kalın**, *eğik*, boru (|) tabloları ve paragraflar.
"""
from __future__ import annotations

import html
import re

_FENCE = re.compile(r"^\s*(`{3,}|~{3,})\s*([\w+#.-]*)\s*$")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_HR = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_ITEM = re.compile(r"^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$")
_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
_CODE_SPAN = re.compile(r"(`+)(.+?)\1")
# Yalnızca yıldızlı vurgu: raporlarda sık geçen __init__, _gizli_ gibi adlar biçimlenmesin.
# Kalının kapanışından sonra yıldız gelmez; böylece ***x*** → <strong><em>x</em></strong>.
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*(?!\*)")
_ITALIC = re.compile(r"(?<![*\w])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![*\w])")
_STRONG_TAG = re.compile(r"</?strong>")
_LINK = re.compile(r"!?\[([^\]\n]+)\]\(((?:[^()\s]|\([^()\s]*\))+)\)")
_TOKEN = re.compile(r"\x00(\d+)\x00")
MAX_DEPTH = 8  # iç içe alıntı sınırı (özyineleme taşmasın)


def to_html(md: str | None) -> str:
    return _render(md or "", 0)


def _render(md: str, depth: int) -> str:
    lines = md.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ").split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = _FENCE.match(line)
        if m:
            i = _code_block(lines, i, m, out)
            continue
        m = _HEADING.match(line)
        if m:
            level = min(6, len(m.group(1)) + 1)  # sayfanın kendi başlığı h1/h2; rapor başlıkları bir alta kayar
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            i += 1
            continue
        if _HR.match(line):
            out.append("<hr>")
            i += 1
            continue
        if _is_table_start(lines, i):
            i = _table(lines, i, out)
            continue
        if _ITEM.match(line):
            i = _list(lines, i, out)
            continue
        if _QUOTE.match(line):
            buf = []
            while i < n and _QUOTE.match(lines[i]):
                buf.append(_QUOTE.match(lines[i]).group(1))
                i += 1
            if depth >= MAX_DEPTH:
                inner = "<p>" + "\n".join(_inline(b.strip()) for b in buf) + "</p>"
            else:
                inner = _render("\n".join(buf), depth + 1)
            out.append("<blockquote>" + inner + "</blockquote>")
            continue
        buf = []
        while i < n and lines[i].strip() and not _starts_block(lines, i):
            buf.append(lines[i].strip())
            i += 1
        out.append("<p>" + "\n".join(_inline(b) for b in buf) + "</p>")
    return "\n".join(out)


def _starts_block(lines: list[str], i: int) -> bool:
    line = lines[i]
    return bool(_FENCE.match(line) or _HEADING.match(line) or _HR.match(line) or _ITEM.match(line)
                or _QUOTE.match(line) or _is_table_start(lines, i))


def _code_block(lines: list[str], i: int, m: re.Match, out: list[str]) -> int:
    fence = m.group(1)
    lang = m.group(2)
    body: list[str] = []
    i += 1
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith(fence[0] * len(fence)) and not s.strip(fence[0]):
            i += 1
            break
        body.append(lines[i])
        i += 1
    # girintiyi ortak kısım kadar azalt (liste altındaki bloklar için)
    pad = min((len(b) - len(b.lstrip(" ")) for b in body if b.strip()), default=0)
    text = "\n".join(b[pad:] for b in body)
    label = f'<span class="md-lang">{html.escape(lang)}</span>' if lang else ""
    out.append(f"<pre>{label}<code>{html.escape(text)}</code></pre>")
    return i


def _cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, cur, tick = [], [], False
    j = 0
    while j < len(s):
        c = s[j]
        if c == "\\" and j + 1 < len(s) and s[j + 1] == "|":
            cur.append("|")
            j += 2
            continue
        if c == "`":
            tick = not tick
        if c == "|" and not tick:
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
        j += 1
    cells.append("".join(cur).strip())
    return cells


def _is_table_start(lines: list[str], i: int) -> bool:
    if not ("|" in lines[i] and i + 1 < len(lines) and "-" in lines[i + 1] and _TABLE_SEP.match(lines[i + 1])):
        return False
    if "|" not in lines[i + 1] + lines[i].strip()[:1]:
        return False
    return len(_cells(lines[i])) == len(_cells(lines[i + 1]))  # GFM: başlık ve ayraç hücre sayısı eşit


def _table(lines: list[str], i: int, out: list[str]) -> int:
    head = _cells(lines[i])
    aligns = []
    for c in _cells(lines[i + 1]):
        c = c.strip()
        aligns.append("c" if c.startswith(":") and c.endswith(":") else "r" if c.endswith(":") else "")
    i += 2
    rows = []
    while i < len(lines) and lines[i].strip() and "|" in lines[i]:
        rows.append(_cells(lines[i]))
        i += 1
    width = len(head)

    def cell(tag: str, text: str, k: int) -> str:
        al = aligns[k] if k < len(aligns) else ""
        cls = f' class="al-{al}"' if al else ""
        return f"<{tag}{cls}>{_inline(text)}</{tag}>"

    parts = ['<div class="md-table"><table><thead><tr>']
    parts += [cell("th", h, k) for k, h in enumerate(head)]
    parts.append("</tr></thead><tbody>")
    for r in rows:
        r = (r + [""] * width)[:width]
        parts.append("<tr>" + "".join(cell("td", c, k) for k, c in enumerate(r)) + "</tr>")
    parts.append("</tbody></table></div>")
    out.append("".join(parts))
    return i


def _list(lines: list[str], i: int, out: list[str]) -> int:
    items: list[list] = []  # [girinti, etiket, metin]
    n = len(lines)
    while i < n:
        line = lines[i]
        m = _ITEM.match(line)
        if m:
            tag = "ol" if m.group(2)[0].isdigit() else "ul"
            items.append([len(m.group(1)), tag, m.group(3).strip()])
            i += 1
            continue
        if not line.strip():
            j = i + 1
            while j < n and not lines[j].strip():
                j += 1
            if j < n and _ITEM.match(lines[j]):
                i = j
                continue
            break
        if items and line.startswith("  ") and not _FENCE.match(line):
            items[-1][2] += " " + line.strip()  # önceki maddenin devamı
            i += 1
            continue
        break
    html_parts: list[str] = []
    stack: list[tuple[int, str]] = []
    for indent, tag, text in items:
        while stack and indent < stack[-1][0]:
            html_parts.append(f"</li></{stack.pop()[1]}>")
        if stack and indent == stack[-1][0]:
            if tag != stack[-1][1]:
                html_parts.append(f"</li></{stack.pop()[1]}><{tag}>")
                stack.append((indent, tag))
            else:
                html_parts.append("</li>")
        else:
            html_parts.append(f"<{tag}>")
            stack.append((indent, tag))
        html_parts.append("<li>" + _inline(text))
    while stack:
        html_parts.append(f"</li></{stack.pop()[1]}>")
    out.append("".join(html_parts))
    return i


def _inline(text: str) -> str:
    """Satır içi biçim: önce kod parçaları ayrılır (içleri biçimlenmez), kalan her şey kaçışlanıp biçimlenir."""
    parts: list[str] = []
    pos = 0
    for m in _CODE_SPAN.finditer(text):
        parts.append(_format(text[pos:m.start()]))
        parts.append("<code>" + html.escape(m.group(2).strip()) + "</code>")
        pos = m.end()
    parts.append(_format(text[pos:]))
    return "".join(parts)


def _format(raw: str) -> str:
    s = html.escape(raw.replace("\x00", ""))
    links: list[str] = []

    def keep(m: re.Match) -> str:  # bağlantılar tıklanmaz; metin + soluk adres olarak gösterilir
        links.append(f'{m.group(1)} <span class="md-url">({m.group(2)})</span>')
        return f"\x00{len(links) - 1}\x00"

    s = _LINK.sub(keep, s)
    s = _BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", s)
    s = _ITALIC.sub(_em, s)
    return _TOKEN.sub(lambda m: links[int(m.group(1))], s)


def _em(m: re.Match) -> str:
    """Kalınla kesişen eğik biçimlenmez (**a *b** c*): etiketler her zaman iç içe kapanır."""
    depth = 0
    for tag in _STRONG_TAG.findall(m.group(1)):
        depth += -1 if tag[1] == "/" else 1
        if depth < 0:
            return m.group(0)
    return f"<em>{m.group(1)}</em>" if depth == 0 else m.group(0)
