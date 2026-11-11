"""스키마 선언·검증 — 스테이지 경계에서 데이터 모양을 확인한다.

검증을 두는 이유: 상류에서 컬럼명 하나가 바뀌면 하류는 KeyError 로 죽는 게 아니라
NaN 이 조용히 번지며 통계가 틀어진다. 경계에서 바로 잡아야 디버깅 범위가 한 스테이지로 좁혀진다.

schemas/<stage>.out.yaml 형식:
    name: s2_clean.out
    description: 중복 제거된 특허 레코드
    primary_key: [patent_id]
    min_rows: 1
    columns:
      patent_id: {dtype: string, required: true, unique: true}
      year:      {dtype: int,    required: true, min: 1990, max: 2030}
      ipc_main:  {dtype: string, required: false, pattern: "^[A-H]\\d{2}"}
      source:    {dtype: string, allowed: [KIPRIS, USPTO, EPO]}
    extra_columns: allow      # allow | warn | forbid

CLI: python -m src.common.schema infer data/interim/x.parquet [--name s2_clean.out] > schemas/s2_clean.out.yaml
"""
from __future__ import annotations

import re
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .config import path_of

_DTYPE_CHECK = {
    "string": lambda s: s.dropna().map(lambda x: isinstance(x, str)).all(),
    "int": lambda s: pd.api.types.is_integer_dtype(s.dropna()) or s.dropna().map(float.is_integer).all(),
    "float": lambda s: pd.api.types.is_numeric_dtype(s),
    "bool": lambda s: pd.api.types.is_bool_dtype(s) or set(s.dropna().unique()) <= {0, 1, True, False},
    "datetime": lambda s: pd.api.types.is_datetime64_any_dtype(s) or pd.to_datetime(s, errors="coerce").notna().sum() == s.notna().sum(),
    "list": lambda s: s.dropna().map(lambda x: isinstance(x, (list, tuple, np.ndarray))).all(),
    "any": lambda s: True,
}


class SchemaError(ValueError):
    pass


def load_schema(cfg: dict, name: str) -> dict | None:
    """schemas/<name>.yaml → dict. 파일이 없으면 None (아직 선언 전 단계)."""
    p = path_of(cfg, "schemas") / f"{name}.yaml"
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def validate(df: pd.DataFrame, schema: dict | None, where: str = "") -> pd.DataFrame:
    """위반이 있으면 SchemaError 로 전부 모아서 던진다. 스키마가 None 이면 경고만."""
    tag = f"[{where}] " if where else ""
    if schema is None:
        warnings.warn(f"{tag}스키마 미선언 — schemas/ 에 선언하면 경계 검증이 켜진다", stacklevel=2)
        return df
    errors: list[str] = []
    cols = schema.get("columns") or {}

    if (mr := schema.get("min_rows")) is not None and len(df) < mr:
        errors.append(f"행 수 {len(df)} < min_rows {mr}")

    for col, spec in cols.items():
        spec = spec or {}
        if col not in df.columns:
            if spec.get("required", False):
                errors.append(f"필수 컬럼 없음: {col}")
            continue
        s = df[col]
        dt = spec.get("dtype", "any")
        check = _DTYPE_CHECK.get(dt)
        if check is None:
            errors.append(f"{col}: 알 수 없는 dtype '{dt}'")
        elif len(s.dropna()) and not check(s):
            errors.append(f"{col}: dtype {dt} 불일치 (실제 {s.dtype})")
        if spec.get("required") and s.isna().any():
            errors.append(f"{col}: 결측 {int(s.isna().sum())}건 (required)")
        if spec.get("unique"):
            try:
                dup = s.dropna().duplicated()
            except TypeError:  # 리스트 컬럼 등 해시 불가
                dup = s.dropna().map(lambda x: str(list(x)) if isinstance(x, (list, tuple, np.ndarray)) else x).duplicated()
            if dup.any():
                errors.append(f"{col}: 중복 {int(dup.sum())}건 (unique)")
        if (lo := spec.get("min")) is not None and (s.dropna() < lo).any():
            errors.append(f"{col}: min {lo} 미만 {int((s.dropna() < lo).sum())}건")
        if (hi := spec.get("max")) is not None and (s.dropna() > hi).any():
            errors.append(f"{col}: max {hi} 초과 {int((s.dropna() > hi).sum())}건")
        if (allowed := spec.get("allowed")) is not None:
            bad = set(s.dropna().unique()) - set(allowed)
            if bad:
                errors.append(f"{col}: 허용 외 값 {sorted(map(str, bad))[:5]}")
        if (pat := spec.get("pattern")) is not None:
            rx = re.compile(pat)
            nb = int((~s.dropna().astype(str).map(lambda x: bool(rx.search(x)))).sum())
            if nb:
                errors.append(f"{col}: pattern {pat!r} 불일치 {nb}건")

    if (pk := schema.get("primary_key")):
        pk = [pk] if isinstance(pk, str) else list(pk)
        if all(c in df.columns for c in pk) and df.duplicated(subset=pk).any():
            errors.append(f"primary_key {pk} 중복 {int(df.duplicated(subset=pk).sum())}건")

    extra = set(df.columns) - set(cols)
    policy = schema.get("extra_columns", "allow")
    if extra and policy == "forbid":
        errors.append(f"선언 외 컬럼: {sorted(extra)}")
    elif extra and policy == "warn":
        warnings.warn(f"{tag}선언 외 컬럼: {sorted(extra)}", stacklevel=2)

    if errors:
        raise SchemaError(f"{tag}스키마 위반 {len(errors)}건 ({schema.get('name', '?')}):\n  - " + "\n  - ".join(errors))
    return df


def infer_schema(df: pd.DataFrame, name: str = "", description: str = "") -> str:
    """기존 DataFrame 에서 스키마 초안 YAML 을 만든다(사람이 required/unique 를 다듬는다)."""
    def dtype_of(s: pd.Series) -> str:
        nn0 = s.dropna()
        if len(nn0) and nn0.map(lambda x: isinstance(x, (list, tuple, np.ndarray))).all():
            return "list"
        if pd.api.types.is_bool_dtype(s):
            return "bool"
        if pd.api.types.is_integer_dtype(s):
            return "int"
        if pd.api.types.is_float_dtype(s):
            return "float"
        if pd.api.types.is_datetime64_any_dtype(s):
            return "datetime"
        nn = s.dropna()
        return "string" if len(nn) and nn.map(lambda x: isinstance(x, str)).all() else "any"

    cols = {}
    for c in df.columns:
        s = df[c]
        spec = {"dtype": dtype_of(s), "required": bool(s.notna().all())}
        if spec["dtype"] != "list" and len(s) and s.notna().all():
            try:
                if s.nunique() == len(s):
                    spec["unique"] = True
            except TypeError:
                pass
        cols[c] = spec
    doc = {
        "name": name or "unnamed",
        "description": description or f"{len(df):,} rows × {df.shape[1]} cols (auto-inferred)",
        "min_rows": 1,
        "columns": cols,
        "extra_columns": "warn",
    }
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)


if __name__ == "__main__":  # python -m src.common.schema infer <table> [--name NAME]
    import argparse
    from .io import read_table

    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["infer"])
    ap.add_argument("table")
    ap.add_argument("--name", default="")
    a = ap.parse_args()
    sys.stdout.write(infer_schema(read_table(a.table), a.name or Path(a.table).stem))
