"""Ayarlar: varsayılanlar, kullanıcı jev.toml birleştirme, doğrulama hataları (rol kuralları dahil)."""
import copy
import tomllib
import unittest
from pathlib import Path

import _ortak
from jev.config import DEFAULT_PATH, Config, ConfigError, deep_merge, load_config

USER_TOML = _ortak.HOME / "jev.toml"


def default_raw() -> dict:
    return tomllib.loads(DEFAULT_PATH.read_text(encoding="utf-8"))


class DefaultTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()

    def test_agents_and_roles(self):
        c = self.cfg
        self.assertEqual(list(c.agents), ["opus", "sol", "sonnet", "luna"])
        self.assertEqual(c.workers(), ["opus", "sol", "sonnet", "luna"])  # Opus yalnızca L görevlerde işçi
        self.assertEqual([c.slots(a) for a in c.agents], [2, 2, 2, 2])
        self.assertEqual((c.planner, c.reviewer), ("opus", "sol"))
        self.assertEqual((c.brain, c.fallback_brain), ("opus", ("sol", "xhigh")))
        # yedek denetçi: yalnızca büyük işte (bilinmeyen seviye büyük sayılır)
        self.assertEqual([c.backup_review(x) for x in ("mini", "kucuk", "orta", "buyuk", None)],
                         [None, None, None, "opus", "opus"])
        self.assertEqual(c.backup_reviewer, "opus")
        self.assertEqual(c.effort("opus", "denetim"), "high")
        self.assertTrue(c.has_role("opus", "planlayici"))
        self.assertTrue(c.has_role("opus", "eskalasyon"))
        self.assertTrue(c.has_role("opus", "isci") and c.has_role("opus", "eskalasyon"))
        self.assertTrue(c.has_role("sol", "denetci"))
        self.assertFalse(c.has_role("sonnet", "denetci"))
        self.assertEqual(c.get("yonlendirme", "eskalasyon"), ["opus"])
        self.assertEqual(c.get("yonlendirme", "L"), ["opus", "sol", "sonnet"])
        self.assertEqual(c.get("yonlendirme", "tur", "research"), ["sonnet", "sol"])
        self.assertFalse(c.has_role("yok", "isci"))
        self.assertEqual((c.provider("sol"), c.provider("sonnet")), ("codex", "claude"))
        self.assertEqual(c.label("opus"), "Claude Opus 5.5")
        self.assertEqual(c.agent("sol")["ad"], "sol")
        with self.assertRaises(ConfigError) as cm:
            c.agent("yok")
        self.assertEqual(str(cm.exception), "Bilinmeyen ajan: yok")

    def test_effort_fallbacks(self):
        c = self.cfg
        self.assertEqual(c.effort("sol", "L"), "xhigh")
        self.assertEqual(c.effort("sol", "test"), "low")
        self.assertEqual(c.effort("opus", "tanımsız"), "high")  # varsayilan
        self.assertEqual(c.effort("luna", "tanımsız"), "medium")  # M
        self.assertEqual((c.effort("opus", "plan"), c.effort("sol", "denetim")), ("xhigh", "high"))
        raw = default_raw()
        raw["ajanlar"]["luna"]["efor"] = {"S": "low"}
        self.assertEqual(Config(raw).effort("luna", "tanımsız"), "high")  # hiçbiri yok

    def test_limits_and_lookup(self):
        c = self.cfg
        self.assertEqual(c.limit("en_fazla_gorev"), 40)
        self.assertEqual(c.timeout_s("isci_M"), 2400)
        self.assertEqual(c.timeout_s("test"), 120)
        self.assertEqual(c.get("arayuz", "port"), 8765)
        self.assertEqual(c.get("yok", "hic", default=5), 5)
        self.assertEqual(c.get("paralel", "alt", default="d"), "d")
        self.assertEqual(c.project_root, Path.home() / "jev-projeler")
        self.assertFalse(c.get("guvenlik", "koruma"))
        self.assertIn("--strict-mcp-config", c.get("cli", "claude_ek_argumanlar"))
        self.assertNotIn("--bare", c.get("cli", "claude_ek_argumanlar"))


class UserFileTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(lambda: USER_TOML.unlink(missing_ok=True))

    def test_user_toml_is_merged(self):
        USER_TOML.write_text('proje_koku = "~/benim-projelerim"\n[arayuz]\nport = 9999\n'
                             '[ajanlar.sol]\nefor = { L = "high" }\n', encoding="utf-8-sig")
        c = load_config()
        self.assertEqual(c.get("arayuz", "port"), 9999)
        self.assertTrue(c.get("arayuz", "acik"))  # diğer anahtarlar korunur
        self.assertEqual((c.effort("sol", "L"), c.effort("sol", "S")), ("high", "medium"))
        self.assertEqual(c.project_root, Path.home() / "benim-projelerim")

    def test_precedence(self):
        USER_TOML.write_text("[arayuz]\nport = 9001\ntarayici_ac = false\n", encoding="utf-8")
        extra = _ortak.temp_dir("ayar") / "ek.toml"
        extra.write_text("[arayuz]\nport = 9002\n", encoding="utf-8")
        c = load_config(extra)
        self.assertEqual((c.get("arayuz", "port"), c.get("arayuz", "tarayici_ac")), (9002, False))
        c = load_config(extra, overrides={"arayuz": {"port": 9003}})
        self.assertEqual(c.get("arayuz", "port"), 9003)
        self.assertEqual(load_config(extra.with_name("yok.toml")).get("arayuz", "port"), 9001)

    def test_invalid_toml(self):
        USER_TOML.write_text("[arayuz\nport = ", encoding="utf-8")
        with self.assertRaises(ConfigError) as cm:
            load_config()
        self.assertTrue(str(cm.exception).startswith(f"{USER_TOML} okunamadı:"), str(cm.exception))

    def test_unknown_keys_in_user_file(self):
        USER_TOML.write_text('[arayz]\nport = 1\n[arayuz]\nprot = 2\n[ajanlar.sol]\nmodl = "x"\n'
                             'efor = { ozel = "high" }\n[yonlendirme.tur]\nbelge = ["luna"]\n', encoding="utf-8")
        with self.assertRaises(ConfigError) as cm:
            load_config()
        msg = str(cm.exception)
        self.assertTrue(msg.startswith(f"{USER_TOML}: bilinmeyen ayar: arayz, arayuz.prot, ajanlar.sol.modl ("), msg)
        # görev türleri ve efor adları serbest: yazım hatası sayılmaz
        USER_TOML.write_text('[ajanlar.sol]\nefor = { ozel = "high" }\n[yonlendirme.tur]\nbelge = ["luna"]\n',
                             encoding="utf-8")
        c = load_config()
        self.assertEqual((c.effort("sol", "ozel"), c.get("yonlendirme", "tur", "belge")), ("high", ["luna"]))

    def test_invalid_values_in_user_file(self):
        USER_TOML.write_text('[jev]\ndenetci = "luna"\n', encoding="utf-8")
        with self.assertRaises(ConfigError) as cm:
            load_config()
        self.assertIn("jev.denetci: 'luna' ajanının 'denetci' rolü yok", str(cm.exception))


class ValidationTests(unittest.TestCase):
    def errors(self, **overrides) -> str:
        with self.assertRaises(ConfigError) as cm:
            _ortak.make_config(**overrides)
        msg = str(cm.exception)
        self.assertTrue(msg.startswith("Ayar hataları:\n- "), msg)
        return msg

    def test_agent_fields(self):
        msg = self.errors(ajanlar={"zeta": {"saglayici": "codex", "model": "m"},
                                   "sol": {"saglayici": "openai", "roller": ["isci", "patron"],
                                           "efor": {"L": "ultra"}},
                                   "luna": {"model": ""}})
        for expected in ("ajanlar.zeta: bilinen ajanlar opus, sol, sonnet, luna",
                         "ajanlar.sol.saglayici: 'codex' ya da 'claude' olmalı",
                         "ajanlar.sol.roller: geçersiz rol 'patron'",
                         "ajanlar.sol.efor.L: geçersiz efor 'ultra' (ultra kullanılmaz)",
                         "ajanlar.luna.model boş olamaz"):
            self.assertIn("- " + expected, msg)

    def test_parallel(self):
        for bad in (0, -1, "2", 1.5, True):
            self.assertIn("- paralel: en az 1 olan bir tam sayı olmalı", self.errors(paralel=bad))
        for ok in (1, 2, 4):
            self.assertEqual(_ortak.make_config(paralel=ok).get("paralel"), ok)

    def test_fixed_roles(self):
        raw = default_raw()
        del raw["ajanlar"]["sol"]
        with self.assertRaises(ConfigError) as cm:
            Config(raw)
        self.assertIn("jev.denetci tanımlı bir ajan olmalı (gelen: 'sol')", str(cm.exception))
        self.assertIn("jev.planlayici: 'sonnet' ajanının 'planlayici' rolü yok",
                      self.errors(jev={"planlayici": "sonnet"}))
        self.assertIn("jev.denetci: 'luna' ajanının 'denetci' rolü yok", self.errors(jev={"denetci": "luna"}))
        # kaldırılan ajan ve rol geri gelmez
        self.assertIn("ajanlar.astra: bilinen ajanlar opus, sol, sonnet, luna",
                      self.errors(ajanlar={"astra": {"saglayici": "codex", "model": "gpt-6-astra"}}))
        self.assertIn("ajanlar.opus.roller: geçersiz rol 'parcalayici'",
                      self.errors(ajanlar={"opus": {"roller": ["planlayici", "beyin", "parcalayici"]}}))
        # denetçiyi değiştirmek: ajana denetci rolü verilir
        c = _ortak.make_config(ajanlar={"sonnet": {"roller": ["isci", "denetci"]}}, jev={"denetci": "sonnet"})
        self.assertEqual(c.reviewer, "sonnet")

    def test_brain_and_routing(self):
        self.assertIn("jev.beyin tanımlı bir ajan olmalı (gelen: 'gpt')", self.errors(jev={"beyin": "gpt"}))
        self.assertIn("jev.yedek_beyin.ajan tanımlı bir ajan olmalı (gelen: 'zz')",
                      self.errors(jev={"yedek_beyin": {"ajan": "zz"}}))
        msg = self.errors(yonlendirme={"S": ["luna", "yok"], "M": ["opus"], "eskalasyon": ["kim"]},
                          ajanlar={"opus": {"roller": ["planlayici", "beyin", "eskalasyon"]}})
        self.assertIn("yonlendirme.S: bilinmeyen ajan 'yok'", msg)
        self.assertIn("yonlendirme.M: 'opus' ajanının 'isci' rolü yok", msg)
        self.assertIn("yonlendirme.eskalasyon: bilinmeyen ajan 'kim'", msg)

    def test_escalation_roles(self):
        # işçi rolü alınan Opus'un L listesinde kalması hata; eskalasyon rolü alınıp listede bırakılması da hata
        self.assertIn("yonlendirme.L: 'opus' ajanının 'isci' rolü yok",
                      self.errors(ajanlar={"opus": {"roller": ["planlayici", "beyin", "eskalasyon"]}}))
        self.assertIn("ajanlar.opus.gorev_boyu", self.errors(ajanlar={"opus": {"gorev_boyu": ["XL"]}}))
        self.assertIn("ajanlar.sonnet.es_zamanli", self.errors(ajanlar={"sonnet": {"es_zamanli": 0}}))
        self.assertIn("yonlendirme.eskalasyon: 'opus' ajanının 'eskalasyon' rolü yok",
                      self.errors(ajanlar={"opus": {"roller": ["planlayici", "beyin"]}}))
        # Opus'u yeniden işçi yapmak isteyen kullanıcı: işçi rolü ve L listesi
        c = _ortak.make_config(ajanlar={"opus": {"roller": ["planlayici", "beyin", "isci"]}},
                               yonlendirme={"L": ["sol", "sonnet", "opus"]})
        self.assertEqual(c.workers(), ["opus", "sol", "sonnet", "luna"])
        self.assertFalse(c.has_role("opus", "eskalasyon"))

    def test_valid_alternatives(self):
        c = _ortak.make_config(jev={"beyin": "sol", "yedek_beyin": {"ajan": "opus", "efor": "high"}})
        self.assertEqual((c.brain, c.fallback_brain), ("sol", ("opus", "high")))
        raw = default_raw()
        del raw["jev"]["yedek_beyin"]
        self.assertEqual(Config(raw).fallback_brain, ("sol", "xhigh"))

    def test_backup_reviewer(self):
        for bad, expected in (
                ("opus", 'jev.yedek_denetci: { ajan = "opus", olcekler = ["buyuk"] } biçiminde olmalı'),
                ({"ajan": "zz"}, "jev.yedek_denetci.ajan tanımlı bir ajan olmalı (gelen: 'zz')"),
                ({"olcekler": "buyuk"}, "jev.yedek_denetci.olcekler: mini, kucuk, orta, buyuk ölçeklerinden oluşan "
                                        "bir liste olmalı (gelen: 'buyuk')"),
                ({"olcekler": ["dev"]}, "jev.yedek_denetci.olcekler: mini, kucuk, orta, buyuk ölçeklerinden oluşan "
                                        "bir liste olmalı (gelen: ['dev'])")):
            with self.subTest(bad=bad):
                self.assertIn("- " + expected, self.errors(jev={"yedek_denetci": bad}))
        # orta işte de; kapalı (boş liste, ayar yok ya da yedek denetçinin kendisi)
        c = _ortak.make_config(jev={"yedek_denetci": {"olcekler": ["orta", "buyuk"]}})
        self.assertEqual((c.backup_review("orta"), c.backup_review("kucuk")), ("opus", None))
        self.assertIsNone(_ortak.make_config(jev={"yedek_denetci": {"olcekler": []}}).backup_review("buyuk"))
        self.assertIsNone(_ortak.make_config(jev={"yedek_denetci": {"ajan": "sol"}}).backup_review("buyuk"))
        raw = default_raw()
        del raw["jev"]["yedek_denetci"]
        self.assertIsNone(Config(raw).backup_reviewer)


class MergeTests(unittest.TestCase):
    def test_deep_merge(self):
        base = {"a": {"b": 1, "c": [1, 2]}, "d": 1}
        snapshot = copy.deepcopy(base)
        out = deep_merge(base, {"a": {"c": [3]}, "e": {"f": 1}})
        self.assertEqual(out, {"a": {"b": 1, "c": [3]}, "d": 1, "e": {"f": 1}})
        self.assertEqual(base, snapshot)  # girdi değişmez
        out["a"]["b"] = 99
        self.assertEqual(base["a"]["b"], 1)
        self.assertEqual(deep_merge({"a": {"b": 1}}, {"a": 5}), {"a": 5})


if __name__ == "__main__":
    unittest.main()
