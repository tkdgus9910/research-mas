#!/usr/bin/env python3
"""기존 스크립트(.py)·노트북(.ipynb)에서 모듈 분할 지점(seam)을 찾는다.

    python seam_finder.py analysis.py            # 보고서를 stdout 으로
    python seam_finder.py analysis.ipynb -o seams.md --json seams.json

원리: 스테이지 경계는 '기능'이 아니라 '파일 I/O 지점'이다. 파일을 읽거나 쓰는 줄, 외부 API·LLM 을
부르는 줄이 자연스러운 이음새다. 각 문장을 유형(collect/clean/extract/analyze/report)으로 라벨링하고,
같은 라벨이 이어지는 구간을 하나의 스테이지 후보로 묶은 뒤, 구간 사이를 오가는 변수(=파일 계약이 될
것)와 외부화 대상(상수·경로·프롬프트·하이퍼파라미터)을 뽑는다.

출력(마크다운): 구간표 · 구간 간 데이터 흐름 · configs/prompts 후보 · 헬퍼 함수 · 주의사항.
이 보고서는 '제안'이다 — 최종 스테이지 매핑은 사람이 확정한다.
"""
from __future__ import annotations

import argparse
import ast
import builtins
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- 패턴 (소스 텍스트 기준)
PATS = {
    "write": re.compile(r"\.(to_csv|to_parquet|to_excel|to_json|to_pickle|to_feather|to_sql|to_hdf)\(|json\.dump\(|pickle\.dump\(|np\.save|savefig\(|write_image\(|write_html\(|\.save\(|open\([^)]*['\"][wa]b?['\"]"),
    "read": re.compile(r"\bpd\.read_\w+\(|\.read_\w+\(|json\.load\(|pickle\.load\(|np\.load\(|load_dataset\(|open\([^)]*\)|glob\.glob\(|\.rglob\(|Path\([^)]*\)\.read_text"),
    "llm": re.compile(r"chat\.completions\.create|messages\.create|responses\.create|generate_content\(|ChatCompletion|\.invoke\(|\.ainvoke\(|\bopenai\b|\banthropic\b|\bgenai\b|langchain|\bollama\b|AutoModel|pipeline\(\s*['\"]|SentenceTransformer|\.encode\("),
    "api": re.compile(r"requests\.(get|post)\(|httpx\.|urlopen\(|\bkipris\b|openalex|scopus|pyalex|arxiv\.|crossref|Entrez|Elsevier|serpapi|BeautifulSoup|selenium", re.I),
    "plot": re.compile(r"\bplt\.|\bsns\.|\bpx\.|go\.Figure|\.plot\(|\.plot\.|altair|bokeh|WordCloud|\.hist\(|\.barh?\("),
    "analyze": re.compile(r"KMeans|HDBSCAN|DBSCAN|Agglomerative|LatentDirichlet|BERTopic|Tfidf|CountVectorizer|cosine_similarity|\bPCA\b|UMAP|TSNE|networkx|\bnx\.|\.groupby\(|pivot_table|\.corr\(|statsmodels|scipy|linregress|value_counts\(|crosstab|\.fit\(|\.predict\(|\.transform\(|Counter\("),
    "clean": re.compile(r"drop_duplicates|dropna|fillna|\.str\.|\.rename\(|\.astype\(|\.merge\(|pd\.concat\(|\.query\(|\.isin\(|\.apply\(|\.map\(|\.replace\(|\.strip\(|\.lower\(|re\.sub\(|\.explode\(|\.split\("),
}
PATH_LIKE = re.compile(r"(^|[\s'\"])((\.{0,2}/|[A-Za-z]:\\|~/)?[\w\-./\\가-힣 ]+\.(csv|xlsx?|parquet|jsonl?|pkl|txt|tsv|png|pdf|feather|hwpx?|db))(['\"]|$)", re.I)
PROMPT_HINT = re.compile(r"\b(you are|extract|return (only )?json|classify|summarize|다음|추출|분류|요약|JSON 형식|출력 형식)\b", re.I)
HYPER_KEYS = {"n_clusters", "n_components", "min_cluster_size", "min_samples", "temperature", "max_tokens", "model",
              "random_state", "n_neighbors", "threshold", "top_k", "k", "n_topics", "eps", "max_features", "ngram_range",
              "min_df", "max_df", "alpha", "test_size", "n_estimators", "batch_size", "max_workers", "limit", "seed"}
LABEL_ORDER = ["collect", "clean", "extract", "analyze", "report"]
BUILTINS = set(dir(builtins))


# ---------------------------------------------------------------- 입력 로딩
def load_source(path: Path) -> tuple[str, list[int | None]]:
    """소스 텍스트와 줄→셀 번호 매핑. .py 는 셀 없음(None). 매직/셸 줄은 주석 처리."""
    if path.suffix == ".ipynb":
        nb = json.loads(path.read_text(encoding="utf-8"))
        lines, cells = [], []
        for ci, cell in enumerate(c for c in nb.get("cells", []) if c.get("cell_type") == "code"):
            src = cell.get("source") or []
            src = "".join(src) if isinstance(src, list) else src
            for ln in src.splitlines():
                s = ln.lstrip()
                if s.startswith(("%", "!", "?")) or s.endswith("?"):
                    ln = "# [magic] " + ln
                lines.append(ln)
                cells.append(ci + 1)
            lines.append("")
            cells.append(ci + 1)
        return "\n".join(lines), cells
    text = path.read_text(encoding="utf-8")
    return text, [None] * (text.count("\n") + 2)


# ---------------------------------------------------------------- 문장 분석
@dataclass
class Stmt:
    idx: int
    lineno: int
    end_lineno: int
    kind: str          # import | def | const | code
    label: str         # collect/clean/extract/analyze/report/other (code 만)
    hits: list[str]
    stores: set[str] = field(default_factory=set)
    loads: set[str] = field(default_factory=set)
    src: str = ""
    reads: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)


def names(node: ast.AST) -> tuple[set[str], set[str]]:
    stores, loads = set(), set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            (stores if isinstance(n.ctx, (ast.Store, ast.Del)) else loads).add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            stores.add(n.name)
        elif isinstance(n, ast.Import):
            stores.update((a.asname or a.name).split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            stores.update(a.asname or a.name for a in n.names)
        elif isinstance(n, ast.arg):
            stores.add(n.arg)
        elif isinstance(n, ast.comprehension):
            s, _ = names(n.target)
            stores.update(s)
    return stores, loads


def is_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(is_literal(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all(k is not None and is_literal(k) and is_literal(v) for k, v in zip(node.keys, node.values))
    if isinstance(node, ast.UnaryOp) and isinstance(node.operand, ast.Constant):
        return True
    return False


def classify(src: str) -> tuple[str, list[str]]:
    hits = [k for k, p in PATS.items() if p.search(src)]
    # 우선순위: 쓰기/그림 → 보고 쪽, LLM → extract, api/read → collect, analyze, clean
    if "llm" in hits:
        return "extract", hits
    if "plot" in hits:
        return "report", hits
    if "write" in hits and not ({"analyze", "clean"} & set(hits)):
        return "report" if re.search(r"xlsx|png|pdf|html|savefig|write_image", src) else "write", hits
    if "api" in hits:
        return "collect", hits
    if "read" in hits and not ({"analyze", "clean"} & set(hits)):
        return "collect", hits
    if "analyze" in hits:
        return "analyze", hits
    if "clean" in hits:
        return "clean", hits
    if "write" in hits:
        return "write", hits
    return "other", hits


def io_targets(node: ast.AST) -> tuple[list[str], list[str]]:
    """문장 안의 읽기/쓰기 호출의 첫 인자(경로 표현식)를 뽑는다 — 변수명·f-string 그대로 보여준다."""
    reads, writes = [], []
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        fn = n.func
        attr = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
        arg = None
        if n.args:
            arg = ast.unparse(n.args[0])
        elif n.keywords:
            kw = next((k for k in n.keywords if k.arg in ("path", "path_or_buf", "fname", "filename", "file")), None)
            arg = ast.unparse(kw.value) if kw else None
        if arg is None:
            continue
        arg = arg[:60]
        if attr.startswith("read_") or attr in ("load", "read_text", "read_bytes", "glob", "rglob") or (
                attr == "open" and not re.search(r"['\"][wa]", ast.unparse(n))):
            reads.append(arg)
        elif attr.startswith("to_") or attr in ("savefig", "dump", "save", "write_text", "write_image", "write_html") or (
                attr == "open" and re.search(r"['\"][wa]", ast.unparse(n))):
            writes.append(arg)
    return reads, writes


def analyze_statements(tree: ast.Module, text: str) -> list[Stmt]:
    lines = text.splitlines()
    out = []
    def_labels: dict[str, str] = {}   # 함수명 → 본문 라벨 (호출부에 전파)
    for i, node in enumerate(tree.body):
        src = "\n".join(lines[node.lineno - 1: node.end_lineno])
        st = Stmt(i, node.lineno, node.end_lineno, "code", "other", [])
        st.src = src
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            st.kind = "import"
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            st.kind = "def"
            lab, hits = classify(src)
            def_labels[node.name] = lab
            st.hits = hits
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None and is_literal(node.value) \
                and not (isinstance(node.value, (ast.List, ast.Dict, ast.Set, ast.Tuple)) and not getattr(node.value, "elts", getattr(node.value, "keys", None))):
            st.kind = "const"
        else:
            st.label, st.hits = classify(src)
        st.stores, st.loads = names(node)
        if st.kind == "def":   # 함수 내부 변수는 외부 흐름에 안 섞이게: 이름만 남긴다
            st.stores, st.loads = {node.name}, set()
        if st.kind == "code":
            # 호출한 사용자 함수의 라벨을 전파 (extract > collect > analyze 우선)
            called = [def_labels[n] for n in st.loads if n in def_labels]
            for strong in ("extract", "collect"):
                if strong in called and st.label in ("other", "clean", "analyze", "write"):
                    st.label = strong
                    st.hits = st.hits + [f"call:{strong}"]
                    break
            r, w = io_targets(node)
            st.reads, st.writes = r, w
        out.append(st)
    # 첫 I/O·분석 이전의 '아무 패턴도 없는' 문장은 전역 설정(setup)으로 분리
    for st in out:
        if st.kind == "code" and st.label == "other" and not st.hits:
            st.kind = "setup"
        elif st.kind == "code":
            break
    return out


# ---------------------------------------------------------------- 구간화
@dataclass
class Segment:
    no: int
    label: str
    start: int
    end: int
    stmt_idx: list[int]
    hits: Counter = field(default_factory=Counter)
    inputs: list[str] = field(default_factory=list)    # 이전 구간에서 받는 변수
    outputs: list[str] = field(default_factory=list)   # 이후 구간이 쓰는 변수
    reads: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    cells: str = ""


RANK = {l: i for i, l in enumerate(LABEL_ORDER)}


def smooth(labels: list[str]) -> list[str]:
    """코드는 보통 collect→…→report 순으로 흐른다. 짧은 역행(≤3문장)은 현재 스테이지의 준비 코드로 본다."""
    labs = list(labels)
    # 런(run) 단위로 본다
    runs: list[list[int]] = []
    for i, l in enumerate(labs):
        if runs and labs[runs[-1][0]] == l:
            runs[-1].append(i)
        else:
            runs.append([i])
    changed = True
    while changed:
        changed = False
        for k in range(1, len(runs)):
            cur, prev = labs[runs[k][0]], labs[runs[k - 1][0]]
            if RANK[cur] < RANK[prev] and len(runs[k]) <= 3:
                for i in runs[k]:
                    labs[i] = prev
                changed = True
                break
            # 같은 라벨 사이에 낀 짧은 섬(디버그용 plot, 룩업 테이블 읽기 등)도 흡수 — 단 LLM 호출은 항상 독립 스테이지
            if k + 1 < len(runs) and labs[runs[k + 1][0]] == prev and len(runs[k]) <= 2 and cur != "extract":
                for i in runs[k]:
                    labs[i] = prev
                changed = True
                break
        if changed:
            runs = []
            for i, l in enumerate(labs):
                if runs and labs[runs[-1][0]] == l:
                    runs[-1].append(i)
                else:
                    runs.append([i])
    return labs


def segment(stmts: list[Stmt], cells: list[int | None]) -> list[Segment]:
    code = [s for s in stmts if s.kind == "code"]
    cur = None
    labels = []
    for s in code:
        lab = s.label
        if lab in ("other", "write"):      # 패턴 없는 문장·단순 저장은 직전 스테이지에 속한다
            lab = cur or "collect"
        labels.append(lab)
        cur = lab
    labels = smooth(labels)
    segs: list[Segment] = []
    for s, lab in zip(code, labels):
        if segs and segs[-1].label == lab:
            segs[-1].end = s.end_lineno
            segs[-1].stmt_idx.append(s.idx)
        else:
            segs.append(Segment(len(segs) + 1, lab, s.lineno, s.end_lineno, [s.idx]))
        segs[-1].hits.update(s.hits)
        for r in s.reads:
            if r not in segs[-1].reads:
                segs[-1].reads.append(r)
        for w in s.writes:
            if w not in segs[-1].writes:
                segs[-1].writes.append(w)
    # 변수 흐름
    by_idx = {s.idx: s for s in stmts}
    seg_stores = [set().union(*(by_idx[i].stores for i in sg.stmt_idx)) for sg in segs]
    seg_loads = [set().union(*(by_idx[i].loads for i in sg.stmt_idx)) for sg in segs]
    for k, sg in enumerate(segs):
        before = set().union(*seg_stores[:k], set())
        after = set().union(*seg_loads[k + 1:], set())
        sg.inputs = sorted((seg_loads[k] - seg_stores[k]) & before - BUILTINS)
        sg.outputs = sorted(seg_stores[k] & after - BUILTINS)
        if cells[0] is not None:
            cs = sorted({cells[min(ln - 1, len(cells) - 1)] for ln in range(sg.start, sg.end + 1)})
            sg.cells = f"cell {cs[0]}" if len(cs) == 1 else f"cell {cs[0]}–{cs[-1]}"
    return segs


# ---------------------------------------------------------------- 외부화 후보
def externalize(stmts: list[Stmt], tree: ast.Module, text: str) -> dict:
    lines = text.splitlines()
    consts, prompts, paths, hypers = [], [], [], []
    for s in stmts:
        if s.kind == "const":
            node = tree.body[s.idx]
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            name = ", ".join(ast.unparse(t) for t in targets)
            val = ast.unparse(node.value)
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str) and (
                    len(node.value.value) > 150 or PROMPT_HINT.search(node.value.value)):
                prompts.append({"name": name, "line": s.lineno, "chars": len(node.value.value)})
            elif PATH_LIKE.search(val):
                paths.append({"name": name, "line": s.lineno, "value": val[:80]})
            else:
                consts.append({"name": name, "line": s.lineno, "value": val[:80]})
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in HYPER_KEYS and isinstance(kw.value, ast.Constant):
                    fn = ast.unparse(node.func)
                    hypers.append({"call": fn.split(".")[-1], "arg": kw.arg, "value": repr(kw.value.value), "line": node.lineno})
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) > 150 and PROMPT_HINT.search(node.value):
            if not any(p["line"] == node.lineno for p in prompts):
                prompts.append({"name": "(inline)", "line": node.lineno, "chars": len(node.value)})
        if isinstance(node, ast.JoinedStr):   # f-string 프롬프트
            lit = "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
            if len(lit) > 120 and PROMPT_HINT.search(lit) and not any(p["line"] == node.lineno for p in prompts):
                prompts.append({"name": "(f-string)", "line": node.lineno, "chars": len(lit)})
    inline_paths = []
    for i, ln in enumerate(lines, 1):
        for m in PATH_LIKE.finditer(ln):
            if not any(p["line"] == i for p in paths):
                inline_paths.append({"line": i, "value": m.group(2)})
    return {"constants": consts, "paths": paths, "inline_paths": inline_paths, "prompts": prompts, "hyperparams": hypers}


# ---------------------------------------------------------------- 주의사항
def warnings_for(stmts: list[Stmt], segs: list[Segment], text: str, is_nb: bool) -> list[str]:
    w = []
    defined: set[str] = set(BUILTINS)
    for s in stmts:
        undefined = {n for n in s.loads if n not in defined and n not in s.stores and not n.startswith("_")} if s.kind == "code" else set()
        undefined -= {"self", "cls"}
        if undefined and is_nb:
            w.append(f"줄 {s.lineno}: 정의 전에 사용된 이름 {sorted(undefined)[:4]} — 셀 실행 순서 의존 가능성(위쪽 셀을 나중에 실행했을 수 있음)")
        elif undefined:
            w.append(f"줄 {s.lineno}: 정의 전에 사용된 이름 {sorted(undefined)[:4]}")
        defined |= s.stores
    if re.search(r"\b(KMeans|UMAP|TSNE|HDBSCAN|train_test_split|sample\(|shuffle)", text) and not re.search(r"random_state|seed", text):
        w.append("확률적 알고리즘 사용 중 random_state/seed 미지정 — 동일성 검증 전에 시드를 고정할 것")
    if re.search(r"(chat\.completions|messages\.create|\.invoke\()", text) and not re.search(r"cache|lru_cache|pickle", text):
        w.append("LLM 호출에 캐시 없음 — 리팩터링 후 재실행 시 전량 재과금. 먼저 원본 응답을 저장하고 cache/llm 로 이관할 것")
    if re.search(r"temperature\s*=\s*0?\.[1-9]|temperature\s*=\s*[1-9]", text):
        w.append("temperature>0 — 응답이 비결정적이므로 출력 동일성은 캐시된 응답 기준으로만 검증 가능")
    globals_used = Counter()
    for s in stmts:
        if s.kind == "def":
            node = next((n for n in ast.walk(ast.parse(s.src)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
            if node:
                params = {a.arg for a in node.args.args + node.args.kwonlyargs}
                _, loads = names(node)
                consts = {c.stores.copy().pop() for c in stmts if c.kind == "const" and c.stores}
                for g in sorted((loads & consts) - params):
                    globals_used[(node.name, g)] += 1
    for (fn, g) in list(globals_used)[:6]:
        w.append(f"함수 {fn}() 가 전역 상수 {g} 를 직접 참조 — 인자(cfg/params)로 받도록 바꿔야 스테이지로 옮길 수 있음")
    if len(segs) <= 1:
        w.append("구간이 1개뿐 — 파일 I/O 가 거의 없는 코드. 함수 단위로 먼저 쪼개고 중간 산출을 파일로 내보낸 뒤 다시 실행할 것")
    return w


# ---------------------------------------------------------------- 보고서
def suggest_stage_names(segs: list[Segment]) -> list[str]:
    cnt = Counter()
    out = []
    for sg in segs:
        cnt[sg.label] += 1
        out.append(f"{sg.label}" + (f"_{cnt[sg.label]}" if cnt[sg.label] > 1 else ""))
    return out


def report_md(path: Path, stmts: list[Stmt], segs: list[Segment], ext: dict, warns: list[str]) -> str:
    code = [s for s in stmts if s.kind == "code"]
    defs = [s for s in stmts if s.kind == "def"]
    names_ = suggest_stage_names(segs)
    setup = [s for s in stmts if s.kind == "setup"]
    L = [f"# Seam report — `{path.name}`", "",
         f"- 최상위 문장 {len(stmts)} (코드 {len(code)} · 함수/클래스 {len(defs)} · 상수 {len([s for s in stmts if s.kind=='const'])} · import {len([s for s in stmts if s.kind=='import'])} · 전역 설정 {len(setup)})",
         f"- 제안 구간 {len(segs)}개 → 스테이지 후보: " + " → ".join(f"`s{i+1}_{n}`" for i, n in enumerate(names_)), ""]
    L += ["## 1. 구간표 (스테이지 후보)", "",
          "| # | 후보 | 줄" + (" (셀)" if segs and segs[0].cells else "") + " | 근거(패턴) | 입력 변수 ← 이전 | 출력 변수 → 이후 | 파일 읽기 | 파일 쓰기 |",
          "|---|---|---|---|---|---|---|---|"]
    for sg, nm in zip(segs, names_):
        loc = f"{sg.start}–{sg.end}" + (f" ({sg.cells})" if sg.cells else "")
        hits = ", ".join(f"{k}×{v}" for k, v in sg.hits.most_common(3)) or "-"
        L.append(f"| {sg.no} | `{nm}` | {loc} | {hits} | {', '.join(sg.inputs) or '-'} | {', '.join(sg.outputs) or '-'} | {', '.join(sg.reads) or '-'} | {', '.join(sg.writes) or '-'} |")
    L += ["", "> 해석: '출력 변수 → 이후' 가 그 스테이지의 **artifact(파일 계약)** 후보다. 여러 변수가 넘어가면 하나의 표로 합치거나 스테이지를 더 쪼갠다.", ""]
    L += ["## 2. 외부화 후보", ""]
    if ext["paths"] or ext["inline_paths"]:
        L += ["### 경로 리터럴 → `configs: paths/artifacts`", ""]
        L += [f"- 줄 {p['line']}: `{p['name']} = {p['value']}`" for p in ext["paths"]]
        L += [f"- 줄 {p['line']}: `{p['value']}` (인라인)" for p in ext["inline_paths"][:15]]
        L.append("")
    if ext["constants"]:
        L += ["### 상수 → `configs: params.<stage>`", ""] + [f"- 줄 {c['line']}: `{c['name']} = {c['value']}`" for c in ext["constants"]] + [""]
    if ext["hyperparams"]:
        L += ["### 호출 인자에 박힌 하이퍼파라미터 → `configs: params.<stage>` / `llm`", ""]
        L += [f"- 줄 {h['line']}: `{h['call']}(... {h['arg']}={h['value']})`" for h in ext["hyperparams"]] + [""]
    if ext["prompts"]:
        L += ["### 프롬프트 → `prompts/<name>_v1.md`", ""] + [f"- 줄 {p['line']}: `{p['name']}` ({p['chars']}자)" for p in ext["prompts"]] + [""]
    if defs:
        L += ["## 3. 헬퍼 함수·클래스 배치", ""]
        for d in defs:
            users = [sg.no for sg in segs if any(d.stores & stmts[i].loads for i in sg.stmt_idx)]
            where = (f"구간 {users[0]} 의 스테이지 폴더" if len(users) == 1 else "`src/common/` (여러 구간에서 사용)") if users else "미사용 — 삭제 후보"
            L.append(f"- `{next(iter(d.stores))}` (줄 {d.lineno}) → {where}")
        L.append("")
    L += ["## 4. 주의사항", ""] + ([f"- {w}" for w in warns] or ["- 없음"]) + [""]
    L += ["## 5. 다음 단계", "",
          "1. 위 구간표를 보고 스테이지 수·이름을 확정한다 (보통 3–5개; 1–2문장짜리 구간은 이웃에 합친다).",
          "2. `scaffold.py <project> --stages <확정 목록>` 으로 골격을 만든다.",
          "3. 구간별 코드를 해당 `stage.py` 의 핵심 로직 자리로 옮기고, '입력 변수'는 `read_table(input_of(...))`, '출력 변수'는 `write_table(..., artifact_path(...))` 로 바꾼다.",
          "4. 2절의 후보를 `configs/`·`prompts/` 로 옮긴다. 함수 안에서 전역 상수를 참조하던 곳은 `p['키']` 로.",
          "5. 원본을 한 번 실행해 기준 출력을 확보한 뒤 `verify_equivalence.py` 로 새 파이프라인 출력과 비교한다."]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source")
    ap.add_argument("-o", "--out", help="마크다운 보고서 경로 (기본: stdout)")
    ap.add_argument("--json", help="구간·후보를 JSON 으로도 저장")
    a = ap.parse_args()

    path = Path(a.source)
    text, cells = load_source(path)
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        sys.exit(f"구문 오류로 분석 불가 (줄 {e.lineno}): {e.msg} — 먼저 실행 가능한 상태로 만든 뒤 다시 시도")
    stmts = analyze_statements(tree, text)
    segs = segment(stmts, cells)
    ext = externalize(stmts, tree, text)
    warns = warnings_for(stmts, segs, text, path.suffix == ".ipynb")
    md = report_md(path, stmts, segs, ext, warns)
    if a.out:
        Path(a.out).write_text(md, encoding="utf-8")
        print(f"보고서 → {a.out}")
    else:
        print(md)
    if a.json:
        payload = {"source": str(path),
                   "segments": [{"no": sg.no, "label": sg.label, "start": sg.start, "end": sg.end, "cells": sg.cells,
                                 "hits": dict(sg.hits), "inputs": sg.inputs, "outputs": sg.outputs,
                                 "reads": sg.reads, "writes": sg.writes} for sg in segs],
                   "externalize": ext, "warnings": warns, "suggested_stages": suggest_stage_names(segs)}
        Path(a.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON → {a.json}")


if __name__ == "__main__":
    main()
