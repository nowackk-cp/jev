"""Görev grafiği: doğrulama mesajları, döngü, kritik yol ağırlığı, sıralama, aynı dosyaya dokunan kartların sırası."""
import unittest

import _ortak  # noqa: F401  (JEV_HOME geçici klasöre)
from jev.dag import DEAD_STATUSES, dependents_count, find_cycle, link_shared_files, topo_order, validate_tasks

PLAN = {"modules": [{"id": "M1"}, {"id": "M2"}],
        "success_criteria": [{"id": "SC1"}, {"id": "SC2"}, {"id": "SC10"}]}


def task(tid, **kw):
    t = {"id": tid, "module": "M1", "type": "code", "verify": ["py -m unittest"], "depends_on": [],
         "covers_criteria": []}
    t.update(kw)
    return t


def full_cover():
    return [task("T1", covers_criteria=["SC1", "SC2"]),
            task("T2", module="M2", covers_criteria=["SC10"], depends_on=["T1"])]


class ValidateTests(unittest.TestCase):
    def test_empty_list_is_single_error(self):
        self.assertEqual(validate_tasks([], PLAN), ["Görev listesi boş."])

    def test_valid_list(self):
        self.assertEqual(validate_tasks(full_cover(), PLAN), [])

    def test_too_many(self):
        errs = validate_tasks(full_cover() + [task("T3")], PLAN, max_tasks=2)
        self.assertIn("Çok fazla görev: 3 (en fazla 2).", errs)

    def test_invalid_ids(self):
        errs = validate_tasks([task(""), task("-a"), task("T 1"), task("Görev"), task("A" * 33),
                               task("a" * 32), task("D1-01"), task("x_y.z")], None)
        self.assertIn("1. görevin kimliği geçersiz: '' (harf, rakam, - _ . ; en fazla 32 karakter).", errs)
        for i in (2, 3, 4, 5):
            self.assertTrue(any(e.startswith(f"{i}. görevin kimliği geçersiz") for e in errs), (i, errs))
        for i in (6, 7, 8):
            self.assertFalse(any(e.startswith(f"{i}. görevin") for e in errs), (i, errs))

    def test_duplicate_and_existing_clash(self):
        errs = validate_tasks([task("T1"), task("T1"), task("T5")], None, existing=[task("T5")])
        self.assertIn("Görev kimliği tekrarlanıyor: T1.", errs)
        self.assertIn("Görev kimliği mevcut bir görevle çakışıyor: T5.", errs)

    def test_dependencies(self):
        errs = validate_tasks([task("T1", depends_on=["T1"]), task("T2", depends_on=["T9"])], None)
        self.assertIn("T1 kendisine bağımlı.", errs)
        self.assertIn("T2 olmayan bir göreve bağımlı: T9.", errs)

    def test_dependency_on_existing_task_is_fine(self):
        self.assertEqual(validate_tasks([task("D1-01", depends_on=["T1"])], None, existing=[task("T1")]), [])

    def test_unknown_module_and_criterion(self):
        errs = validate_tasks([task("T1", module="M9", covers_criteria=["SC9"])], PLAN, require_coverage=False)
        self.assertIn("T1: bilinmeyen modül 'M9' (plandaki modüller: M1, M2).", errs)
        self.assertIn("T1: bilinmeyen başarı ölçütü 'SC9'.", errs)

    def test_code_and_test_tasks_need_verify(self):
        errs = validate_tasks([task("T1", verify=[]), task("T2", type="test", verify=["  "]),
                               task("T3", type="docs", verify=[]), task("T4", type="config", verify=None)], None)
        self.assertIn("T1: code görevinde en az bir doğrulama komutu (verify) olmalı.", errs)
        self.assertIn("T2: test görevinde en az bir doğrulama komutu (verify) olmalı.", errs)
        self.assertEqual(len(errs), 2, errs)

    def test_cycle(self):
        errs = validate_tasks([task("A", depends_on=["B"]), task("B", depends_on=["A"])], None)
        self.assertEqual(errs, ["Döngüsel bağımlılık: A → B → A."])

    def test_cycle_through_existing_tasks(self):
        existing = [task("T1", depends_on=["D1-01"])]
        errs = validate_tasks([task("D1-01", depends_on=["T1"])], None, existing=existing)
        self.assertTrue(any(e.startswith("Döngüsel bağımlılık:") for e in errs), errs)

    def test_coverage_natural_order(self):
        errs = validate_tasks([task("T1", covers_criteria=["SC1"])], PLAN)
        self.assertIn("Hiçbir görevin karşılamadığı başarı ölçütleri: SC2, SC10.", errs)
        self.assertIn("Hiçbir görevin karşılamadığı modüller: M2.", errs)

    def test_coverage_can_be_disabled_or_come_from_existing(self):
        self.assertEqual(validate_tasks([task("T1", covers_criteria=["SC1"])], PLAN, require_coverage=False), [])
        existing = [task("T1", covers_criteria=["SC1", "SC2"]), task("T2", module="M2")]
        self.assertEqual(validate_tasks([task("D1-01", covers_criteria=["SC10"])], PLAN, existing=existing), [])

    def test_without_plan_modules_are_not_checked(self):
        self.assertEqual(validate_tasks([task("T1", module="herhangi")], None), [])


class GraphTests(unittest.TestCase):
    def test_find_cycle(self):
        self.assertEqual(find_cycle([task("A", depends_on=["B"]), task("B", depends_on=["A"])]), ["A", "B", "A"])
        self.assertIsNone(find_cycle([task("A"), task("B", depends_on=["A", "YOK"])]))
        self.assertEqual(find_cycle([task("A", depends_on=["A"])]), ["A", "A"])

    def test_dependents_count(self):
        tasks = [task("A"), task("B", depends_on=["A"]), task("C", depends_on=["B"]), task("D")]
        self.assertEqual(dependents_count(tasks), {"A": 2, "B": 1, "C": 0, "D": 0})

    def test_dependents_count_diamond_counts_once(self):
        tasks = [task("A"), task("B", depends_on=["A"]), task("C", depends_on=["A"]),
                 task("D", depends_on=["B", "C"])]
        self.assertEqual(dependents_count(tasks)["A"], 3)

    def test_topo_order(self):
        tasks = [task("C", depends_on=["B"]), task("B", depends_on=["A"]), task("A"), task("X")]
        order = topo_order(tasks)
        self.assertLess(order.index("A"), order.index("B"))
        self.assertLess(order.index("B"), order.index("C"))
        self.assertEqual(sorted(order), ["A", "B", "C", "X"])

    def test_topo_order_with_cycle_keeps_everything(self):
        order = topo_order([task("A", depends_on=["B"]), task("B", depends_on=["A"]), task("C")])
        self.assertEqual(order, ["C", "A", "B"])


class LinkSharedFilesTests(unittest.TestCase):
    """Aynı dosyaya dokunan kartları Jev kendisi sıraya koyar (planlayıcıya geri dönmeden)."""

    def test_reader_waits_for_writer(self):
        tasks = [task("T1", outputs=["src/a.py"]), task("T2", reads=[".\\SRC\\A.py"])]  # yol yazımı fark etmez
        self.assertEqual(link_shared_files(tasks), ["T2 → T1 (okuduğu .\\SRC\\A.py dosyasını yazıyor)"])
        self.assertEqual([t["depends_on"] for t in tasks], [[], ["T1"]])

    def test_second_writer_waits_for_first(self):
        tasks = [task("T1", outputs=["ortak.txt"]), task("T2", outputs=["ortak.txt"])]
        self.assertEqual(link_shared_files(tasks), ["T2 → T1 (ikisi de ortak.txt dosyasını yazıyor)"])
        self.assertEqual([t["depends_on"] for t in tasks], [[], ["T1"]])

    def test_no_self_duplicate_or_cycle_links(self):
        # kendi yazdığını okuyan, zaten (dolaylı) bekleyen ya da döngü doğuracak kart için bağ eklenmez
        tasks = [task("T1", outputs=["a.py"], reads=["a.py"]),
                 task("T2", depends_on=["T1"]),
                 task("T3", depends_on=["T2"], reads=["a.py"]),
                 task("T4", outputs=["b.py"], depends_on=["T5"]),
                 task("T5", reads=["b.py"])]
        before = [list(t["depends_on"]) for t in tasks]
        self.assertEqual(link_shared_files(tasks), [])
        self.assertEqual([t["depends_on"] for t in tasks], before)

    def test_existing_tasks_and_dead_statuses(self):
        # düzeltme kartı eski görevin yazdığı dosyayı okursa onu bekler; bölünmüş, başarısız ya da engellenmiş
        # görev hiç bitmeyeceği için beklenmez. Eski görevlere dokunulmaz.
        for dead in DEAD_STATUSES:
            with self.subTest(status=dead):
                existing = [task("T1", outputs=["a.py"], status="done"), task("T2", outputs=["b.py"], status=dead)]
                new = [task("D1-01", reads=["a.py", "b.py"])]
                self.assertEqual(link_shared_files(new, existing=existing),
                                 ["D1-01 → T1 (okuduğu a.py dosyasını yazıyor)"])
                self.assertEqual(new[0]["depends_on"], ["T1"])
                self.assertEqual([t["depends_on"] for t in existing], [[], []])


if __name__ == "__main__":
    unittest.main()
