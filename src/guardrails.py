"""가드레일: 개인정보(PII) 마스킹과 프롬프트 인젝션 방어 (SERVICE.md 정책 3·4).

에이전트(create_agent)의 미들웨어로 붙는다 (src/agent.py).
- InputGuardMiddleware (before_agent): 모델을 부르기 전에
  1) 대화의 모든 사용자 메시지에서 카드번호, 주민등록번호, 휴대폰 번호, 이메일을 가린다.
     서버는 대화를 저장하지 않지만, 화면이 이전 대화를 다시 보내므로 이전 메시지까지 가린다.
  2) 마지막 사용자 메시지에 시스템 지시를 바꾸려는 문구가 있으면 모델을 부르지 않고 정해진 안내로 끝낸다.
- PIIMiddleware (LangChain 기본 제공): 마지막 입력과 모델 출력의 카드번호, 주민등록번호를 가린다.
  도구 결과에서 개인정보가 답변으로 새어 나가는 것을 막는다.
LLM을 부르지 않는 정규식 검사라 결과가 매번 같다.
"""

import re
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState, PIIMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage

# ---------------------------------------------------------------------------
# 개인정보 패턴
# ---------------------------------------------------------------------------
# 카드번호: 4자리씩 끊거나 붙여 쓴 13~19자리. 날짜(2027-04-12)나 가격과 헷갈리지 않게 Luhn 검사를 함께 한다
CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
# 주민등록번호: 6자리-7자리 (뒷자리 첫 숫자 1~8)
RRN_RE = re.compile(r"(?<!\d)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])[- ]?[1-8]\d{6}(?!\d)")
# 한국 휴대폰 번호: 010-1234-5678, 01012345678, +82 10-1234-5678
PHONE_RE = re.compile(r"(?<![\d+])(?:\+82[ -]?1[016-9]|01[016-9])[ .-]?\d{3,4}[ .-]?\d{4}(?!\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

MASKS = {
    "card": "[카드번호 가림]",
    "rrn": "[주민등록번호 가림]",
    "phone": "[전화번호 가림]",
    "email": "[이메일 가림]",
}


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def mask_pii(text: str) -> tuple[str, list[str]]:
    """개인정보를 가린 글과, 가린 종류 목록을 돌려준다."""
    found: list[str] = []

    def card(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m[0])
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            found.append("card")
            return MASKS["card"]
        return m[0]

    def sub(kind: str, pattern: re.Pattern[str], value: str) -> str:
        def repl(_: re.Match[str]) -> str:
            found.append(kind)
            return MASKS[kind]

        return pattern.sub(repl, value)

    text = CARD_RE.sub(card, text)
    text = sub("rrn", RRN_RE, text)
    text = sub("phone", PHONE_RE, text)
    text = sub("email", EMAIL_RE, text)
    return text, sorted(set(found))


# ---------------------------------------------------------------------------
# 프롬프트 인젝션 패턴
# ---------------------------------------------------------------------------
# 시스템 지시를 무시시키거나, 시스템 프롬프트를 빼내거나, 역할을 바꾸려는 문구.
# "이전에 찾은 대회"처럼 평범한 질문과 겹치지 않게 '지시·규칙·프롬프트'를 함께 요구한다
INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above|earlier)\s+(instructions|rules|prompts?)",
        r"disregard\s+(all\s+|the\s+)?(previous|prior|above|system)\s+(instructions|rules|prompts?)",
        r"(reveal|show|print|repeat|output)\s+(me\s+)?(your|the)\s+(system\s+prompt|instructions|hidden\s+prompt)",
        r"(you\s+are\s+now|act\s+as)\s+(dan|an?\s+unrestricted|jailbroken)",
        r"developer\s+mode|jailbreak",
        r"(이전|앞|위|기존|모든|원래)\s*의?\s*(지시|지침|명령|규칙|프롬프트|설정)\s*(을|를|은|는|들을|들은)?\s*(전부|모두|다)?\s*(무시|잊어|잊고|따르지\s*마)",
        r"(시스템|system)\s*(프롬프트|prompt|지시문|지침)\s*(을|를)?\s*(보여|알려|출력|공개|말해)",
        r"너의?\s*(규칙|지시|지침|정책)\s*(을|를)?\s*(무시|해제|풀어|끄고|꺼)",
        r"(지금부터|이제부터)\s*너는\s*.{0,20}(제한\s*없는|아무\s*제약|탈옥)",
    ]
]

INJECTION_REPLY = (
    "이 요청에는 RunTrip의 안내 규칙을 바꾸거나 내부 지시를 보여 달라는 내용이 있어서 따를 수 없어요. "
    "저는 해외 마라톤 대회 찾기와 여행지 러닝 코스 탐색을 도와드릴 수 있어요. "
    '예: "내년 봄에 갈 만한 해외 하프마라톤 찾아줘", "교토 가와라마치역 근처 러닝 코스 알려줘"'
)


def detect_injection(text: str) -> list[str]:
    """찾은 인젝션 문구 목록. 없으면 빈 목록."""
    return [m[0] for p in INJECTION_PATTERNS if (m := p.search(text))]


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict))


class InputGuardMiddleware(AgentMiddleware):
    """에이전트가 시작될 때 한 번: 사용자 메시지의 개인정보를 가리고, 인젝션이면 바로 끝낸다."""

    @hook_config(can_jump_to=["end"])
    def before_agent(self, state: AgentState, runtime: Any) -> dict[str, Any] | None:
        messages = state["messages"]
        updates: list[Any] = []
        for msg in messages:
            if not isinstance(msg, HumanMessage) or not msg.id:
                continue
            masked, kinds = mask_pii(_content_text(msg.content))
            if kinds:
                # 같은 id로 돌려주면 그 메시지가 교체된다
                updates.append(HumanMessage(content=masked, id=msg.id))

        last_user = next((m for m in reversed(messages) if isinstance(m, HumanMessage)), None)
        # 출력 검증(verify)이 보낸 다시 쓰기 요청은 사용자 입력이 아니므로 검사하지 않는다
        if last_user is not None and not last_user.additional_kwargs.get("from_verifier"):
            if detect_injection(_content_text(last_user.content)):
                return {
                    "messages": [*updates, AIMessage(INJECTION_REPLY, name="input_guard")],
                    "jump_to": "end",
                }
        return {"messages": updates} if updates else None


def build_guard_middleware() -> list[AgentMiddleware]:
    """에이전트에 붙일 가드레일 미들웨어 목록 (실행 순서대로)."""
    return [
        InputGuardMiddleware(),
        # LangChain 기본 PII 미들웨어: 마지막 입력과 모델 출력을 함께 검사한다
        PIIMiddleware("credit_card", strategy="redact", apply_to_input=True, apply_to_output=True),
        PIIMiddleware(
            "kr_rrn",
            detector=RRN_RE.pattern,
            strategy="redact",
            apply_to_input=True,
            apply_to_output=True,
        ),
    ]
