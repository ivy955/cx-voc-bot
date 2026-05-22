from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import datetime
import holidays
import os

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SOURCE_CHANNEL_ID = "C018LUAJHS4"   # 2_share_cs_커뮤니케이션
MY_USER_ID = "U08QR91TVK5"          # 본인 User ID
MY_DM_CHANNEL_ID = "D08QR92129Z"    # 본인 DM 채널 ID
DONE_EMOJI = "완료"                  # 커스텀 완료 이모지 이름

KST = datetime.timezone(datetime.timedelta(hours=9))

client = WebClient(token=SLACK_BOT_TOKEN)

def get_business_days_elapsed(msg_date, today):
    """메시지 날짜로부터 영업일 기준 경과일 계산"""
    kr_holidays = holidays.KR()
    count = 0
    current = msg_date + datetime.timedelta(days=1)
    while current <= today:
        if current.weekday() < 5 and current not in kr_holidays:
            count += 1
        current += datetime.timedelta(days=1)
    return count

def is_business_day(date):
    kr_holidays = holidays.KR()
    return date.weekday() < 5 and date not in kr_holidays

def check_and_remind():
    today = datetime.datetime.now(KST).date()

    # 영업일 아니면 종료
    if not is_business_day(today):
        print("주말 또는 공휴일 → 종료")
        return

    # 최근 30일치 메시지 조회
    oldest = (datetime.datetime.now(KST) - datetime.timedelta(days=30)).timestamp()

    try:
        result = client.conversations_history(
            channel=SOURCE_CHANNEL_ID,
            oldest=str(oldest),
            limit=200
        )
    except SlackApiError as e:
        print(f"채널 조회 실패: {e.response['error']}")
        return

    overdue_messages = []

    for msg in result.get("messages", []):
        # 본인이 올린 메시지만
        if msg.get("user") != MY_USER_ID:
            continue

        # 완료 이모지 체크
        reactions = msg.get("reactions", [])
        done = any(r["name"] == DONE_EMOJI for r in reactions)
        if done:
            continue

        # 영업일 2일 초과 체크
        msg_ts = float(msg["ts"])
        msg_date = datetime.datetime.fromtimestamp(msg_ts, KST).date()
        elapsed = get_business_days_elapsed(msg_date, today)

        if elapsed > 2:
            overdue_messages.append({
                "text": msg.get("text", "")[:50],
                "elapsed": elapsed,
                "ts": msg["ts"]
            })

    if not overdue_messages:
        print("미완료 메시지 없음 → 종료")
        return

    # DM으로 리마인드 발송
    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "⏰ 미완료 메시지가 있어요!"}
        }
    ]

    for m in overdue_messages:
        link = f"https://bravecompanyworkspace.slack.com/archives/{SOURCE_CHANNEL_ID}/p{m['ts'].replace('.', '')}"
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"📌 [{m['elapsed']}일 전] {m['text']}...\n`{link}`"
            }
        })

    try:
        client.chat_postMessage(
            channel=MY_DM_CHANNEL_ID,
            blocks=blocks,
            text="⏰ 미완료 메시지가 있어요!"
        )
        print(f"✅ 리마인드 {len(overdue_messages)}건 발송 완료")
    except SlackApiError as e:
        print(f"❌ DM 발송 실패: {e.response['error']}")

if __name__ == "__main__":
    check_and_remind()