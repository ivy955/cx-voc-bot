import os
import re
import json
from datetime import datetime, date
from zoneinfo import ZoneInfo
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import anthropic

# ── 환경변수 ──────────────────────────────────────────────
SLACK_BOT_TOKEN   = os.environ["SLACK_BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SOURCE_CHANNEL_ID = os.environ["SOURCE_CHANNEL_ID"]   # 1-슬기로운cx팀생활
TARGET_CHANNEL_ID = os.environ["TARGET_CHANNEL_ID"]   # 5_cowork_고객voc
VOC_BOT_USER_ID   = os.environ["VOC_BOT_USER_ID"]     # VOC봇 슬랙 User ID

KST = ZoneInfo("Asia/Seoul")

slack  = WebClient(token=SLACK_BOT_TOKEN)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ─────────────────────────────────────────────────────────
# 1. 오늘 VOC봇 메시지 찾기
# ─────────────────────────────────────────────────────────
def find_todays_voc_message():
    today = date.today()
    # 당일 00:00 ~ 23:59 KST 범위
    oldest = datetime(today.year, today.month, today.day, 0, 0, tzinfo=KST).timestamp()
    latest  = datetime(today.year, today.month, today.day, 23, 59, tzinfo=KST).timestamp()

    try:
        result = slack.conversations_history(
            channel=SOURCE_CHANNEL_ID,
            oldest=str(oldest),
            latest=str(latest),
            limit=50
        )
    except SlackApiError as e:
        raise RuntimeError(f"슬랙 히스토리 조회 실패: {e.response['error']}")

    for msg in result.get("messages", []):
        if msg.get("user") == VOC_BOT_USER_ID or msg.get("bot_id"):
            # VOC 취합 메시지 키워드 확인
            text = msg.get("text", "")
            if "VOC 취합" in text or "voc" in text.lower():
                return msg
    return None


# ─────────────────────────────────────────────────────────
# 2. 스레드 댓글 가져오기
# ─────────────────────────────────────────────────────────
def get_thread_replies(parent_ts):
    try:
        result = slack.conversations_replies(
            channel=SOURCE_CHANNEL_ID,
            ts=parent_ts
        )
    except SlackApiError as e:
        raise RuntimeError(f"스레드 조회 실패: {e.response['error']}")

    messages = result.get("messages", [])
    # 첫 번째는 부모 메시지 → 제외
    replies = [m for m in messages[1:] if m.get("text", "").strip()]
    return replies


# ─────────────────────────────────────────────────────────
# 3. 댓글 파싱: 브랜드 / 카테고리 / 내용 / 건수
# ─────────────────────────────────────────────────────────
def parse_reply(text: str) -> dict | None:
    """
    형식 예시:
      모두공 [건의] 수강앱 확대기능 요청 - 1건
      삼쩜삼캠퍼스 [건의] 강의 자막 요청 - 1건

    반환: {"brand": ..., "category": ..., "content": ..., "count": int}
    """
    text = text.strip()

    # 건수 추출 (맨 끝 "- N건")
    count = 1
    count_match = re.search(r"-\s*(\d+)\s*건\s*$", text)
    if count_match:
        count = int(count_match.group(1))
        text = text[:count_match.start()].strip()

    # 카테고리 추출 ([건의], [불만] 등)
    cat_match = re.search(r"\[([^\]]+)\]", text)
    if not cat_match:
        return None  # 형식 불일치 → 스킵
    category = cat_match.group(1)

    # 브랜드: 카테고리 앞 부분
    brand = text[:cat_match.start()].strip()

    # 내용: 카테고리 뒤 부분
    content = text[cat_match.end():].strip()

    if not brand or not content:
        return None

    return {"brand": brand, "category": category, "content": content, "count": count}


# ─────────────────────────────────────────────────────────
# 4. Claude API로 유사 항목 병합
# ─────────────────────────────────────────────────────────
def merge_similar_items(items: list[dict]) -> list[dict]:
    """
    items = [{"brand": "모두공", "category": "건의", "content": "수강앱 확대기능 요청", "count": 1}, ...]
    Claude에게 유사 항목 병합을 맡기고 결과 반환
    """
    if len(items) <= 1:
        return items

    prompt = f"""아래는 고객 VOC 댓글에서 파싱한 항목들입니다.
같은 브랜드, 같은 카테고리에서 내용이 실질적으로 동일하거나 유사한(띄어쓰기/표현만 다른) 항목을 하나로 병합하고, 건수를 합산해 주세요.

규칙:
- brand와 category가 다르면 절대 병합하지 마세요.
- 내용이 실질적으로 같은 요청/문의인 경우에만 병합하세요.
- 병합 시 content는 더 완전하고 명확한 표현으로 하나를 선택하세요.
- count는 해당 항목들의 count 합산값을 사용하세요.

입력 항목 (JSON):
{json.dumps(items, ensure_ascii=False, indent=2)}

출력 형식: JSON 배열만 반환하세요. 다른 텍스트 없이.
예시: [{{"brand":"모두공","category":"건의","content":"수강앱 확대기능 요청","count":5}}]
"""

    message = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()
    # 마크다운 코드블록 제거
    raw = re.sub(r"```json|```", "", raw).strip()

    try:
        merged = json.loads(raw)
        return merged
    except json.JSONDecodeError:
        # 파싱 실패 시 원본 반환
        print("⚠️ Claude 병합 응답 파싱 실패, 원본 사용")
        return items


# ─────────────────────────────────────────────────────────
# 5. 5채널 포스팅 메시지 포맷 생성
# ─────────────────────────────────────────────────────────
def format_voc_message(merged_items: list[dict]) -> str:
    today_str = datetime.now(KST).strftime("%-m월 %-d일")

    # 브랜드별로 그룹핑
    brand_groups: dict[str, list[dict]] = {}
    for item in merged_items:
        brand = item["brand"]
        if brand not in brand_groups:
            brand_groups[brand] = []
        brand_groups[brand].append(item)

    lines = [f"*{today_str} 주요 VOC 공유*", "자세한 내용이 필요하시다면 CX팀에 문의해 주세요.\n"]

    for brand, group_items in brand_groups.items():
        lines.append(f"*{brand}*")
        for item in group_items:
            lines.append(f"[{item['category']}] {item['content']} - {item['count']}건")
        lines.append("")  # 브랜드 사이 빈 줄

    return "\n".join(lines).strip()


# ─────────────────────────────────────────────────────────
# 6. 메인 실행
# ─────────────────────────────────────────────────────────
def main():
    print(f"[{datetime.now(KST).strftime('%Y-%m-%d %H:%M')}] VOC 자동화 시작")

    # Step 1: 오늘 VOC봇 메시지 찾기
    voc_msg = find_todays_voc_message()
    if not voc_msg:
        print("오늘 VOC봇 메시지 없음 → 종료")
        return

    print(f"VOC봇 메시지 발견: ts={voc_msg['ts']}")

    # Step 2: 스레드 댓글 가져오기
    replies = get_thread_replies(voc_msg["ts"])
    if not replies:
        print("스레드 댓글 없음 → 5채널 미게시, 종료")
        return

    print(f"댓글 {len(replies)}개 발견")

    # Step 3: 댓글 파싱
    parsed_items = []
    for reply in replies:
        text = reply.get("text", "")
        parsed = parse_reply(text)
        if parsed:
            parsed_items.append(parsed)
        else:
            print(f"⚠️ 파싱 실패한 댓글 스킵: {text[:50]}")

    if not parsed_items:
        print("파싱된 항목 없음 → 종료")
        return

    print(f"파싱 완료: {len(parsed_items)}개 항목")

    # Step 4: Claude로 유사 항목 병합
    merged_items = merge_similar_items(parsed_items)
    print(f"병합 후: {len(merged_items)}개 항목")

    # Step 5: 메시지 포맷
    message_text = format_voc_message(merged_items)
    print("=== 생성된 메시지 ===")
    print(message_text)
    print("====================")

    # Step 6: 5채널에 포스팅
    try:
        slack.chat_postMessage(
            channel=TARGET_CHANNEL_ID,
            text=message_text,
            mrkdwn=True
        )
        print("✅ 5채널 게시 완료!")
    except SlackApiError as e:
        raise RuntimeError(f"5채널 게시 실패: {e.response['error']}")


if __name__ == "__main__":
    main()
