from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import datetime
import holidays
import os

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
CHANNEL_ID = "C02CH01V8E4"

KST = datetime.timezone(datetime.timedelta(hours=9))
client = WebClient(token=SLACK_BOT_TOKEN)

def is_business_day(date):
    kr_holidays = holidays.KR()
    return date.weekday() < 5 and date not in kr_holidays

def send_voc_message():
    today = datetime.datetime.now(KST)
    if is_business_day(today):
        date_str = f"{today.month}월 {today.day}일"
        message = f"""`VOC` {date_str} VOC 취합

voc 내용을 댓글로 남겨주세요
> voc 내용은 아래와 같은 양식으로 부탁드립니다.
> 사이트명 [건의] / [불만] VOC 내용 - 건수
> 예시) 모두경 [건의] 23년 승진대비 커리큘럼 제공요청 - 1건"""

        try:
            response = client.chat_postMessage(channel=CHANNEL_ID, text=message)
            print(f"✅ 메시지 전송 성공:", response['ts'])
        except SlackApiError as e:
            print(f"❌ 메시지 전송 실패:", e.response['error'])
    else:
        print("⛔ 주말 또는 공휴일입니다.")

if __name__ == "__main__":
    send_voc_message()
