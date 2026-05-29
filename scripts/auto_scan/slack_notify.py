"""Slack Incoming Webhook 알림."""
from __future__ import annotations
import os, requests

SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK_URL",
    # 기본: "프롬프트 테스트 알림이" 봇 → #team_kr-contents_mkt
    "https://hooks.slack.com/services/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")


def notify(text: str) -> bool:
    """단순 텍스트 메시지. 성공 시 True."""
    if not SLACK_WEBHOOK:
        print("[slack] webhook URL 미설정 — 알림 스킵")
        return False
    try:
        r = requests.post(SLACK_WEBHOOK, json={"text": text}, timeout=10)
        return r.status_code < 300
    except Exception as e:
        print(f"[slack] 알림 실패: {e}")
        return False


def notify_scan_done(campaign: str, stats: dict, dashboard_url: str = "http://localhost:8000") -> bool:
    """스캔 완료 알림 — 사용자가 확정한 포맷."""
    feed    = stats.get("feed", 0)
    story   = stats.get("story", 0)
    profile = stats.get("profile", 0)
    total   = feed + story + profile
    text = (
        f"🤖 [오가닉 모니터링]  @{campaign}  스캔 완료\n\n"
        f"✅ 감지: {total}명 (피드 {feed} / 스토리 {story} / 프사 {profile})\n"
        f"⏳ 검수 대기 중\n\n"
        f"🔗 결과 보기: {dashboard_url}"
    )
    return notify(text)


def notify_scan_error(campaign: str, err: str) -> bool:
    text = (
        f"⚠️ [오가닉 모니터링]  @{campaign}  스캔 실패\n\n"
        f"```{err[:500]}```"
    )
    return notify(text)


if __name__ == "__main__":
    # 테스트 호출 (실제 메시지 가니까 주의)
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--send":
        ok = notify_scan_done("테스트캠페인", {"feed": 5, "story": 3, "profile": 2})
        print(f"전송: {'OK' if ok else 'FAIL'}")
    else:
        print("실제 전송하려면: python slack_notify.py --send")
