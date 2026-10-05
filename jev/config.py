"""Ayarlar: varsayilan.toml + %USERPROFILE%\\.jev\\jev.toml, doğrulama."""
from __future__ import annotations

import copy
import fnmatch
import tomllib
from pathlib import Path
from typing import Any

from .util import expand_path, jev_home

DEFAULT_PATH = Path(__file__).parent / "varsayilan.toml"
KNOWN_AGENTS = ("opus", "sol", "sonnet", "luna")
VALID_ROLES = {"planlayici", "denetci", "beyin", "eskalasyon", "isci"}
# Koşuyu yöneten sabit görevler: [jev] anahtarı → ajanın taşıması gereken rol (beyin ve yedeği için rol aranmaz)
FIXED_ROLES = {"planlayici": "planlayici", "denetci": "denetci", "beyin": None}
VALID_PROVIDERS = {"codex", "claude"}
VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max")
# Anahtarlarını kullanıcının seçtiği tablolar (görev türleri, efor adları): bilinmeyen anahtar denetimi dışında
FREE_TABLES = ("yonlendirme.tur", "ajanlar.*.efor")
# Ölçek: işin boyu (tören) ve zorluğu (ajan, efor). Zorluk tek görevli işte görev boyuna denk düşer.
SCALES = ("mini", "kucuk", "orta", "buyuk")
SCALE_TR = {"mini": "Mini", "kucuk": "Küçük", "orta": "Orta", "buyuk": "Büyük"}
DIFFICULTIES = ("kolay", "orta", "zor")
DIFFICULTY_TR = {"kolay": "Kolay", "orta": "Orta", "zor": "Zor"}
DIFFICULTY_SIZE = {"kolay": "S", "orta": "M", "zor": "L"}
NO_CAP = "tablo"


def cap_effort(effort: str, cap: str | None) -> str:
    """Eforu ölçek tavanıyla sınırlar; tavan yoksa ("tablo", boş) ya da tanınmıyorsa efor aynen kalır."""
    if not cap or cap not in VALID_EFFORTS or effort not in VALID_EFFORTS:
        return effort
    return min(effort, cap, key=VALID_EFFORTS.index)


def _scaled(value: Any, general: float) -> float:
    """Profil değeri genel ayarın üstüne çıkamaz; 0 (ya da boş) genel ayar demektir."""
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        v = 0.0
    return min(v, general) if v > 0 else general


class ConfigError(ValueError):
    pass


def unknown_keys(over: dict, ref: dict, prefix: str = "") -> list[str]:
    """`over` içinde olup varsayılanlarda (`ref`) bulunmayan anahtarlar: jev.toml'daki yazım hatalarını yakalar."""
    found = []
    for k, v in over.items():
        path = prefix + k
        if k not in ref:
            found.append(path)
        elif isinstance(v, dict) and isinstance(ref[k], dict) \
                and not any(fnmatch.fnmatchcase(path, pat) for pat in FREE_TABLES):
            found += unknown_keys(v, ref[k], path + ".")
    return found


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Config:
    def __init__(self, raw: dict):
        self.raw = raw
        validate_config(raw)

    # --- genel -------------------------------------------------------------
    def get(self, *keys: str, default: Any = None) -> Any:
        cur: Any = self.raw
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    @property
    def project_root(self) -> Path:
        return expand_path(self.raw.get("proje_koku", "~/jev-projeler"))

    def limit(self, key: str) -> int:
        return int(self.raw["sinirlar"][key])

    def timeout_s(self, key: str) -> int:
        return int(float(self.raw["zaman_asimi_dk"][key]) * 60)

    # --- ajanlar -----------------------------------------------------------
    @property
    def agents(self) -> dict[str, dict]:
        return self.raw["ajanlar"]

    def agent(self, name: str) -> dict:
        if name not in self.agents:
            raise ConfigError(f"Bilinmeyen ajan: {name}")
        a = dict(self.agents[name])
        a["ad"] = name
        return a

    def provider(self, name: str) -> str:
        return self.agents[name]["saglayici"]

    def label(self, name: str) -> str:
        return self.agents[name].get("etiket") or self.agents[name]["model"]

    def slots(self, name: str) -> int:
        """Ajanın paralel görevlerde aynı anda alabileceği en fazla görev sayısı."""
        return max(1, int(self.agent(name).get("es_zamanli", 1) or 1))

    def takes(self, name: str, size: str | None) -> bool:
        """İşçi bu boydaki görevi kendiliğinden alır mı (`gorev_boyu`; yoksa her boy)."""
        sizes = self.agent(name).get("gorev_boyu")
        return not sizes or (size or "M") in sizes

    def refusal_model(self, name: str) -> str | None:
        """Güvenlik reddinde aynı çağrının bir kez denendiği model (`ret_yedek_model`)."""
        return self.agent(name).get("ret_yedek_model") or None

    def has_role(self, name: str, role: str) -> bool:
        return role in self.agents.get(name, {}).get("roller", [])

    def workers(self) -> list[str]:
        return [a for a in self.agents if self.has_role(a, "isci")]

    def effort(self, name: str, key: str, cap: str | None = None) -> str:
        """Ajanın efor tablosundaki değer; `cap` (ölçek tavanı) verilirse ondan yüksek olamaz."""
        ef = self.agents[name].get("efor", {})
        return cap_effort(ef.get(key) or ef.get("varsayilan") or ef.get("M") or "high", cap)

    # --- ölçek -------------------------------------------------------------
    def profile(self, level: str | None) -> dict:
        """[olcek.<seviye>] profili. Bilinmeyen ya da boş seviye büyük sayılır: ölçeği olmayan eski koşular
        bugünkü tam hattan geçer."""
        lvl = level if level in SCALES else "buyuk"
        return {**((self.raw.get("olcek") or {}).get(lvl) or {}), "seviye": lvl}

    def step_cap(self, level: str | None, step: str) -> str | None:
        """plan / denetim adımının efor tavanı: None = tavansız (ajan tablosu). "yok" ve "jev" ayrıca sorulur
        (skips_step, jev_review)."""
        v = self.profile(level).get(step) or NO_CAP
        return v if v in VALID_EFFORTS else None

    def skips_step(self, level: str | None, step: str) -> bool:
        return self.profile(level).get(step) == "yok"

    def jev_review(self, level: str | None) -> bool:
        """Son kontrolü denetçi ajan değil Jev mi yapar (mini, küçük): komutları Jev çalıştırır, raporu da kendisi yazar."""
        return self.profile(level).get("denetim") == "jev"

    def worker_cap(self, level: str | None, size: str) -> str | None:
        v = (self.profile(level).get("isci_tavan") or {}).get(size) or NO_CAP
        return v if v in VALID_EFFORTS else None

    def brain_cap(self, level: str | None) -> str | None:
        v = self.profile(level).get("beyin_tavan") or NO_CAP
        return v if v in VALID_EFFORTS else None

    def attempts(self, level: str | None) -> int:
        return int(_scaled(self.profile(level).get("deneme"), self.limit("gorev_basina_deneme")))

    def max_tasks(self, level: str | None) -> int:
        return int(_scaled(self.profile(level).get("en_fazla_gorev"), self.limit("en_fazla_gorev")))

    def verify_timeout_s(self, level: str | None) -> int:
        return int(_scaled(self.profile(level).get("verify_dk"), float(self.raw["zaman_asimi_dk"]["verify"])) * 60)

    def parallel(self, level: str | None) -> int:
        return max(1, int(_scaled(self.profile(level).get("paralel"), int(self.raw.get("paralel", 1) or 1))))

    def pool(self, difficulty: str | None) -> list[str]:
        """Tek görevli işin aday işçileri (zorluğa göre); yalnızca 'isci' rolü olanlar ilk denemede aday olur."""
        lst = ((self.raw.get("olcek") or {}).get("havuz") or {}).get(difficulty or "", [])
        return [a for a in lst if a in self.agents and self.has_role(a, "isci")]

    @property
    def brain(self) -> str:
        return self.raw["jev"]["beyin"]

    @property
    def planner(self) -> str:
        """Planı ve görev kartlarını yazan ajan."""
        return self.raw["jev"]["planlayici"]

    @property
    def reviewer(self) -> str:
        """Orta ve büyük işin son kontrolünü yapan ajan."""
        return self.raw["jev"]["denetci"]

    @property
    def backup_reviewer(self) -> str | None:
        """Yedek denetçi ([jev] yedek_denetci): kodun çoğunu denetçi kendisi yazdıysa son kontrolü yapan ajan.
        Kapalıysa (ölçek listesi boş ya da yedek denetçinin kendisi) None."""
        yd = self.raw["jev"].get("yedek_denetci") or {}
        name = yd.get("ajan")
        return name if name in self.agents and name != self.reviewer and yd.get("olcekler") else None

    def backup_review(self, level: str | None) -> str | None:
        """Bu ölçekte yedek denetçi devreye girebilir mi: girebilirse adı. Bilinmeyen seviye büyük sayılır."""
        name = self.backup_reviewer
        scales = (self.raw["jev"].get("yedek_denetci") or {}).get("olcekler") or []
        return name if name and self.profile(level)["seviye"] in scales else None

    @property
    def swap_partner(self) -> str | None:
        """Ret takası ([jev] ret_takasi): planlayıcı güvenlik gerekçesiyle reddederse yer değiştireceği ajan."""
        name = self.raw["jev"].get("ret_takasi") or None
        return name if name in self.agents and name != self.planner else None

    @property
    def fallback_brain(self) -> tuple[str, str]:
        fb = self.raw["jev"].get("yedek_beyin") or {}
        return fb.get("ajan", "sol"), fb.get("efor", "xhigh")


def validate_config(raw: dict) -> None:
    errs: list[str] = []
    par = raw.get("paralel", 1)
    if isinstance(par, bool) or not isinstance(par, int) or par < 1:
        errs.append("paralel: en az 1 olan bir tam sayı olmalı")
    agents = raw.get("ajanlar", {})
    for name, a in agents.items():
        if name not in KNOWN_AGENTS:
            errs.append(f"ajanlar.{name}: bilinen ajanlar {', '.join(KNOWN_AGENTS)}")
        if a.get("saglayici") not in VALID_PROVIDERS:
            errs.append(f"ajanlar.{name}.saglayici: 'codex' ya da 'claude' olmalı")
        if not a.get("model"):
            errs.append(f"ajanlar.{name}.model boş olamaz")
        es = a.get("es_zamanli", 1)
        if isinstance(es, bool) or not isinstance(es, int) or es < 1:
            errs.append(f"ajanlar.{name}.es_zamanli: en az 1 olan bir tam sayı olmalı")
        gb = a.get("gorev_boyu", ["S", "M", "L"])
        if not isinstance(gb, list) or not gb or not all(x in ("S", "M", "L") for x in gb):
            errs.append(f"ajanlar.{name}.gorev_boyu: S, M, L boylarından oluşan boş olmayan bir liste olmalı")
        if not isinstance(a.get("ret_yedek_model", ""), str):
            errs.append(f"ajanlar.{name}.ret_yedek_model: model adı ya da \"\" olmalı")
        for r in a.get("roller", []):
            if r not in VALID_ROLES:
                errs.append(f"ajanlar.{name}.roller: geçersiz rol '{r}'")
        for k, v in (a.get("efor") or {}).items():
            if v not in VALID_EFFORTS:
                errs.append(f"ajanlar.{name}.efor.{k}: geçersiz efor '{v}' (ultra kullanılmaz)")
    jev = raw.get("jev", {})
    for key, role in FIXED_ROLES.items():
        name = jev.get(key)
        if name not in agents:
            errs.append(f"jev.{key} tanımlı bir ajan olmalı (gelen: {name!r})")
        elif role and role not in agents[name].get("roller", []):
            errs.append(f"jev.{key}: '{name}' ajanının '{role}' rolü yok")
    fb = (jev.get("yedek_beyin") or {}).get("ajan")
    if fb and fb not in agents:
        errs.append(f"jev.yedek_beyin.ajan tanımlı bir ajan olmalı (gelen: {fb!r})")
    rt = jev.get("ret_takasi", "")
    if rt and rt not in agents:
        errs.append(f"jev.ret_takasi tanımlı bir ajan ya da \"\" olmalı (gelen: {rt!r})")
    elif rt and rt == jev.get("planlayici"):
        errs.append("jev.ret_takasi planlayıcının kendisi olamaz")
    elif rt and "isci" not in agents[rt].get("roller", []):
        errs.append(f"jev.ret_takasi: '{rt}' ajanının 'isci' rolü yok (reddedilen görevi de o alır)")
    yd = jev.get("yedek_denetci")
    if yd is not None and not isinstance(yd, dict):
        errs.append('jev.yedek_denetci: { ajan = "opus", olcekler = ["buyuk"] } biçiminde olmalı')
    elif yd is not None:
        if yd.get("ajan") not in agents:
            errs.append(f"jev.yedek_denetci.ajan tanımlı bir ajan olmalı (gelen: {yd.get('ajan')!r})")
        olc = yd.get("olcekler", [])
        if not isinstance(olc, list) or not all(x in SCALES for x in olc):
            errs.append(f"jev.yedek_denetci.olcekler: {', '.join(SCALES)} ölçeklerinden oluşan bir liste olmalı "
                        f"(gelen: {olc!r})")
    jm = jev.get("model") or {}
    try:
        if not 0.0 <= float(jm.get("guven_esigi", 0.6)) <= 1.0:
            errs.append("jev.model.guven_esigi 0 ile 1 arasında olmalı")
        if float(jm.get("zaman_asimi_sn", 20)) <= 0 or int(jm.get("deneme", 3)) < 1:
            errs.append("jev.model.zaman_asimi_sn pozitif, jev.model.deneme en az 1 olmalı")
    except (TypeError, ValueError):
        errs.append("jev.model: guven_esigi, zaman_asimi_sn ve deneme sayı olmalı")
    routing = raw.get("yonlendirme", {})
    lists = [(k, routing.get(k, [])) for k in ("S", "M", "L")]
    lists += [(f"tur.{k}", v) for k, v in (routing.get("tur") or {}).items()]
    for key, lst in lists:
        for a in lst:
            if a not in agents:
                errs.append(f"yonlendirme.{key}: bilinmeyen ajan '{a}'")
            elif "isci" not in agents[a].get("roller", []):
                errs.append(f"yonlendirme.{key}: '{a}' ajanının 'isci' rolü yok")
    for a in routing.get("eskalasyon", []):
        if a not in agents:
            errs.append(f"yonlendirme.eskalasyon: bilinmeyen ajan '{a}'")
        elif not {"eskalasyon", "isci"} & set(agents[a].get("roller", [])):
            errs.append(f"yonlendirme.eskalasyon: '{a}' ajanının 'eskalasyon' rolü yok")
    errs += _validate_scales(raw.get("olcek") or {}, agents)
    if errs:
        raise ConfigError("Ayar hataları:\n- " + "\n- ".join(errs))


def _validate_scales(ol: dict, agents: dict) -> list[str]:
    errs: list[str] = []
    try:
        if not 0.0 <= float(ol.get("emin_olma", 0.5)) <= 1.0:
            errs.append("olcek.emin_olma 0 ile 1 arasında olmalı")
    except (TypeError, ValueError):
        errs.append("olcek.emin_olma sayı olmalı")
    for key in ("ifade_mini", "ifade_buyuk", "kural_mini", "kural_buyuk"):
        v = ol.get(key, [])
        if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
            errs.append(f"olcek.{key}: boş olmayan metinlerden oluşan bir liste olmalı")
    for diff, lst in (ol.get("havuz") or {}).items():
        if diff not in DIFFICULTIES:
            errs.append(f"olcek.havuz.{diff}: zorluk {', '.join(DIFFICULTIES)} olmalı")
        for a in lst if isinstance(lst, list) else []:
            if a not in agents:
                errs.append(f"olcek.havuz.{diff}: bilinmeyen ajan '{a}'")
            elif not {"isci", "eskalasyon"} & set(agents[a].get("roller", [])):
                errs.append(f"olcek.havuz.{diff}: '{a}' ajanının 'isci' rolü yok")
    steps = {"plan": ("yok", NO_CAP), "denetim": ("jev", NO_CAP), "beyin_tavan": (NO_CAP,)}
    for lvl in SCALES:
        p = ol.get(lvl) or {}
        for key, extra in steps.items():
            if key in p and p[key] not in VALID_EFFORTS + extra:
                errs.append(f"olcek.{lvl}.{key}: geçersiz değer {p[key]!r} ({', '.join(extra + VALID_EFFORTS)})")
        for size, v in (p.get("isci_tavan") or {}).items():
            if size not in ("S", "M", "L") or v not in VALID_EFFORTS + (NO_CAP,):
                errs.append(f"olcek.{lvl}.isci_tavan.{size}: geçersiz efor {v!r}")
        for key in ("en_fazla_gorev", "deneme", "verify_dk", "paralel"):
            v = p.get(key, 0)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                errs.append(f"olcek.{lvl}.{key}: 0 ya da pozitif bir sayı olmalı")
    return errs


def load_config(extra_path: Path | None = None, overrides: dict | None = None) -> Config:
    defaults = tomllib.loads(DEFAULT_PATH.read_text(encoding="utf-8"))
    raw = defaults
    user = jev_home() / "jev.toml"
    for p in (user, extra_path):
        if p and p.exists():
            try:
                over = tomllib.loads(p.read_text(encoding="utf-8-sig"))
            except tomllib.TOMLDecodeError as e:
                raise ConfigError(f"{p} okunamadı: {e}") from e
            bad = unknown_keys(over, defaults)
            if bad:
                raise ConfigError(f"{p}: bilinmeyen ayar: {', '.join(bad)} (yazım hatası olabilir; geçerli "
                                  "anahtarlar Jev'in varsayilan.toml dosyasında)")
            raw = deep_merge(raw, over)
    if overrides:
        raw = deep_merge(raw, overrides)
    return Config(raw)
