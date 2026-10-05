"""Kota: kalıp eşleme, sıfırlanma zamanı ayrıştırma, soğuma defteri, hata sınıflandırma."""
import datetime as dt
import json
import unittest

import _ortak
from jev.adapters.base import AgentAdapter
from jev.quota import QuotaBook, epoch_reset, log_unknown_error, matches_any, parse_reset
from jev.util import iso, now, parse_iso

UTC = dt.timezone.utc
REF = dt.datetime(2026, 3, 10, 9, 0, tzinfo=UTC)
CFG = _ortak.make_config()


def local_fields(t, ref):
    """parse_reset yerel saati ref'in yerel ofsetiyle yorumlar; karşılaştırma da aynı ofsetle yapılır."""
    lt = t.astimezone(ref.astimezone().tzinfo)
    return lt.year, lt.month, lt.day, lt.hour, lt.minute


class MatchTests(unittest.TestCase):
    def test_matches_any(self):
        self.assertTrue(matches_any("HTTP 429 Too Many Requests", [r"\b429\b"]))
        self.assertFalse(matches_any("port 4290", [r"\b429\b"]))
        self.assertTrue(matches_any("USAGE LIMIT reached", ["usage limit"]))
        self.assertFalse(matches_any("", ["usage limit"]))
        self.assertFalse(matches_any("her şey yolunda", []))

    def test_invalid_regex_falls_back_to_substring(self):
        self.assertTrue(matches_any("rate (limit hit", ["rate (limit"]))
        self.assertFalse(matches_any("başka", ["rate (limit"]))


class ParseResetTests(unittest.TestCase):
    def test_epoch(self):
        t = REF + dt.timedelta(hours=5)
        self.assertEqual(parse_reset(f"Claude AI usage limit reached|{int(t.timestamp())}", REF), t)
        far = REF + dt.timedelta(days=20)
        self.assertIsNone(parse_reset(f"usage limit reached|{int(far.timestamp())}", REF))

    def test_epoch_reset(self):
        """Claude'un rate_limit_event'indeki resetsAt (Unix saniyesi)."""
        t = REF + dt.timedelta(hours=3)
        self.assertEqual(epoch_reset(int(t.timestamp()), REF), t)
        self.assertEqual(epoch_reset(str(int(t.timestamp())), REF), t)
        for bad in (None, "", "yarın", {"a": 1}, (REF + dt.timedelta(days=20)).timestamp(), -1, 10 ** 20):
            self.assertIsNone(epoch_reset(bad, REF), bad)

    def test_relative(self):
        self.assertEqual(parse_reset("Try again in 2 hours 30 minutes.", REF), REF + dt.timedelta(hours=2, minutes=30))
        self.assertEqual(parse_reset("rate limit, retry in 45 min", REF), REF + dt.timedelta(minutes=45))
        self.assertEqual(parse_reset("available in 1 day and 3 hours", REF), REF + dt.timedelta(hours=27))
        self.assertEqual(parse_reset("wait in 90s", REF), REF + dt.timedelta(seconds=90))

    def test_date_time(self):
        t = parse_reset("You've hit your usage limit. Try again at Feb 5th, 2027 10:15 PM.", REF)
        self.assertEqual(local_fields(t, REF), (2027, 2, 5, 22, 15))
        t = parse_reset("sıfırlanma Şub 3 09:30", dt.datetime(2026, 1, 10, 12, 0, tzinfo=UTC))
        self.assertEqual(local_fields(t, REF), (2026, 2, 3, 9, 30))

    def test_date_without_year_rolls_over(self):
        ref = dt.datetime(2026, 12, 30, 12, 0, tzinfo=UTC)
        t = parse_reset("Your limit resets Jan 3 at 9am", ref)
        self.assertEqual(local_fields(t, ref), (2027, 1, 3, 9, 0))

    def test_time_only(self):
        t = parse_reset("Rate limit reached. Resets at 3:30 pm", REF)
        self.assertEqual(local_fields(t, REF)[3:], (15, 30))
        self.assertTrue(dt.timedelta(0) < t - REF <= dt.timedelta(days=1))
        t = parse_reset("limit resets at 12am", REF)
        self.assertEqual(local_fields(t, REF)[3:], (0, 0))
        self.assertTrue(dt.timedelta(0) < t - REF <= dt.timedelta(days=1))
        self.assertEqual(local_fields(parse_reset("resets at 12pm", REF), REF)[3:], (12, 0))
        self.assertEqual(local_fields(parse_reset("try again at 10.45", REF), REF)[3:], (10, 45))

    def test_time_now_is_not_rolled_over_to_tomorrow(self):
        hhmm = REF.astimezone().strftime("%H:%M")
        self.assertEqual(parse_reset(f"resets at {hhmm}", REF), REF)
        stale_ref = REF + dt.timedelta(minutes=6)
        self.assertEqual(parse_reset(f"resets at {hhmm}", stale_ref), REF + dt.timedelta(days=1))

    def test_reset_message_logged_after_reset(self):
        # Real failure: 05:10 reset logged at 05:10:03 became a 24-hour cooldown.
        local_ref = REF.astimezone().replace(hour=5, minute=10, second=3)
        ref = local_ref.astimezone(UTC)
        self.assertEqual(parse_reset("You've hit your usage limit. Try again at 5:10 AM.", ref), ref)
        for delay in (dt.timedelta(minutes=4, seconds=57), dt.timedelta(minutes=4, seconds=58)):
            with self.subTest(delay=delay):
                logged_at = ref + delay
                expected = logged_at if delay.seconds <= 297 else ref.replace(second=0) + dt.timedelta(days=1)
                self.assertEqual(parse_reset("try again at 5:10 AM", logged_at), expected)

    def test_reset_just_before_midnight(self):
        ref = REF.astimezone().replace(hour=0, minute=1, second=3).astimezone(UTC)
        self.assertEqual(parse_reset("resets at 11:59 PM", ref), ref)

    def test_ambiguous_or_invalid(self):
        for text in ("resets 3", "resets at 25:00", "resets Feb 30 10:00", "usage limit reached", "", None,
                     "until 99:99"):
            self.assertIsNone(parse_reset(text, REF), text)


class QuotaBookTests(unittest.TestCase):
    def setUp(self):
        self.book = QuotaBook(_ortak.temp_dir("kota") / "kota.json")

    def test_mark_until_clear(self):
        u = now() + dt.timedelta(hours=1)
        self.book.mark("sol", u, "x" * 500)
        self.assertEqual(self.book.until("sol"), parse_iso(iso(u)))
        self.assertFalse(self.book.available("sol"))
        self.assertTrue(self.book.available("sonnet"))
        self.assertTrue(self.book.available("sol", at=u + dt.timedelta(seconds=1)))
        self.assertEqual(len(self.book.all()["sol"]["reason"]), 300)
        self.book.mark("luna", now() - dt.timedelta(minutes=1))
        self.assertNotIn("luna", self.book.all())
        self.assertIsNone(self.book.until("luna"))
        self.book.clear("sol")
        self.assertTrue(self.book.available("sol"))
        self.book.mark("sol", u)
        self.book.clear()
        self.assertEqual(self.book.all(), {})

    def test_corrupt_file_means_no_cooldown(self):
        self.book.path.write_text(json.dumps([1, 2]), encoding="utf-8")
        self.assertTrue(self.book.available("sol"))
        self.assertEqual(self.book.all(), {})

    def test_default_path_and_unknown_error_log(self):
        self.assertEqual(QuotaBook().path, _ortak.HOME / "kota.json")
        log_unknown_error("sol", "  beklenmeyen hata: ığüşöç  ")
        text = (_ortak.HOME / "bilinmeyen-hatalar.log").read_text(encoding="utf-8")
        self.assertIn(" sol\nbeklenmeyen hata: ığüşöç\n", text)


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self.a = AgentAdapter(CFG)

    def test_quota(self):
        kind, reset = self.a.classify("Error: You've hit your usage limit. Try again in 2 hours.")
        self.assertEqual(kind, "quota")
        self.assertAlmostEqual((reset - now()).total_seconds(), 7200, delta=30)
        self.assertEqual(self.a.classify("429 Too Many Requests"), ("quota", None))
        self.assertEqual(self.a.classify("429; please log in")[0], "quota")  # kota önce gelir
        # Claude CLI'ın gerçek metni (1 Ekim 2026): "rate_limit" alt çizgili, "hit your session limit" araya kelime alır
        kind, reset = self.a.classify("rate_limit\nYou've hit your session limit · resets 1:40pm (Europe/Istanbul)")
        self.assertEqual(kind, "quota")
        self.assertEqual(reset.astimezone().strftime("%H:%M"), "13:40")
        for text in ("Rate-limited", "rate limit exceeded", "You have hit your weekly limit"):
            self.assertEqual(self.a.classify(text)[0], "quota", text)

    def test_auth_transient_crash(self):
        for text in ("Invalid API key · Please run /login", "Unauthorized (401)", "Not logged in. Run codex login",
                     "OAuth token has expired"):
            self.assertEqual(self.a.classify(text), ("auth", None), text)
        for text in ("stream error: 503 Service Unavailable", "Overloaded", "request timed out", "ECONNRESET"):
            self.assertEqual(self.a.classify(text), ("transient", None), text)
        self.assertEqual(self.a.classify("Traceback (most recent call last): KeyError"), ("crash", None))


if __name__ == "__main__":
    unittest.main()
