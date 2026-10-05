"""jev/schemas/*.schema.json dosyalarını üretir (görev şeması tek kaynaktan)."""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "jev" / "schemas"


def obj(props: dict) -> dict:
    return {"type": "object", "additionalProperties": False, "properties": props, "required": list(props)}


S = {"type": "string"}
S_NULL = {"type": ["string", "null"]}
STRS = {"type": "array", "items": S}
BOOL = {"type": "boolean"}

# Görev kartı: işçi yalnızca kartı ve kartta adı geçen dosyaları okur. reads = okunacak dosyalar, outputs = yazılacak
# dosyalar, contracts = plandaki sözleşme kimlikleri (Jev tanımlarını karta olduğu gibi koyar).
TASK = obj({
    "id": S, "title": S, "module": S,
    "type": {"type": "string", "enum": ["code", "test", "docs", "config", "research"]},
    "description": S, "reads": STRS, "outputs": STRS, "contracts": STRS,
    "acceptance": STRS, "verify": STRS, "depends_on": STRS, "covers_criteria": STRS,
    "complexity": {"type": "string", "enum": ["S", "M", "L"]},
    "suggested_agent": {"type": ["string", "null"], "enum": ["sol", "sonnet", "luna", None]},
    "notes": S,
})
# Plan çağrısının işin ihtiyaçları için verdiği karar: ne gerekiyor, hangi görev karşılıyor (yoksa null)
NEED = obj({
    "kind": {"type": "string", "enum": ["research", "data", "website", "tool", "resource"]},
    "what": S, "task": S_NULL,
})
CONTRACT = obj({"id": S, "name": S, "definition": S})

SCHEMAS = {
    "plan": obj({
        "project_name": S, "slug": S, "summary": S, "plan_markdown": S,
        "stack": obj({"language": S, "frameworks": STRS, "runtime_notes": S}),
        "commands": obj({"install": S_NULL, "test": S_NULL, "run": S_NULL, "lint": S_NULL}),
        "modules": {"type": "array", "items": obj({
            "id": S, "name": S, "responsibility": S, "interfaces": S, "depends_on": STRS})},
        "success_criteria": {"type": "array", "items": obj({
            "id": S, "statement": S, "verification": S, "command": S_NULL})},
        "assumptions": STRS, "out_of_scope": STRS, "risks": STRS, "conventions": S,
        # Planlayıcı iş ölçülenden belirgin biçimde büyükse daha büyük seviyeyi yazar (yalnız yukarı)
        "scale": {"type": ["string", "null"], "enum": ["orta", "buyuk", None]},
        "needs": {"type": "array", "items": NEED},
        "contracts": {"type": "array", "items": CONTRACT},
        "tasks": {"type": "array", "items": TASK},
    }),
    "tasks": obj({"tasks": {"type": "array", "items": TASK}}),
    "worker_result": obj({
        "status": {"type": "string", "enum": ["done", "blocked", "failed"]},
        "summary": S, "changed_files": STRS,
        "verification": {"type": "array", "items": obj({"command": S, "passed": BOOL, "output_tail": S})},
        "blocker": S_NULL, "notes": S, "follow_ups": STRS,
    }),
    "jev_decision": obj({
        "decision": {"type": "string", "enum": ["retry", "reassign", "split", "revise", "skip", "pause", "abort"]},
        "agent": {"type": ["string", "null"], "enum": ["sol", "sonnet", "luna", "opus", None]},
        "effort": {"type": ["string", "null"], "enum": ["low", "medium", "high", "xhigh", "max", None]},
        "rollback": BOOL, "guidance": S,
        "revised_task": {"anyOf": [TASK, {"type": "null"}]},
        "new_tasks": {"type": "array", "items": TASK},
        "rationale": S,
        "deviation": {"type": "string", "enum": ["none", "minor", "major"]},
        "report_note": S,
    }),
    "review": obj({
        "verdict": {"type": "string", "enum": ["basarili", "kismen", "basarisiz"]},
        "summary": S, "report_markdown": S,
        "criteria": {"type": "array", "items": obj({
            "id": S, "status": {"type": "string", "enum": ["met", "partial", "unmet", "unverified"]},
            "evidence": S, "gap": S_NULL})},
        "gaps": {"type": "array", "items": obj({"id": S, "criteria": STRS, "description": S, "suggested_fix": S})},
        "risks": STRS, "next_steps": STRS,
    }),
}

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, schema in SCHEMAS.items():
        (OUT / f"{name}.schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("yazıldı:", name)
