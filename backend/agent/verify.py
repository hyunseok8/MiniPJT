"""출력 검증: 최종 답변의 링크가 실제 도구 결과(또는 사용자 입력)에 나온 주소인지 코드로 확인한다.

정책 1(지어내지 않는다)을 프롬프트만으로 지키지 못한 사례가 있어서 추가했다.
검색이 실패하자 모델이 기억으로 크루 모임 링크와 일정을 채운 일이 있었다 (evals/runs 디버깅 기록).
LLM을 부르지 않는 순수 함수로만 이루어져 있다.
"""

import calendar
import json
import re
from urllib.parse import unquote

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

# 마크다운 링크 안이나 본문의 URL. 한글·일본어 도메인도 잡도록 공백과 괄호류에서만 끊는다
URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"'`|\\]+")
TRAILING = ".,;:!?*~」』）】、。"


def extract_urls(text: str) -> list[str]:
    urls = []
    for raw in URL_RE.findall(text):
        url = raw.rstrip(TRAILING)
        if url and url not in urls:
            urls.append(url)
    return urls


def normalize(url: str) -> str:
    """비교용 정규화: 디코딩, 스킴·www·끝 슬래시·#조각 제거, 호스트 소문자."""
    url = unquote(url).split("#", 1)[0]
    url = re.sub(r"^https?://", "", url, flags=re.IGNORECASE)
    host, _, path = url.partition("/")
    host = host.lower().removeprefix("www.")
    return f"{host}/{path}".rstrip("/")


def _message_text(msg: BaseMessage) -> str:
    content = msg.content
    if isinstance(content, list):
        content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    # 도구 결과는 JSON 문자열이다. 비ASCII가 \uXXXX로 이스케이프돼 있을 수 있어 풀어서 다시 쓴다
    if isinstance(msg, ToolMessage):
        try:
            return json.dumps(json.loads(content), ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            pass
    return str(content)


def evidence_urls(messages: list[BaseMessage]) -> set[str]:
    """답변의 근거로 인정하는 주소: 도구 결과, 도구 호출 인자(fetch_page로 연 주소), 사용자 입력,
    이전 대화의 답변(그때 검증을 거침)에 나온 주소."""
    texts = []
    for msg in messages:
        if isinstance(msg, (ToolMessage, HumanMessage)):
            texts.append(_message_text(msg))
        elif isinstance(msg, AIMessage):
            texts += [json.dumps(c["args"], ensure_ascii=False) for c in msg.tool_calls]
            if msg.additional_kwargs.get("from_history"):
                texts.append(_message_text(msg))
    return {normalize(u) for text in texts for u in extract_urls(text)}


def find_unverified(answer: str, messages: list[BaseMessage]) -> list[str]:
    """답변에 있지만 근거 주소 어디에도 없는 링크 목록.

    근거 주소와 같거나, 근거 주소의 상위 경로(예: 사이트 첫 화면)이면 인정한다.
    """
    seen = evidence_urls(messages)
    unverified = []
    for url in extract_urls(answer):
        n = normalize(url)
        if not any(s == n or s.startswith(n + "/") for s in seen):
            unverified.append(url)
    return unverified


def strip_unverified(answer: str, urls: list[str]) -> str:
    """재작성 후에도 남은 근거 없는 링크를 지운다. 링크 글자는 남기고 '출처 확인 불가'를 붙인다."""
    for url in urls:
        escaped = re.escape(url)
        answer = re.sub(rf"\[([^\]]*)\]\({escaped}[^)]*\)", r"\1 (출처 확인 불가)", answer)
        answer = re.sub(rf"<?{escaped}>?", "(출처 확인 불가)", answer)
    return answer


# 답변 맨 앞의 진행 멘트. 프롬프트로 금지해도 도구를 많이 부른 긴 작업 뒤에 다시 나와서 코드로 지운다
PREAMBLE_RE = re.compile(
    r"(준비(가)?\s?(됐|되었|완료)|모였|모두\s?(확인|수집|모았)|(확인|수집)(이)?\s?(됐|되었|완료)|"
    r"정리해\s?(드릴|드립|볼)|최종\s?답변|답변(을)?\s?드릴|찾아\s?보겠|살펴\s?보겠|알아\s?보겠|"
    r"충분한\s?정보|필요한\s?정보(를|가)|"
    # "정리해드리겠습니다", "비교해 드릴게요", "비교해 드립니다" 같은 예고 문장.
    # "드릴 수 없어요" 같은 거절은 잡지 않는다
    r"드리겠|드릴게|해\s?드립니다)"
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+")
MAX_PREAMBLE_SENTENCE = 60


def strip_preamble(answer: str) -> tuple[str, list[str]]:
    """답변 첫 줄 앞부분의 진행 멘트 문장을 지운다. 지운 문장 목록도 돌려준다.

    짧은 문장(60자 이하)이 진행 멘트 표현을 담고 있을 때만 지운다. 같은 줄에 이어지는 결론 문장은 남긴다.
    """
    lines = answer.split("\n")
    idx = next((i for i, line in enumerate(lines) if line.strip()), None)
    if idx is None:
        return answer, []
    sentences = SENTENCE_SPLIT_RE.split(lines[idx].strip())
    removed = []
    while (
        sentences
        and len(sentences[0]) <= MAX_PREAMBLE_SENTENCE
        and PREAMBLE_RE.search(sentences[0])
    ):
        removed.append(sentences.pop(0))
    if not removed:
        return answer, []
    rest = " ".join(sentences).strip()
    if rest:
        lines[idx] = rest
        return "\n".join(lines), removed
    # 첫 줄 전체가 진행 멘트였다: 그 줄과 바로 뒤의 빈 줄, 구분선(---)도 지운다
    tail = lines[idx + 1 :]
    while tail and (not tail[0].strip() or tail[0].strip() == "---"):
        tail.pop(0)
    return "\n".join(lines[:idx] + tail), removed


def _split_row(line: str) -> list[str]:
    return line.strip().strip("|").split("|")


def _is_table_row(line: str) -> bool:
    return line.strip().startswith("|") and line.strip().endswith("|")


def _is_separator(cells: list[str]) -> bool:
    return all(re.fullmatch(r"\s*:?-{2,}:?\s*", c) for c in cells)


def fix_elevation_cells(answer: str) -> tuple[str, list[str]]:
    """표의 '고저차' 칸에 숫자도 '확인 불가'도 없으면 '확인 불가 (출처 설명: …)'로 바꾼다.

    프롬프트로 금지해도 "완만한 오르막"처럼 출처의 말을 그대로 옮기는 일이 반복돼서 코드로 고친다.
    바꾼 원래 값 목록도 돌려준다.
    """
    lines = answer.split("\n")
    changed: list[str] = []
    col = None
    for i, line in enumerate(lines):
        if not _is_table_row(line):
            col = None
            continue
        cells = _split_row(line)
        if col is None:
            headers = [c.strip() for c in cells]
            col = next((j for j, h in enumerate(headers) if "고저차" in h), None)
            if col is None:
                col = -1  # 고저차 열이 없는 표: 이 표가 끝날 때까지 건너뛴다
            continue
        if col < 0 or _is_separator(cells) or col >= len(cells):
            continue
        value = cells[col].strip()
        plain = value.replace("*", "").strip()
        # 숫자가 있거나 "확인 불가"로 시작하면 규칙대로 쓴 칸이다. "완만한 오르막 (숫자 확인 불가)"처럼
        # 설명 뒤에 '확인 불가'를 덧붙인 칸은 고친다 (2026-11-12 평가에서 빠져나간 사례)
        if (
            not plain
            or plain in {"-", "—"}
            or re.search(r"\d", plain)
            or plain.startswith("확인 불가")
        ):
            continue
        description = re.sub(r"\s*\([^)]*확인 불가[^)]*\)", "", plain).strip() or plain
        cells[col] = f" 확인 불가 (출처 설명: {description}) "
        lines[i] = "|" + "|".join(cells) + "|"
        changed.append(plain)
    return "\n".join(lines), changed


# 답변 첫 줄에 밝힌 기간. "2027년 3~5월" / "2027년 4월 12일~18일" / "4월 12일(월)~18일(일)"
MONTH_RANGE_RE = re.compile(r"(\d{4})년\s*(\d{1,2})\s*[~\-–]\s*(\d{1,2})월")
DAY_RANGE_RE = re.compile(
    r"(?:(\d{4})년\s*)?(\d{1,2})월\s*(\d{1,2})일(?:\s*\([^)]*\))?\s*[~\-–]\s*"
    r"(?:(\d{1,2})월\s*)?(\d{1,2})일"
)
ROW_DATE_RE = re.compile(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일")
OUT_OF_RANGE_MARKS = ("범위 밖", "기간 밖")


def stated_range(answer: str) -> tuple[tuple[int, int, int], tuple[int, int, int]] | None:
    """답변 첫 줄에서 (시작, 끝) 날짜를 읽는다. 연도를 모르면 0으로 둔다(월·일만 비교)."""
    first = next((line for line in answer.split("\n") if line.strip()), "")
    if m := MONTH_RANGE_RE.search(first):
        year, m1, m2 = int(m[1]), int(m[2]), int(m[3])
        end_year = year + 1 if m2 < m1 else year
        return (year, m1, 1), (end_year, m2, calendar.monthrange(end_year, m2)[1])
    if m := DAY_RANGE_RE.search(first):
        year = int(m[1]) if m[1] else 0
        m1, d1 = int(m[2]), int(m[3])
        m2 = int(m[4]) if m[4] else m1
        return (year, m1, d1), (year, m2, int(m[5]))
    return None


def mark_out_of_range(answer: str) -> tuple[str, list[str]]:
    """첫 줄에 밝힌 기간 밖의 날짜가 표에 있으면 그 날짜 옆에 '(요청 기간 밖)'을 붙인다.

    에이전트가 기간을 밝혀 놓고 범위 밖 대회를 섞는 일이 있어서 코드로 표시한다.
    표시한 날짜 목록도 돌려준다.
    """
    rng = stated_range(answer)
    if rng is None:
        return answer, []
    (y1, m1, d1), (y2, m2, d2) = rng
    lines = answer.split("\n")
    marked: list[str] = []
    for i, line in enumerate(lines):
        if not _is_table_row(line) or any(mark in line for mark in OUT_OF_RANGE_MARKS):
            continue
        m = ROW_DATE_RE.search(line)
        if not m:
            continue
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
        if y1 == 0:  # 연도를 모르면 월·일만 비교한다
            inside = (m1, d1) <= (mo, d) <= (m2, d2)
        else:
            inside = (y1, m1, d1) <= (y, mo, d) <= (y2, m2, d2)
        if not inside:
            lines[i] = line[: m.end()] + " (요청 기간 밖)" + line[m.end() :]
            marked.append(m[0])
    return "\n".join(lines), marked


def feedback_message(urls: list[str]) -> str:
    listed = "\n".join(f"- {u}" for u in urls)
    return (
        "[출력 검증] 답변에 도구 결과에서 확인되지 않은 링크가 있다:\n"
        f"{listed}\n"
        "이 링크와, 이 링크로 뒷받침한 정보(모임 일정, 장소, 날짜, 가격 등)는 지어낸 것으로 본다. "
        "도구로 다시 확인할 수 있으면 확인하고, 확인하지 못하면 해당 항목을 '확인 불가'로 바꾸거나 빼서 "
        "최종 답변 전체를 다시 써라. 도구 결과에 나온 링크만 쓴다. 이 검증 메시지는 언급하지 않는다."
    )
