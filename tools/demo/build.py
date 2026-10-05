"""Build the public, backend-free office demo from the real office assets.

Run from any directory: python tools/demo/build.py
Only the generated client has its transport replaced. The local authenticated
office and its access-key checks remain untouched.
"""
from pathlib import Path
import json
import shutil
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from jev.office import snapshot


def build():
    source = ROOT / "jev/office/static"
    dest = ROOT / "docs/demo"
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("office.css", "scene.js", "characters.js"):
        shutil.copyfile(source / name, dest / name)
    js = (source / "office.js").read_text(encoding="utf-8")
    replacements = {
        'new URLSearchParams(location.search).get("k") || ""': '"public-demo"',
        "await fetch(": "await window.JevDemo.fetch(",
        "new EventSource(": "new window.JevDemo.EventSource(",
    }
    for old, new in replacements.items():
        expected = 2 if old == "await fetch(" else 1
        if js.count(old) != expected:
            raise RuntimeError(f"Office transport changed: expected {expected} × {old!r}")
        js = js.replace(old, new)
    (dest / "office.js").write_text(js, encoding="utf-8", newline="\n")
    for name in ("demo.js", "demo.css"):
        shutil.copyfile(Path(__file__).parent / name, dest / name)
    raw = tomllib.loads((ROOT / "jev/varsayilan.toml").read_text(encoding="utf-8"))
    labels = {"status": snapshot.STATUS_TR, "phase": snapshot.PHASE_TR,
              "verdict": snapshot.VERDICT_TR, "criteria": snapshot.CRIT_TR,
              "outcome": snapshot.OUTCOME_TR, "decision": snapshot.DECISION_TR,
              "display": snapshot.DISPLAY, "role": snapshot.ROLE_TR,
              "call_phase": snapshot.CALL_PHASE_TR, "deviation": snapshot.DEVIATION_TR,
              "applied": snapshot.APPLIED_TR, "type": snapshot.TYPE_TR}
    agents = [{"name": name, "display": snapshot.DISPLAY.get(name, name.title()),
               "label": a["etiket"], "model": a["model"], "provider": a["saglayici"],
               "roles": a["roller"], "roles_tr": [snapshot.ROLE_TR[r] for r in a["roller"]]}
              for name, a in raw["ajanlar"].items()]
    agents.append({"name": "jev", "display": "Jev", "label": "Orkestratör",
                   "model": "jev-latest", "provider": "typesafe", "roles": ["orkestrator"],
                   "roles_tr": ["Orkestratör"], "brain": "Opus", "brain_display": "Opus", "fallback": "Sol"})
    (dest / "config.js").write_text("window.JevDemoConfig = " + json.dumps(
        {"labels": labels, "agents": agents, "repository_url": "https://github.com/nowackk-cp/jev"}, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")
    html = (source / "index.html").read_text(encoding="utf-8")
    for name in ("office.css", "scene.js", "characters.js", "office.js"):
        html = html.replace(f'"/{name}"', f'"./demo/{name}"')
    html = html.replace('<title>Jev Ofis</title>', '<title>Jev · İnteraktif ofis demosu</title>\n'
                        '<meta name="description" content="Jev çok ajanlı yazılım ofisini deneyin: animasyonlu ajanlar, görev panosu, kararlar ve raporlar.">')
    html = html.replace('<script src="./demo/office.js" defer></script>',
                        '<link rel="stylesheet" href="./demo/demo.css">\n'
                        '<script src="./demo/config.js" defer></script>\n'
                        '<script src="./demo/demo.js" defer></script>\n'
                        '<script src="./demo/office.js" defer></script>')
    bar = '''
<section class="demo-bar" aria-label="Demo kontrolleri">
  <div class="demo-intro"><span class="demo-eyebrow">JEV / İNTERAKTİF DEMO</span>
    <h1>Bir istek. Bir ekip. Canlı bir ofis.</h1>
    <p>Ajanlara ve görev kartlarına tıkla; planı, kararları ve raporu incele.</p>
    <small>Örnek senaryo · Gerçek model çağrısı yapılmaz, dosya yazılmaz.</small>
  </div>
  <div class="demo-actions">
    <label>Senaryo<select id="demo-scenario">
      <option value="parallel">Paralel ekip · birim dönüştürücü</option>
      <option value="retry">Hata ve yeniden deneme · görev listesi</option>
      <option value="mini">Mini iş · hesap makinesi</option>
    </select></label>
    <div class="demo-buttons">
      <button id="demo-pause" type="button" class="btn">Duraklat</button>
      <button id="demo-restart" type="button" class="btn">Baştan oynat</button>
      <label class="demo-speed">Hız<select id="demo-speed" aria-label="Oynatma hızı">
        <option value="1">1×</option><option value="2">2×</option><option value="4">4×</option>
      </select></label>
      <a id="demo-github" class="btn demo-github" href="https://github.com" target="_blank" rel="noopener noreferrer">GitHub ↗</a>
    </div>
    <div class="demo-timeline"><span id="demo-step" role="status">Senaryo hazırlanıyor…</span>
      <progress id="demo-progress" max="1" value="0" aria-label="Demo ilerlemesi"></progress>
    </div>
  </div>
</section>
'''
    html = html.replace("<body>", "<body>\n" + bar)
    (ROOT / "docs/index.html").write_text(html, encoding="utf-8", newline="\n")
    (ROOT / "docs/.nojekyll").touch()
    print("Built docs/index.html and docs/demo/ from the real office UI.")


if __name__ == "__main__":
    build()
