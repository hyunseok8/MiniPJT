"""평가 루프 실행기: 인-아웃 세트 전체를 한 번 실행하고 판정한다 (CLAUDE.md '평가 루프' 1~5단계).

사용법:
    uv run python -m src.evaluate_regression                      # 반복 1회 (전체 세트, 결과를 iter 파일로 저장)
    uv run python -m src.evaluate_regression --note "고친 내용"     # 이번 반복 전에 고친 내용을 LOOP_LOG.md에 함께 기록
    uv run python -m src.evaluate_regression --case A-01 --case G-03   # 디버깅용 부분 실행 (판정에 쓰지 않음)
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from src.agent import run_agent
from src.judge import judge_case
from src.llm import LLMConfigError

# src/<파일> → 프로젝트 루트. 문항과 결과는 루트의 evaluation/ 폴더에 둔다
ROOT = Path(__file__).resolve().parents[1]
QUERIES_FILE = ROOT / "evaluation" / "regression" / "test_queries.json"
RUNS_DIR = ROOT / "evaluation" / "regression" / "runs"
LOG_FILE = ROOT / "evaluation" / "regression" / "LOOP_LOG.md"


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": case["id"],
        "category": case["category"],
        "query": case["query"],
    }
    try:
        output = run_agent([{"role": "user", "content": case["query"]}])
    except Exception as exc:
        # 에이전트 자체가 실행되지 않음: 설정 누락이나 외부 장애일 가능성이 높다
        return {**record, "status": "error", "passed": False, "error": f"에이전트 실행 실패: {exc}"}
    record.update(output)

    called = {c["name"] for c in output["tool_calls"]}
    expected = set(case.get("expected_tools", []))
    # expected_tools는 통과 조건이 아니라 실패 원인 분석용 진단 정보다 (test_queries.json case_fields)
    record["diagnostics"] = {
        "expected_tools_missing": sorted(expected - called),
        "unexpected_tool_calls": sorted(called) if not expected and called else [],
    }
    try:
        record["items"] = judge_case(
            case, output["answer"], output["tool_calls"], output.get("tool_results", [])
        )
    except Exception as exc:
        return {**record, "status": "error", "passed": False, "error": f"판정 실패: {exc}"}
    record["status"] = "ok"
    record["passed"] = all(item["passed"] for item in record["items"])
    return record


def rate(results: list[dict[str, Any]]) -> float:
    return sum(r["passed"] for r in results) / len(results) if results else 0.0


def previous_iterations() -> list[dict[str, Any]]:
    files = sorted(RUNS_DIR.glob("iter_*.json"))
    return [json.loads(f.read_text(encoding="utf-8")) for f in files]


def loop_status(history: list[dict[str, Any]], loop_cfg: dict, criteria: dict) -> tuple[str, str]:
    """history의 마지막 항목이 이번 반복이다. (상태, 설명)을 돌려준다."""
    need = criteria["consecutive_passing_iterations"]
    recent = history[-need:]
    if len(recent) == need and all(h["summary"]["meets_criteria"] for h in recent):
        return "success", f"기준을 연속 {need}회 넘었다. 루프를 종료하고 결과를 보고한다."
    if len(history) >= loop_cfg["max_iterations"]:
        return (
            "stop",
            f"최대 반복 {loop_cfg['max_iterations']}회에 도달했다. 남은 실패를 사용자에게 보고한다.",
        )
    window = loop_cfg["stop_if_no_improvement_for"]
    rates = [h["summary"]["overall_pass_rate"] for h in history]
    if len(rates) > window and max(rates[-window:]) <= max(rates[:-window]):
        return (
            "stop",
            f"{window}회 연속 통과율이 오르지 않았다. 원인 분석과 함께 사용자에게 보고한다.",
        )
    if history[-1]["summary"]["meets_criteria"]:
        return "continue", "기준을 넘었다. 코드를 바꾸지 말고 한 번 더 실행해 연속 통과를 확인한다."
    return (
        "continue",
        "실패 원인을 분석해 고친 뒤 다시 실행한다 (CLAUDE.md '실패 유형과 수정 위치').",
    )


def print_report(run: dict[str, Any]) -> None:
    s = run["summary"]
    print(f"\n=== {run['label']} ===")
    print(f"전체 통과율: {s['overall_pass_rate']:.0%} ({s['passed']}/{s['total']})")
    if s.get("guardrail_total", 1):
        print(f"가드레일 통과율: {s['guardrail_pass_rate']:.0%}")
    else:
        print("가드레일 통과율: 해당 케이스 없음")
    if s.get("newly_passed") is not None:
        print(f"새로 통과: {', '.join(s['newly_passed']) or '없음'}")
        print(f"회귀(새로 실패): {', '.join(s['regressions']) or '없음'}")
    for r in run["results"]:
        if r["passed"]:
            continue
        print(f"\n[실패] {r['id']} ({r['category']}) {r['query']}")
        if r["status"] == "error":
            print(f"  - 오류: {r['error']}")
            continue
        for item in r["items"]:
            if not item["passed"]:
                print(f"  - {item['kind']}: {item['criterion']}")
                print(f"    근거: {item['evidence']}")
        diag = r.get("diagnostics", {})
        if diag.get("expected_tools_missing"):
            print(f"  - 진단: 호출하지 않은 기대 도구 {diag['expected_tools_missing']}")
        if diag.get("unexpected_tool_calls"):
            print(
                f"  - 진단: 도구를 부르지 않아야 할 케이스에서 호출 {diag['unexpected_tool_calls']}"
            )
        for found in (r.get("verification") or {}).get("history", []):
            if found:
                print(f"  - 출력 검증: 근거 없는 링크 발견 {found}")
        if (r.get("verification") or {}).get("stripped"):
            print(f"  - 출력 검증: 고쳐 쓴 뒤에도 남아 지운 링크 {r['verification']['stripped']}")
        for err in r.get("tool_errors", []):
            print(f"  - 도구 오류(외부 장애일 수 있음): {err['tool']}: {err['error']}")
    if "loop" in run:
        print(f"\n루프 상태: {run['loop']['status']} - {run['loop']['next']}")


def append_log(run: dict[str, Any], note: str) -> None:
    if not LOG_FILE.exists():
        LOG_FILE.write_text("# 평가 루프 기록\n", encoding="utf-8")
    s = run["summary"]
    row = (
        f"| {run['iteration']} | {run['timestamp'][:16]} | {note or '-'} | "
        f"{s['overall_pass_rate']:.0%} | {s['guardrail_pass_rate']:.0%} | "
        f"{', '.join(s['newly_passed']) or '-'} | {', '.join(s['regressions']) or '-'} | "
        f"{run['loop']['status']} |\n"
    )
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(row)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="RunTrip Agent 평가 루프 실행기")
    parser.add_argument(
        "--case", action="append", help="디버깅용: 지정한 케이스만 실행 (판정에 쓰지 않음)"
    )
    parser.add_argument("--note", default="", help="이번 반복 전에 고친 내용 (LOOP_LOG.md에 기록)")
    parser.add_argument("--workers", type=int, default=3, help="동시에 실행할 케이스 수")
    args = parser.parse_args()

    spec = json.loads(QUERIES_FILE.read_text(encoding="utf-8"))
    cases = spec["cases"]
    if args.case:
        cases = [c for c in cases if c["id"] in set(args.case)]
        if not cases:
            print(f"케이스를 찾지 못했다: {args.case}")
            return 2

    try:
        from src.agent import build_graph

        build_graph()
    except LLMConfigError as exc:
        print(f"실행할 수 없다: {exc}")
        return 2

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run_case, cases))

    guardrails = [r for r in results if r["category"] == "guardrail"]
    criteria = spec["meta"]["pass_criteria"]
    summary: dict[str, Any] = {
        "total": len(results),
        "passed": sum(r["passed"] for r in results),
        "overall_pass_rate": rate(results),
        "guardrail_pass_rate": rate(guardrails),
        "guardrail_total": len(guardrails),
        "errors": [r["id"] for r in results if r["status"] == "error"],
    }
    summary["meets_criteria"] = (
        summary["overall_pass_rate"] >= criteria["overall_min_pass_rate"]
        and summary["guardrail_pass_rate"] >= criteria["guardrail_min_pass_rate"]
    )
    now = datetime.now().isoformat(timespec="seconds")
    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    if args.case:  # 디버깅용 부분 실행: 반복 횟수와 로그에 포함하지 않는다
        run = {
            "label": "디버깅 실행 (판정에 쓰지 않음)",
            "timestamp": now,
            "summary": summary,
            "results": results,
        }
        path = RUNS_DIR / f"debug_{now.replace(':', '')}.json"
        path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
        print_report(run)
        print(f"\n결과 파일: {path.relative_to(ROOT)}")
        return 0

    history = previous_iterations()
    iteration = len(history) + 1
    prev_passed = {r["id"] for r in history[-1]["results"] if r["passed"]} if history else set()
    now_passed = {r["id"] for r in results if r["passed"]}
    summary["newly_passed"] = sorted(now_passed - prev_passed) if history else sorted(now_passed)
    summary["regressions"] = sorted(prev_passed - now_passed)

    run = {
        "label": f"반복 {iteration}회차",
        "iteration": iteration,
        "timestamp": now,
        "note": args.note,
        "summary": summary,
        "results": results,
    }
    status, next_step = loop_status([*history, run], spec["meta"]["loop"], criteria)
    run["loop"] = {"status": status, "next": next_step}

    path = RUNS_DIR / f"iter_{iteration:03d}.json"
    path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    append_log(run, args.note)
    print_report(run)
    print(f"\n결과 파일: {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
