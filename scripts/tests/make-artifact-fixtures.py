"""Generate the M22 artifact fixture SPECS and their ground truth.

Usage (from services/api): uv run python ../../scripts/tests/make-artifact-fixtures.py ../../services/api/tests/fixtures/artifacts

A spec is what the Artifact Factory renders FROM (docs/M22_ARTIFACT_FACTORY_SPEC.md §1); the
truth lists, per spec and format, the references an independent reader must find after the
render (M20's reference scheme: ``p<n>``, ``h<level>:<title>``, ``sheet:<name>!<cell>``, ``s<n>``,
``r<n>``, ``$.<key>``) and the numbers the owner "said", which the render may never exceed.
Deterministic: fixed content, LF, sorted keys.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BUDGET_ROWS = [["Kira", 12000], ["Maaş", 45000], ["Yazılım", 8000]]

SPECS: dict[str, dict] = {
    "butce-tablosu": {
        "kind": "spreadsheet",
        "title": "Bütçe 2026",
        "language": "tr",
        "sheets": [
            {
                "name": "Ozet",
                "columns": ["Kalem", "Tutar"],
                "rows": BUDGET_ROWS,
                "totals": {"Tutar": "sum"},
                "formulas": {"B6": "=B5*0.2", "A6": "KDV"},
            }
        ],
        "spoken_numbers": [12000, 45000, 8000],
    },
    "toplanti-notlari": {
        "kind": "document",
        "title": "Toplantı Notları",
        "language": "tr",
        "sections": [
            {"heading": "Giriş", "level": 1, "paragraphs": ["8 Eylül 2026 toplantısının notları."]},
            {"heading": "Kararlar", "level": 2, "paragraphs": ["Bütçe onaylandı.", "Sürüm takvimi bir hafta öne alındı."], "bullets": ["Kira 12000", "Maaş 45000"]},
            {"heading": "Sorumlular", "level": 2, "table": {"columns": ["Kişi", "Görev"], "rows": [["Ali", "Plan"], ["Ayşe", "Bütçe"]]}},
            {"heading": "Riskler", "level": 2, "paragraphs": ["Tedarikçi bağımlılığı."]},
            {"heading": "Sonuç", "level": 1, "paragraphs": ["Bir sonraki toplantı 15 Eylül."]},
        ],
        "spoken_numbers": [12000, 45000, 8, 15, 2026],
    },
    "q3-sunum": {
        "kind": "presentation",
        "title": "Q3 Özeti",
        "language": "tr",
        "slides": [
            {"title": "Giriş", "bullets": ["Üç yeni müşteri", "Gelir hedefi aşıldı"]},
            {"title": "Bulgular", "bullets": ["Bulut maliyeti yüzde 8 arttı", "Dönüşüm oranı yüzde 12"]},
            {"title": "Sonuç", "bullets": ["İki ürün lansmanı", "Destek süresi yarıya"]},
        ],
        "spoken_numbers": [8, 12],
    },
    "musteri-listesi": {
        "kind": "dataset",
        "title": "Müşteri Listesi",
        "language": "tr",
        "columns": ["Ad", "Şehir", "Tutar"],
        "rows": [["Ayşe", "Ankara", 1200], ["Mehmet", "İstanbul", 980], ["Zeynep", "Bursa", 1500]],
        "spoken_numbers": [1200, 980, 1500],
    },
    "hosgeldin-sayfasi": {
        "kind": "page",
        "title": "Hoş geldin",
        "language": "tr",
        "sections": [
            {"heading": "Hoş geldin", "level": 1, "paragraphs": ["Bu sayfa Artifact Factory tarafından üretildi."]},
            {"heading": "Ne yapabilirim?", "level": 2, "bullets": ["Belge", "Tablo", "Sunum"]},
        ],
        "spoken_numbers": [],
    },
}

FORMATS = {
    "spreadsheet": ["xlsx", "csv"],
    "document": ["docx", "pdf", "html", "md", "txt"],
    "presentation": ["pptx"],
    "dataset": ["csv", "json"],
    "page": ["html", "md"],
}


def truth_for(name: str, spec: dict) -> dict:
    kind = spec["kind"]
    expect: dict[str, list[dict]] = {}
    if kind == "spreadsheet":
        sheet = spec["sheets"][0]
        total = sum(r[1] for r in sheet["rows"])
        xlsx = [{"ref": f"sheet:{sheet['name']}!A1", "text": "Kalem"}, {"ref": f"sheet:{sheet['name']}!B1", "text": "Tutar"}]
        for i, (k, v) in enumerate(sheet["rows"], start=2):
            xlsx += [{"ref": f"sheet:{sheet['name']}!A{i}", "text": k}, {"ref": f"sheet:{sheet['name']}!B{i}", "value": v}]
        xlsx += [{"ref": f"sheet:{sheet['name']}!A5", "text": "Toplam"}, {"ref": f"sheet:{sheet['name']}!B5", "value": total},
                 {"ref": f"sheet:{sheet['name']}!B6", "formula": "=B5*0.2"}]
        expect["xlsx"] = xlsx
        expect["csv"] = [{"ref": "r1", "contains": "Kalem"}] + [{"ref": f"r{i}", "contains": str(v)} for i, (_, v) in enumerate(sheet["rows"], start=2)] + [{"ref": "r5", "contains": str(total)}]
    elif kind in ("document", "page"):
        heads = [{"ref": f"h{s['level']}:{s['heading']}", "text": s["heading"]} for s in spec["sections"]]
        paras = [{"contains": p} for s in spec["sections"] for p in s.get("paragraphs", [])]
        for fmt in FORMATS[kind]:
            expect[fmt] = heads + paras
    elif kind == "presentation":
        expect["pptx"] = [{"ref": f"s{i}", "title": s["title"], "contains": s["bullets"]} for i, s in enumerate(spec["slides"], start=1)]
        expect["pptx"].append({"structure": {"slide_count": len(spec["slides"])}})
    elif kind == "dataset":
        expect["csv"] = [{"ref": "r1", "contains": ",".join(spec["columns"])}] + [{"ref": f"r{i}", "contains": str(r[-1])} for i, r in enumerate(spec["rows"], start=2)]
        expect["json"] = [{"ref": "$.columns", "equals": spec["columns"]}, {"ref": "$.rows", "count": len(spec["rows"])}]
    return {"kind": kind, "title": spec["title"], "formats": FORMATS[kind], "expect": expect, "spoken_numbers": spec["spoken_numbers"]}


def main(out: Path) -> None:
    specs = out / "specs"
    specs.mkdir(parents=True, exist_ok=True)
    truth = {"generated_by": "scripts/tests/make-artifact-fixtures.py", "specs": {}}
    for name, spec in SPECS.items():
        (specs / f"{name}.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        truth["specs"][name] = truth_for(name, spec)
    (out / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {len(SPECS)} specs to {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
