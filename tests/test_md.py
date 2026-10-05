"""Güvenli markdown → HTML: her metin kaçışlanır; bağlantı, resim ve ham HTML üretilmez, etiketler hep iç içe kapanır."""
import random
import re
import unittest
from html.parser import HTMLParser

import _ortak  # noqa: F401
from jev.office.md import MAX_DEPTH, to_html

TAGS = {"p", "h2", "h3", "h4", "h5", "h6", "hr", "ul", "ol", "li", "blockquote", "pre", "code", "strong", "em",
        "table", "thead", "tbody", "tr", "th", "td", "div", "span"}
CLASSES = {"md-url", "md-lang", "md-table", "al-c", "al-r"}


class _Checker(HTMLParser):
    """Yalnızca izinli etiketler ve class değerleri; her etiket doğru sırayla kapanmalı."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.problems = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in TAGS:
            self.problems.append(f"etiket <{tag}>")
        for name, value in attrs:
            if name != "class" or value not in CLASSES:
                self.problems.append(f"öznitelik {name}={value!r}")
        if tag != "hr":
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.problems.append(f"<{tag}/>")

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.problems.append(f"yanlış kapanış </{tag}>, açık: {self.stack}")

    def handle_comment(self, data):
        self.problems.append("yorum")

    def handle_decl(self, decl):
        self.problems.append("bildirim")

    def handle_pi(self, data):
        self.problems.append("işlem talimatı")

    def unknown_decl(self, data):
        self.problems.append("bilinmeyen bildirim")


def assert_safe(tc: unittest.TestCase, out: str) -> None:
    checker = _Checker()
    checker.feed(out)
    checker.close()
    tc.assertEqual(checker.problems, [], out)
    tc.assertEqual(checker.stack, [], out)
    rest = re.sub(r' class="[a-z-]+"', "", out)
    tc.assertNotIn('"', rest, out)  # tırnak yalnızca bizim class özniteliklerimizde
    tc.assertNotIn("'", rest, out)


class SafetyTests(unittest.TestCase):
    def test_raw_html_is_escaped(self):
        self.assertEqual(to_html("<script>alert('x')</script>"),
                         "<p>&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;</p>")
        self.assertEqual(to_html("&lt;zaten&gt; & <b>"), "<p>&amp;lt;zaten&amp;gt; &amp; &lt;b&gt;</p>")
        self.assertEqual(to_html("# <style>body{}</style>"), "<h2>&lt;style&gt;body{}&lt;/style&gt;</h2>")

    def test_links_and_images_are_not_clickable(self):
        self.assertEqual(to_html("[tıkla](javascript:alert(1))"),
                         '<p>tıkla <span class="md-url">(javascript:alert(1))</span></p>')
        self.assertEqual(to_html("![logo](http://ornek.invalid/a.png)"),
                         '<p>logo <span class="md-url">(http://ornek.invalid/a.png)</span></p>')
        out = to_html('[a](http://x"onclick=alert(1)) [<img src=x onerror=alert(1)>](http://y)')
        self.assertEqual(out, '<p>a <span class="md-url">(http://x&quot;onclick=alert(1))</span> '
                              '&lt;img src=x onerror=alert(1)&gt; <span class="md-url">(http://y)</span></p>')
        assert_safe(self, out)

    def test_placeholder_tokens_cannot_be_forged(self):
        self.assertEqual(to_html("\x000\x00 ve [a](b)"), '<p>0 ve a <span class="md-url">(b)</span></p>')
        self.assertEqual(to_html("\x005\x00"), "<p>5</p>")

    def test_hostile_inputs(self):
        for doc in ('```js" onload="x\nkod\n```', "> <iframe src=x>", "- <svg onload=alert(1)>",
                    "| <b> | x |\n|---|---|\n| <i>y</i> | `<u>` |", "**<b>kalın</b>**", '<a href="http://kötü">tık</a>',
                    "<!-- yorum -->", "<![CDATA[x]]>", "<?xml version='1.0'?>", "\"'`", "|", "\\|", "`", "#", "1.",
                    "-", ">", "~~~~", "\r\r\r", "x" * 20000):
            with self.subTest(doc=doc[:40]):
                assert_safe(self, to_html(doc))


class BlockTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(to_html(None), "")
        self.assertEqual(to_html(""), "")
        self.assertEqual(to_html("   \n\t\n"), "")

    def test_paragraphs(self):
        self.assertEqual(to_html("bir\r\niki\n\n\nüç"), "<p>bir\niki</p>\n<p>üç</p>")
        self.assertEqual(to_html("metin\n# başlık\n---"), "<p>metin</p>\n<h2>başlık</h2>\n<hr>")
        s = "Görev çıktısı: ğüşiöç ĞÜŞİÖÇ"
        self.assertEqual(to_html(s), f"<p>{s}</p>")

    def test_headings(self):
        self.assertEqual(to_html("# Rapor"), "<h2>Rapor</h2>")
        self.assertEqual(to_html("## Özet ##"), "<h3>Özet</h3>")
        self.assertEqual(to_html("##### Beş"), "<h6>Beş</h6>")
        self.assertEqual(to_html("###### Altı"), "<h6>Altı</h6>")
        self.assertEqual(to_html("####### yedi"), "<p>####### yedi</p>")
        self.assertEqual(to_html("#etiket"), "<p>#etiket</p>")
        self.assertEqual(to_html("# C# ve **kalın** `kod`"), "<h2>C# ve <strong>kalın</strong> <code>kod</code></h2>")

    def test_rules(self):
        for s in ("---", "* * *", "___", "  - - -"):
            self.assertEqual(to_html(s), "<hr>", s)
        self.assertEqual(to_html("--"), "<p>--</p>")

    def test_code_blocks(self):
        self.assertEqual(to_html("```python\n<b>x</b> **kalın değil**\n\n  girinti\n```"),
                         '<pre><span class="md-lang">python</span><code>&lt;b&gt;x&lt;/b&gt; **kalın değil**\n\n'
                         '  girinti</code></pre>')
        self.assertEqual(to_html("````\n```\nkod\n````\nsonra"), "<pre><code>```\nkod</code></pre>\n<p>sonra</p>")
        self.assertEqual(to_html("~~~\na\n```\n~~~"), "<pre><code>a\n```</code></pre>")
        self.assertEqual(to_html("```\nkapanmadı"), "<pre><code>kapanmadı</code></pre>")
        self.assertEqual(to_html("- madde\n  ```\n  kod\n    iç\n  ```"),
                         "<ul><li>madde</li></ul>\n<pre><code>kod\n  iç</code></pre>")

    def test_lists(self):
        self.assertEqual(to_html("- bir\n  - iki\n    1. üç\n- dört"),
                         "<ul><li>bir<ul><li>iki<ol><li>üç</li></ol></li></ul></li><li>dört</li></ul>")
        self.assertEqual(to_html("- a\n1. b"), "<ul><li>a</li></ul><ol><li>b</li></ol>")
        self.assertEqual(to_html("* uzun madde\n  devamı\n\n+ ikinci\n\nparagraf"),
                         "<ul><li>uzun madde devamı</li><li>ikinci</li></ul>\n<p>paragraf</p>")
        self.assertEqual(to_html("1) bir\n2) **iki**"), "<ol><li>bir</li><li><strong>iki</strong></li></ol>")
        self.assertEqual(to_html("\t- sekme"), "<ul><li>sekme</li></ul>")
        for doc in ("- a\n    - b\n  - c", "  - a\n- b", "- a\n  1. b\n  - c"):  # düzensiz girintiler
            assert_safe(self, to_html(doc))

    def test_quotes(self):
        self.assertEqual(to_html("> **not:** dikkat\n> - madde\n\nsonra"),
                         "<blockquote><p><strong>not:</strong> dikkat</p>\n<ul><li>madde</li></ul></blockquote>\n"
                         "<p>sonra</p>")
        deep = to_html(">" * 50 + " derin")
        self.assertEqual(deep.count("<blockquote>"), MAX_DEPTH + 1)
        self.assertIn("&gt;" * (50 - MAX_DEPTH - 1) + " derin", deep)
        assert_safe(self, deep)
        assert_safe(self, to_html(">" * 5000))  # özyineleme taşmaz

    def test_tables(self):
        md = ("| Ad | Puan | Not |\n|:---|---:|:-:|\n| Luna | 3 | `a|b` |\n| Sol \\| Sonnet | 4 |\n"
              "| x | y | z | fazla |\nsonra")
        self.assertEqual(to_html(md),
                         '<div class="md-table"><table><thead><tr><th>Ad</th><th class="al-r">Puan</th>'
                         '<th class="al-c">Not</th></tr></thead><tbody>'
                         '<tr><td>Luna</td><td class="al-r">3</td><td class="al-c"><code>a|b</code></td></tr>'
                         '<tr><td>Sol | Sonnet</td><td class="al-r">4</td><td class="al-c"></td></tr>'
                         '<tr><td>x</td><td class="al-r">y</td><td class="al-c">z</td></tr>'
                         '</tbody></table></div>\n<p>sonra</p>')
        self.assertEqual(to_html("| a | b |\n|---|"), "<p>| a | b |\n|---|</p>")  # hücre sayısı tutmuyor
        self.assertEqual(to_html("a | b\n---"), "<p>a | b</p>\n<hr>")


class InlineTests(unittest.TestCase):
    def test_emphasis(self):
        self.assertEqual(to_html("**kalın** ve *eğik*."), "<p><strong>kalın</strong> ve <em>eğik</em>.</p>")
        self.assertEqual(to_html("__init__, _gizli_ ve dosya_adi_2.py"), "<p>__init__, _gizli_ ve dosya_adi_2.py</p>")
        self.assertEqual(to_html("2 * 3 * 4 ve a*b*c"), "<p>2 * 3 * 4 ve a*b*c</p>")
        self.assertEqual(to_html("***ikisi***"), "<p><strong><em>ikisi</em></strong></p>")
        self.assertEqual(to_html("*a **b** c*"), "<p><em>a <strong>b</strong> c</em></p>")

    def test_crossing_emphasis_stays_nested(self):
        self.assertEqual(to_html("**a *b** c*"), "<p><strong>a *b</strong> c*</p>")
        for s in ("*a **b* c**", "***a** b*", "**a *b** *c*", "*a* **b *c** d*"):
            assert_safe(self, to_html(s))

    def test_code_spans(self):
        self.assertEqual(to_html("`**x** <b>` ve ``a`b``"), "<p><code>**x** &lt;b&gt;</code> ve <code>a`b</code></p>")
        self.assertEqual(to_html("*a `kod` b*"), "<p>*a <code>kod</code> b*</p>")  # vurgu kodu aşmaz

    def test_link_inside_emphasis(self):
        self.assertEqual(to_html("**[a](b)**"), '<p><strong>a <span class="md-url">(b)</span></strong></p>')
        self.assertEqual(to_html("[**a**](u)"), '<p>**a** <span class="md-url">(u)</span></p>')


class FuzzTests(unittest.TestCase):
    LINES = ["# b", "## *e* **k**", "- a", "  - b", "    1. c", "1) d", "> q", ">> qq", "> - q", "```", "```py", "~~~",
             "| a | b |", "|---|:-:|", "| 1 | `2|3` |", "---", "* * *", "", "", "**x", "*y*", "`z", "[l](u)",
             "![i](u)", "<b>", "<script>x</script>", "\t- t", "düz metin", "***ü***", "*a **b** c*", "**a *b** c*",
             "\\|", "\x00", "&amp;", "\"'", "|", "a | b", "  devam"]
    ATOMS = ["*", "**", "***", "a", "ğ", " ", "`", "``", "[", "]", "(", ")", "<", ">", "&", "_", "!", "\x00", "\"", "'"]

    def test_random_documents(self):
        rng = random.Random(20260927)
        for _ in range(400):
            doc = "\n".join(rng.choice(self.LINES) for _ in range(rng.randint(1, 25)))
            with self.subTest(doc=doc):
                assert_safe(self, to_html(doc))

    def test_random_inline(self):
        rng = random.Random(27)
        for _ in range(600):
            line = "".join(rng.choice(self.ATOMS) for _ in range(rng.randint(1, 30)))
            with self.subTest(line=line):
                assert_safe(self, to_html("x " + line))  # "x " → her zaman paragraf


if __name__ == "__main__":
    unittest.main()
