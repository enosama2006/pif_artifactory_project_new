# -*- coding: utf-8 -*-
"""Full pipeline through the ADK stage functions (no ADK runtime needed):
docx bytes → ingest → inventory → scan → classify+rules → decide → assemble,
with a ScriptedLlm playing Groq (including one dropped leaf) and a second run
in pure StubLlm mode proving the agent works with no key at all.
"""
import asyncio
import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _adk import stages
from app.llm.client import StubLlm

W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _p(text, style=None):
    st = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{st}<w:r><w:t>{text}</w:t></w:r></w:p>"


DOC_XML = f"""<w:document {W_NS}><w:body>
{_p("سياسة أمن المعلومات", "Title")}
{_p("أعدّه: أحمد عبدالرحمن")}
{_p("1. الغرض", "Heading1")}
{_p("تحدد هذه السياسة التزامات صندوق الاستثمارات العامة.")}
{_p("2. الاعتماد", "Heading1")}
<w:tbl>
  <w:tr><w:tc>{_p("القرار")}</w:tc><w:tc>{_p("التاريخ")}</w:tc><w:tc>{_p("الموضوع")}</w:tc></w:tr>
  <w:tr><w:tc>{_p("قرار رقم 47")}</w:tc><w:tc>{_p("12/3/1445")}</w:tc><w:tc>{_p("سياسة أمن المعلومات")}</w:tc></w:tr>
</w:tbl>
</w:body></w:document>"""


def make_docx(tmp_path) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", DOC_XML)
        z.writestr("word/header1.xml", f"<w:hdr {W_NS}>{_p('صندوق الاستثمارات العامة')}</w:hdr>")
    p = tmp_path / "sample.docx"
    p.write_bytes(buf.getvalue())
    return str(p)


class ScriptedLlm(StubLlm):
    """Groq stand-in: real-looking inventory; decide drops one leaf on purpose."""
    is_stub = False

    def json_call(self, prompt, *, payload=None, **kw):
        task = (payload or {}).get("task", "")
        if task == "inventory":
            actors = []
            joined = " ".join(l["text"] for l in payload["leaves"])
            if "أحمد عبدالرحمن" in joined:
                actors.append({"name": "أحمد عبدالرحمن", "kind": "PERSON",
                               "role": "معدّ الوثيقة", "variants": ["أحمد عبدالرحمن"]})
            if "صندوق الاستثمارات العامة" in joined:
                actors.append({"name": "صندوق الاستثمارات العامة", "kind": "ORG_OWNER",
                               "role": "الجهة المُصدِرة",
                               "variants": ["صندوق الاستثمارات العامة", "الصندوق"]})
            if "سياسة أمن المعلومات" in joined:
                actors.append({"name": "سياسة أمن المعلومات", "kind": "INTERNAL_DOC",
                               "role": "الوثيقة نفسها", "variants": ["سياسة أمن المعلومات"]})
            return {"actors": actors}
        if task == "decide":
            out = {lf["id"]: {"decision": "REWRITE" if (lf["mentions"] or lf["cascade"]) else "KEEP",
                              "use": None, "reason": "scripted judgment"}
                   for lf in payload["leaves"]}
            out.pop(payload["leaves"][0]["id"])          # adversarial drop
            return {"decisions": out}
        return super().json_call(prompt, payload=payload, **kw)


def run_pipeline(path, llm):
    state = {"input_path": path}
    for fn in (stages.ingest_stage, stages.inventory_stage, stages.scan_stage,
               stages.classify_rules_stage, stages.decide_stage, stages.assemble_stage):
        result = asyncio.run(fn(state, llm))
        assert result.get("ok", True), f"{fn.__name__}: {result.get('message')}"
        state.update(result.get("delta", {}))
    return state


def test_full_run_scripted_llm(tmp_path):
    state = run_pipeline(make_docx(tmp_path), ScriptedLlm())
    m = state["metrics"]
    assert m["coverage"] == 1.0
    assert m["silent_losses"] == 0
    assert m["review"] >= 1                       # the dropped leaf surfaced as REVIEW

    header_leaf_ids = {l["leaf_id"] for l in state["leaves"] if l["kind"] == "page_header"}
    rewritten = {p["leaf_id"] for p in state["payload"]}
    assert header_leaf_ids & rewritten            # org name in the PAGE HEADER is rewritten

    cascade_ids = {c["leaf_id"] for c in state["cascade"]}
    assert len(cascade_ids) == 2                  # decision number + date followed doc name

    placeholders = {s["replace"] for p in state["payload"] for s in p["spans"]}
    allowed = {a["placeholder"] for a in state["actors"].values()} | \
              {c["placeholder"] for c in state["cascade"]}
    assert placeholders <= allowed                # nothing outside the locked dictionary


def test_full_run_stub_mode_no_key(tmp_path):
    """No GROQ_API_KEY at all: the agent still completes with full coverage."""
    state = run_pipeline(make_docx(tmp_path), StubLlm())
    assert state["metrics"]["coverage"] == 1.0
    assert state["metrics"]["silent_losses"] == 0
