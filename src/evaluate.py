"""evaluation/test_queries.csv의 문항마다 에이전트를 실행하고 채점한다.

채점 기준 (문항 하나):
- expected_tools: 값이 있으면 그 도구를 모두 불렀는지 코드로 확인한다.
  "search_races|search_web"처럼 |로 묶은 항목은 둘 중 하나만 불러도 된다.
- forbidden: 항목 하나라도 답변에 해당하면 실패다. 항목이 "국내 대회 포함"처럼 설명문이라
  글자 일치로는 잡을 수 없으므로, LLM 판정기(src/judge.py)가 도구 결과와 대조해 판정한다.
- expected_traits: 자동 채점하지 않는다. 답변과 나란히 검토 파일에 남겨 사람이 본다.
- 자동 통과 = 실행 성공 + 기대 도구 모두 호출 + 금지 항목 위반 없음

사용법 (프로젝트 루트에서):
    uv run python -m src.evaluate                  # 전체 문항
    uv run python -m src.evaluate --case P-01 --case G-01
    uv run python -m src.evaluate --workers 2      # 동시 실행 (무료 검색이 막힐 수 있어 기본 1)

결과 (evaluation/results/):
    run_<시각>.json        문항별 전체 기록 (답변, 도구 호출, 도구 결과, 판정 근거)
    review_<시각>.csv      사람 검토용: 입력, 기대 특성, 답변, 자동 판정을 나란히. '사람 판정' 칸을 채운다
    summary_<시각>.txt     카테고리별, 전체 통과율
"""

import argparse
import csv
import json
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from src.agent import run_agent
from src.judge import judge_case
from src.llm import LLMConfigError

# src/<파일> → 프로젝트 루트. 문항과 결과는 루트의 evaluation/ 폴더에 둔다
ROOT = Path(__file__).resolve().parents[1]
CSV_FILE = ROOT / "evaluation" / "test_queries.csv"
RESULTS_DIR = ROOT / "evaluation" / "results"
CATEGORY_ORDER = ["positive", "negative", "edge", "guardrail"]


def split_list(value: str) -> list[str]:
    return [v.strip() for v in (value or "").split(";") if v.strip()]


def load_cases(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    return [
        {
            "id": r["id"].strip(),
            "category": r["category"].strip(),
            "input": r["input"].strip(),
            "expected_traits": split_list(r["expected_traits"]),
            "forbidden": split_list(r["forbidden"]),
            "expected_tools": split_list(r["expected_tools"]),
            "note": r.get("note", "").strip(),
        }
        for r in rows
    ]


def grade(case: dict[str, Any]) -> dict[str, Any]:
    record: dict[str, Any] = {**case, "status": "ok", "failures": []}
    try:
        output = run_agent([{"role": "user", "content": case["input"]}])
    except Exception as exc:  # 설정 누락이나 외부 장애
        record.update(status="error", passed=False, failures=[f"에이전트 실행 실패: {exc}"])
        return record
    record.update(output)

    # 1) 기대 도구 호출 여부 (코드 판정)
    called = {c["name"] for c in output["tool_calls"]}
    # "a|b"는 둘 중 하나만 불러도 된다는 뜻이다
    missing = [
        t for t in case["expected_tools"] if not any(alt.strip() in called for alt in t.split("|"))
    ]
    record["tools_check"] = {
        "expected": case["expected_tools"],
        "called": sorted(called),
        "missing": missing,
        "passed": not missing,
    }
    if missing:
        record["failures"].append(f"기대 도구를 부르지 않음: {', '.join(missing)}")

    # 2) 금지 항목 (LLM 판정). must_not 판정만 쓴다
    record["forbidden_check"] = []
    if case["forbidden"]:
        try:
            items = judge_case(
                {"query": case["input"], "must_include": [], "must_not": case["forbidden"]},
                output["answer"],
                output["tool_calls"],
                output.get("tool_results", []),
            )
        except Exception as exc:
            record.update(status="error", passed=False)
            record["failures"].append(f"금지 항목 판정 실패: {exc}")
            return record
        for item in items:
            violated = not item["passed"]
            record["forbidden_check"].append(
                {"forbidden": item["criterion"], "violated": violated, "evidence": item["evidence"]}
            )
            if violated:
                record["failures"].append(f"금지 항목 위반: {item['criterion']}")

    record["passed"] = not record["failures"]
    return record


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_cat: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r["passed"])
    cats = sorted(by_cat, key=lambda c: (CATEGORY_ORDER + [c]).index(c))
    return {
        "total": len(results),
        "passed": sum(r["passed"] for r in results),
        "overall_pass_rate": sum(r["passed"] for r in results) / len(results) if results else 0.0,
        "by_category": {
            c: {
                "total": len(by_cat[c]),
                "passed": sum(by_cat[c]),
                "pass_rate": sum(by_cat[c]) / len(by_cat[c]),
            }
            for c in cats
        },
    }


def summary_text(summary: dict[str, Any], results: list[dict[str, Any]]) -> str:
    lines = [
        "[자동 채점 결과] 기대 도구 호출 + 금지 항목 위반 여부 (expected_traits는 사람 검토)",
        "",
    ]
    for cat, s in summary["by_category"].items():
        lines.append(f"{cat:<10} {s['pass_rate']:>5.0%}  ({s['passed']}/{s['total']})")
    lines.append(
        f"{'전체':<10} {summary['overall_pass_rate']:>5.0%}  ({summary['passed']}/{summary['total']})"
    )
    failed = [r for r in results if not r["passed"]]
    if failed:
        lines += ["", "[실패 문항]"]
        for r in failed:
            lines.append(f"{r['id']} ({r['category']}) {r['input']}")
            for msg in r["failures"]:
                lines.append(f"  - {msg}")
            for fc in r.get("forbidden_check", []):
                if fc["violated"]:
                    lines.append(f"    근거: {fc['evidence']}")
    errors = [(r["id"], e) for r in results for e in r.get("tool_errors", [])]
    if errors:
        lines += ["", "[도구 오류 - 외부 장애일 수 있음]"]
        lines += [f"{rid}: {e['tool']}: {e['error'][:150]}" for rid, e in errors]
    return "\n".join(lines)


def write_review_csv(path: Path, results: list[dict[str, Any]]) -> None:
    """사람 검토용 파일. 기대 특성과 답변을 나란히 두고, 특성마다 확인할 수 있게 번호를 붙인다."""
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "id",
                "category",
                "input",
                "expected_traits (사람이 확인)",
                "answer",
                "자동 판정",
                "자동 판정 사유",
                "호출한 도구",
                "사람 판정 (O/X)",
                "사람 메모",
            ]
        )
        for r in results:
            traits = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(r["expected_traits"]))
            w.writerow(
                [
                    r["id"],
                    r["category"],
                    r["input"],
                    traits,
                    r.get("answer", ""),
                    "통과" if r["passed"] else "실패",
                    "\n".join(r["failures"]) or "-",
                    ", ".join(c["name"] for c in r.get("tool_calls", [])) or "(없음)",
                    "",
                    "",
                ]
            )


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="test_queries.csv 채점기")
    parser.add_argument("--csv", type=Path, default=CSV_FILE, help="문항 CSV 경로")
    parser.add_argument("--case", action="append", help="지정한 id만 실행 (여러 번 지정 가능)")
    parser.add_argument("--workers", type=int, default=1, help="동시에 실행할 문항 수")
    args = parser.parse_args()

    cases = load_cases(args.csv)
    if args.case:
        cases = [c for c in cases if c["id"] in set(args.case)]
        if not cases:
            print(f"문항을 찾지 못했다: {args.case}")
            return 2

    try:
        from src.agent import build_graph

        build_graph()
    except LLMConfigError as exc:
        print(f"실행할 수 없다: {exc}")
        return 2

    print(f"{len(cases)}개 문항 실행 중...")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(grade, cases))

    summary = summarize(results)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    run_path = RESULTS_DIR / f"run_{stamp}.json"
    review_path = RESULTS_DIR / f"review_{stamp}.csv"
    summary_path = RESULTS_DIR / f"summary_{stamp}.txt"

    run_path.write_text(
        json.dumps(
            {
                "timestamp": stamp,
                "source": str(args.csv.relative_to(ROOT)),
                "summary": summary,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    write_review_csv(review_path, results)
    text = summary_text(summary, results)
    summary_path.write_text(text + "\n", encoding="utf-8")

    print()
    print(text)
    print()
    print("결과 파일:")
    for p in (summary_path, review_path, run_path):
        print(f"  {p.relative_to(ROOT)}")
    print("review CSV의 '사람 판정' 칸에서 expected_traits를 확인한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
