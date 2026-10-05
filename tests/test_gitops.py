"""Git yardımcıları: depo açma, .jev dışlama, dal, checkpoint/geri alma, commit, değişiklik özeti.

Git, kullanıcının genel ayarlarından yalıtılmış çalışır (bkz. `_ortak`).
"""
import os
import unittest

import _ortak
from jev import gitops
from jev.gitops import (GITIGNORE_DEFAULT, GitError, branch_exists, changes_since, checkpoint, commit_all,
                        commit_paths, count_commits, current_branch, diff_stat, ensure_exclude, file_tree, git,
                        has_commits, head, identity_args, init_repo, inside_repo, is_clean, is_repo, log_oneline,
                        rollback, shortstat_since, start_branch, status_lines)

def write(path, text="x\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def last_commit(cwd, fmt="%s"):
    return git(["log", "-1", f"--format={fmt}"], cwd).stdout.strip()


class RepoTests(unittest.TestCase):
    def setUp(self):
        self.p = _ortak.temp_dir("git") / "proje"
        self.base = init_repo(self.p)

    def test_init_repo(self):
        self.assertRegex(self.base, r"^[0-9a-f]{40}$")
        self.assertTrue(is_repo(self.p) and has_commits(self.p) and is_clean(self.p))
        self.assertEqual((self.p / ".gitignore").read_text(encoding="utf-8"), GITIGNORE_DEFAULT)
        self.assertEqual(last_commit(self.p), "jev: proje başlangıcı")
        self.assertEqual(last_commit(self.p, "%an <%ae>"), "Jev <jev@localhost>")  # kimlik yoksa geçici kimlik
        exclude = (self.p / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        self.assertIn(".jev/", exclude.splitlines())
        # ikinci kez: yeni commit yok, dışlama satırı tekrarlanmaz
        self.assertEqual(init_repo(self.p), self.base)
        self.assertEqual(count_commits(self.p, self.base), 0)
        self.assertEqual((self.p / ".git" / "info" / "exclude").read_text(encoding="utf-8").count(".jev/"), 1)

    def test_existing_gitignore_is_kept(self):
        p = _ortak.temp_dir("git") / "ozel"
        write(p / ".gitignore", "benim/\n")
        init_repo(p, "ilk")
        self.assertEqual((p / ".gitignore").read_text(encoding="utf-8"), "benim/\n")
        self.assertEqual(last_commit(p), "ilk")

    def test_is_repo_vs_inside(self):
        sub = self.p / "alt"
        sub.mkdir()
        self.assertFalse(is_repo(sub))
        self.assertTrue(inside_repo(sub))
        self.assertFalse(is_repo(_ortak.temp_dir("duz")))

    def test_ensure_exclude_variants(self):
        ex = self.p / ".git" / "info" / "exclude"
        ex.write_text("foo", encoding="utf-8")
        ensure_exclude(self.p)
        self.assertEqual(ex.read_text(encoding="utf-8"), "foo\n.jev/\n")
        ex.write_text("/.jev\n", encoding="utf-8")
        ensure_exclude(self.p)
        self.assertEqual(ex.read_text(encoding="utf-8"), "/.jev\n")

    def test_jev_folder_never_shows_up(self):
        write(self.p / ".jev" / "state.json", "{}")
        self.assertTrue(is_clean(self.p))
        self.assertTrue(gitops._is_jev_path(".jev"))
        self.assertTrue(gitops._is_jev_path('".jev/a b.json"'))
        self.assertTrue(gitops._is_jev_path(".jev\\x"))
        self.assertFalse(gitops._is_jev_path(".jevx"))
        self.assertFalse(gitops._is_jev_path("src/.jev"))
        write(self.p / "yeni.txt")
        self.assertEqual(status_lines(self.p), ["?? yeni.txt"])

    def test_identity(self):
        self.assertEqual(identity_args(self.p), ["-c", "user.name=Jev", "-c", "user.email=jev@localhost"])
        git(["config", "user.name", "Deneme Kişi"], self.p)  # yalnızca bu deponun ayarı
        git(["config", "user.email", "deneme@example.invalid"], self.p)
        self.assertEqual(identity_args(self.p), [])
        write(self.p / "a.txt")
        commit_all(self.p, "kimlikli")
        self.assertEqual(last_commit(self.p, "%an"), "Deneme Kişi")

    def test_branches(self):
        first = current_branch(self.p)
        start_branch(self.p, "jev/r1")
        self.assertEqual(current_branch(self.p), "jev/r1")
        self.assertTrue(branch_exists(self.p, "jev/r1"))
        self.assertFalse(branch_exists(self.p, "jev/yok"))
        start_branch(self.p, "jev/r1")  # zaten orada
        git(["checkout", "-q", first], self.p)
        start_branch(self.p, "jev/r1")  # var olan dala geçer
        self.assertEqual(current_branch(self.p), "jev/r1")

    def test_checkpoint_and_rollback_keep_jev(self):
        write(self.p / "a.py", "print(1)\n")
        commit_all(self.p, "a")
        cp = checkpoint(self.p)
        write(self.p / "a.py", "bozuk\n")
        write(self.p / "yeni.py")
        write(self.p / "klasör" / "b.py")
        write(self.p / ".jev" / "state.json", '{"korunur": true}')
        write(self.p / "__pycache__" / "x.pyc", "önbellek")
        commit_all(self.p, "yarım iş")
        write(self.p / "commitsiz.txt")
        rollback(self.p, cp)
        self.assertEqual(head(self.p), cp)
        self.assertEqual((self.p / "a.py").read_text(encoding="utf-8"), "print(1)\n")
        for gone in ("yeni.py", "klasör", "commitsiz.txt"):
            self.assertFalse((self.p / gone).exists(), gone)
        self.assertEqual((self.p / ".jev" / "state.json").read_text(encoding="utf-8"), '{"korunur": true}')
        self.assertTrue((self.p / "__pycache__" / "x.pyc").exists())  # yok sayılan dosyalara dokunulmaz

    def test_rollback_switches_back_to_run_branch(self):
        start_branch(self.p, "jev/r2")
        cp = checkpoint(self.p)
        git(["checkout", "-q", "-b", "baska"], self.p)
        write(self.p / "x.txt")
        rollback(self.p, cp, branch="jev/r2")
        self.assertEqual(current_branch(self.p), "jev/r2")
        self.assertFalse((self.p / "x.txt").exists())

    def test_commit_all(self):
        write(self.p / "a.txt")
        h = commit_all(self.p, "jev(T01): Görev başlığı [sol]")
        self.assertEqual(h, head(self.p))
        self.assertEqual(last_commit(self.p), "jev(T01): Görev başlığı [sol]")
        self.assertIsNone(commit_all(self.p, "boş", allow_empty=False))
        self.assertEqual(head(self.p), h)
        h2 = commit_all(self.p, "boş commit")
        self.assertNotEqual(h2, h)
        self.assertEqual(count_commits(self.p, self.base), 2)

    def test_commit_paths(self):
        write(self.p / "rapor.md", "# Rapor\n")
        write(self.p / "baska.txt")
        h = commit_paths(self.p, ["rapor.md", "olmayan.md"], "jev: rapor")
        self.assertEqual(h, head(self.p))
        self.assertEqual(status_lines(self.p), ["?? baska.txt"])
        self.assertIsNone(commit_paths(self.p, ["rapor.md"], "değişiklik yok"))
        self.assertIsNone(commit_paths(self.p, ["olmayan.md"], "dosya yok"))

    def test_changes_since(self):
        write(self.p / "degisecek.py")
        write(self.p / "silinecek.py")
        commit_all(self.p, "hazırlık")
        cp = checkpoint(self.p)
        write(self.p / "degisecek.py", "yeni içerik\n")
        (self.p / "silinecek.py").unlink()
        write(self.p / "eklenen.py")
        commit_all(self.p, "commitli değişiklik")
        write(self.p / "ğüşiöç.txt")
        write(self.p / "klasör" / "yeni dosya.md")
        write(self.p / ".jev" / "gunluk.txt")
        ch = changes_since(self.p, cp)
        self.assertEqual(sorted(ch["added"]), ["eklenen.py", "klasör/yeni dosya.md", "ğüşiöç.txt"])
        self.assertEqual(ch["modified"], ["degisecek.py"])
        self.assertEqual(ch["deleted"], ["silinecek.py"])
        self.assertEqual(ch["all"], sorted(ch["added"] + ["degisecek.py", "silinecek.py"]))

    def test_summaries(self):
        self.assertEqual(shortstat_since(self.p, self.base), "değişiklik yok")
        write(self.p / "a.txt")
        write(self.p / "b.txt")
        self.assertEqual(shortstat_since(self.p, self.base), "2 yeni dosya")
        commit_all(self.p, "iki dosya")
        write(self.p / "a.txt", "değişti\n")
        write(self.p / "c.txt")
        s = shortstat_since(self.p, self.base)
        self.assertTrue(s.endswith(", 1 yeni dosya"), s)
        self.assertIn("a.txt", diff_stat(self.p, self.base))
        self.assertIn("iki dosya", log_oneline(self.p, self.base))
        self.assertNotIn("proje başlangıcı", log_oneline(self.p, self.base))
        self.assertEqual(count_commits(self.p, self.base), 1)
        self.assertEqual(count_commits(self.p, "olmayan-ref"), 0)

    def test_file_tree(self):
        for name in ("b.py", "a.py", "src/c.py"):
            write(self.p / name)
        commit_all(self.p, "dosyalar")
        write(self.p / "takipsiz.md")
        write(self.p / ".jev" / "state.json")
        write(self.p / "__pycache__" / "x.pyc")
        self.assertEqual(file_tree(self.p).splitlines(), [".gitignore", "a.py", "b.py", "src/c.py", "takipsiz.md"])
        self.assertEqual(file_tree(self.p, limit=2).splitlines(), [".gitignore", "a.py", "… ve 3 dosya daha"])

    def test_errors(self):
        with self.assertRaises(GitError) as cm:
            git(["checkout", "olmayan-dal"], self.p)
        self.assertTrue(str(cm.exception).startswith("git checkout olmayan-dal başarısız ("), str(cm.exception))
        self.assertNotEqual(git(["checkout", "olmayan-dal"], self.p, check=False).returncode, 0)
        empty = _ortak.temp_dir("git") / "bos"
        empty.mkdir()
        git(["init", "-q"], empty)
        self.assertFalse(has_commits(empty))
        with self.assertRaises(GitError):
            head(empty)


def same_path(a, b) -> bool:
    """git yolları uzun adla yazar; geçici klasör kısa adla (ADMINI~1) gelebilir."""
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


class WorktreeTests(unittest.TestCase):
    """Paralel görev şeritleri: çalışma ağacı açma ve kaldırma, koşu dalına tek commit birleştirme, çakışmada ana
    ağacın korunması, işin koşunun son hâline işaretlerle taşınması, bağımlılık klasörlerinin bağlanması."""
    BRANCH = "jev/r1-serit-T01"

    def setUp(self):
        self.p = _ortak.temp_dir("git") / "proje"
        init_repo(self.p)
        start_branch(self.p, "jev/r1")
        write(self.p / "ortak.txt", "bir\niki\nüç\n")
        self.base = commit_all(self.p, "ortak")
        self.root = gitops.worktree_root(self.p)

    def lane(self):
        path = self.root / "abc-T01"
        gitops.worktree_add(self.p, path, self.BRANCH, self.base)
        return path

    def conflicting(self, path):
        """Şerit ve koşu dalı aynı satırı farklı değiştirir. Dönüş: (şeridin commit'i, koşu dalının commit'i)."""
        write(path / "ortak.txt", "bir\nIKI (şerit)\nüç\n")
        write(path / "serit.txt", "görevin işi\n")
        rev = commit_all(path, "şerit")
        write(self.p / "ortak.txt", "bir\nİKİ (ana)\nüç\n")
        return rev, commit_all(self.p, "ana")

    def test_root_is_project_sibling(self):
        self.assertEqual(self.root, self.p.parent / "proje--jev-wt")

    def test_add_and_remove(self):
        path = self.lane()
        self.assertEqual((current_branch(path), head(path)), (self.BRANCH, self.base))
        self.assertEqual(current_branch(self.p), "jev/r1")  # ana ağaç yerinde
        self.assertTrue(any(same_path(x, path) for x in gitops.worktree_paths(self.p)))
        self.assertEqual(gitops.branches(self.p, "jev/r1-serit-"), [self.BRANCH])
        write(path / "yarim.txt")
        gitops.worktree_add(self.p, path, self.BRANCH, self.base)  # aynı yere yeniden: eskisi kaldırılır
        self.assertFalse((path / "yarim.txt").exists())
        gitops.worktree_remove(self.p, path, self.BRANCH)
        self.assertFalse(path.exists())
        self.assertFalse(branch_exists(self.p, self.BRANCH))
        self.assertEqual(len(gitops.worktree_paths(self.p)), 1)
        gitops.worktree_remove(self.p, path, self.BRANCH)  # olmayan şerit: hata yok

    def test_squash_merge_single_commit(self):
        path = self.lane()
        write(path / "a.py", "print(1)\n")
        commit_all(path, "ara 1")
        write(path / "b.py", "print(2)\n")
        commit_all(path, "ara 2")
        sha = gitops.squash_merge(self.p, self.BRANCH, "jev(T01): iş [sonnet]")
        self.assertEqual(head(self.p), sha)
        self.assertEqual(last_commit(self.p), "jev(T01): iş [sonnet]")
        self.assertEqual(git(["rev-list", "--parents", "-n", "1", "HEAD"], self.p).stdout.split()[1:], [self.base])
        self.assertTrue((self.p / "a.py").exists() and (self.p / "b.py").exists())
        self.assertTrue(is_clean(self.p))

    def test_squash_merge_conflict_restores_main(self):
        path = self.lane()
        _, main = self.conflicting(path)
        with self.assertRaises(gitops.MergeConflict) as cm:
            gitops.squash_merge(self.p, self.BRANCH, "jev(T01)")
        self.assertEqual(cm.exception.files, ["ortak.txt"])
        self.assertEqual(head(self.p), main)
        self.assertTrue(is_clean(self.p))
        self.assertFalse((self.p / "serit.txt").exists())
        self.assertEqual((self.p / "ortak.txt").read_text(encoding="utf-8"), "bir\nİKİ (ana)\nüç\n")

    def test_update_from(self):
        path = self.lane()
        write(path / "serit.txt")
        commit_all(path, "şerit")
        write(self.p / "ana.txt")
        main = commit_all(self.p, "ana")
        gitops.update_from(path, main, "güncelle")
        self.assertTrue((path / "ana.txt").exists() and (path / "serit.txt").exists())
        self.assertEqual(git(["merge-base", "--is-ancestor", main, "HEAD"], path, check=False).returncode, 0)

    def test_update_from_conflict_keeps_lane(self):
        path = self.lane()
        rev, main = self.conflicting(path)
        with self.assertRaises(gitops.MergeConflict) as cm:
            gitops.update_from(path, main, "güncelle")
        self.assertEqual(cm.exception.files, ["ortak.txt"])
        self.assertEqual(head(path), rev)
        self.assertTrue(is_clean(path))

    def test_carry_with_markers(self):
        path = self.lane()
        rev, main = self.conflicting(path)
        self.assertEqual(gitops.carry(self.p, path, self.BRANCH, main, rev), ["ortak.txt"])
        self.assertEqual((current_branch(path), head(path)), (self.BRANCH, main))
        text = (path / "ortak.txt").read_text(encoding="utf-8")
        for part in ("<<<<<<<", ">>>>>>>", "İKİ (ana)", "IKI (şerit)"):
            self.assertIn(part, text)
        self.assertEqual((path / "serit.txt").read_text(encoding="utf-8"), "görevin işi\n")
        self.assertEqual(git(["diff", "--cached", "--name-only"], path).stdout.strip(), "")  # index temiz
        self.assertEqual(changes_since(path, main)["all"], ["ortak.txt", "serit.txt"])
        self.assertFalse(branch_exists(self.p, self.BRANCH + "-onceki"))

    def test_carry_without_conflict(self):
        path = self.lane()
        write(path / "serit.txt", "görevin işi\n")
        rev = commit_all(path, "şerit")
        write(self.p / "ana.txt")
        main = commit_all(self.p, "ana")
        self.assertEqual(gitops.carry(self.p, path, self.BRANCH, main, rev), [])
        self.assertEqual(head(path), main)
        self.assertTrue((path / "ana.txt").exists())
        self.assertEqual(changes_since(path, main)["all"], ["serit.txt"])

    def test_shared_dirs_linked_not_copied(self):
        write(self.p / "node_modules" / "paket" / "index.js", "module.exports = 1;\n")
        write(self.p / "venv" / "izlenen.txt")  # izlenen klasör: bağlanmaz (şeritte zaten var)
        git(["add", "-f", "venv/izlenen.txt"], self.p)
        commit_all(self.p, "izlenen venv")
        path = self.root / "abc-T01"
        gitops.worktree_add(self.p, path, self.BRANCH, head(self.p))
        self.assertEqual(gitops.link_shared(self.p, path), ["node_modules"])
        self.assertEqual(gitops.link_shared(self.p, path), [])  # zaten bağlı
        self.assertTrue(gitops._is_link(path / "node_modules"))
        self.assertFalse(gitops._is_link(path / "venv"))
        self.assertTrue((path / "node_modules" / "paket" / "index.js").exists())
        self.assertTrue(is_clean(path))  # bağlantı görevin değişikliği sayılmaz
        write(path / "a.txt")
        commit_all(path, "iş")
        self.assertEqual(git(["show", "--name-only", "--format=", "HEAD"], path).stdout.split(), ["a.txt"])
        gitops.worktree_remove(self.p, path, self.BRANCH)
        self.assertFalse(path.exists())
        self.assertTrue((self.p / "node_modules" / "paket" / "index.js").exists())  # asıl klasöre dokunulmadı


if __name__ == "__main__":
    unittest.main()
