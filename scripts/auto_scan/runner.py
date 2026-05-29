"""UGC 모니터 자동 스캔 — 매일 13:00 launchd로 발동.

흐름:
1. 마스터 시트 읽기
2. 게시일=어제 + 노션URL 있음 + 자동스캔_큐 미등록인 row 필터
3. 각 row마다:
   - 노션 → 프롬프트 + 이미지
   - Apify → 댓글 CSV
   - /scan API POST
   - 완료 대기 (폴링)
   - 슬랙 알림
   - 자동스캔_큐 status 업데이트
"""
from __future__ import annotations
import os, sys, json, time, datetime, requests, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "auto_scan"))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "ugc-monitor-api" / ".env")

import gspread
from google.oauth2.service_account import Credentials

from notion_extract import fetch_campaign
from apify_comments import fetch_comments_csv
from slack_notify import notify_scan_done, notify_scan_error, notify

# ── 설정 ─────────────────────────────────────────
MASTER_SHEET_ID = "1HkiTAyPA1XLSuWJf6WSwCcHZ_6GFS94z9Kf8W-XO2po"
MASTER_GID      = 1513961355
QUEUE_TAB       = "자동스캔_큐"
API_BASE        = "http://127.0.0.1:8000"
DASHBOARD_URL   = "http://localhost:8000"
GOOGLE_CREDS    = str(ROOT / "ugc-monitor-api" / "config" / "google_credentials.json")
LOG_DIR         = Path.home() / ".ugc_monitor" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str):
    """stdout + 파일 로그."""
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    log_file = LOG_DIR / f"auto_scan_{datetime.datetime.now():%Y-%m-%d}.log"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _sheet_client():
    creds = Credentials.from_service_account_file(
        GOOGLE_CREDS,
        scopes=["https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"],
    )
    return gspread.authorize(creds).open_by_key(MASTER_SHEET_ID)


def _parse_kr_date(s: str) -> datetime.date | None:
    """마스터 시트의 '업로드 일자' 형식: '25/12/4', '26/05/27' 등 → date."""
    if not s or "/" not in s:
        return None
    try:
        parts = s.strip().split("/")
        if len(parts) != 3:
            return None
        yy, mm, dd = (int(p) for p in parts)
        year = 2000 + yy if yy < 100 else yy
        return datetime.date(year, mm, dd)
    except (ValueError, IndexError):
        return None


def _read_queue_post_urls(ss) -> set[str]:
    """자동스캔_큐 탭에서 이미 처리/진행 중인 게시물 URL 집합."""
    try:
        ws = ss.worksheet(QUEUE_TAB)
        rows = ws.get_all_values()[1:]
        urls = set()
        for r in rows:
            if len(r) >= 4 and r[3] and r[8] in ("queued", "running", "done"):
                urls.add(r[3].strip())
        return urls
    except gspread.WorksheetNotFound:
        return set()


def _queue_append(ss, row: list):
    """자동스캔_큐 에 한 행 추가."""
    ws = ss.worksheet(QUEUE_TAB)
    ws.append_row(row, value_input_option="RAW")


def _queue_update(ss, post_url: str, status: str, result: str = "", error: str = ""):
    """자동스캔_큐 의 해당 post_url 행 status/처리일시/결과/에러 업데이트."""
    ws = ss.worksheet(QUEUE_TAB)
    rows = ws.get_all_values()
    if not rows:
        return
    header = rows[0]
    # 컬럼 인덱스 (0-based)
    try:
        url_col   = header.index("게시물URL")
        st_col    = header.index("status")
        ts_col    = header.index("처리일시")
        res_col   = header.index("결과")
        err_col   = header.index("에러")
    except ValueError:
        return
    # 가장 최근 동일 URL 행 (아래에서 위로)
    target_row = None
    for i in range(len(rows) - 1, 0, -1):
        if len(rows[i]) > url_col and rows[i][url_col] == post_url:
            target_row = i + 1
            break
    if not target_row:
        return
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    updates = [
        {"range": f"'{QUEUE_TAB}'!{gspread.utils.rowcol_to_a1(target_row, st_col + 1)}",  "values": [[status]]},
        {"range": f"'{QUEUE_TAB}'!{gspread.utils.rowcol_to_a1(target_row, ts_col + 1)}",  "values": [[now]]},
        {"range": f"'{QUEUE_TAB}'!{gspread.utils.rowcol_to_a1(target_row, res_col + 1)}", "values": [[result]]},
        {"range": f"'{QUEUE_TAB}'!{gspread.utils.rowcol_to_a1(target_row, err_col + 1)}", "values": [[error]]},
    ]
    ss.values_batch_update({"valueInputOption": "RAW", "data": updates})


def find_target_campaigns(target_date: datetime.date) -> list[dict]:
    """마스터 시트에서 target_date (어제) 게시 + 노션URL 있음 + 자동스캔_큐 미등록인 캠페인."""
    ss = _sheet_client()
    ws = ss.get_worksheet_by_id(MASTER_GID)
    rows = ws.get_all_values()
    # 헤더 5행 → 데이터 6행~
    header = rows[4] if len(rows) > 4 else []

    def col(name: str, default: int) -> int:
        for i, h in enumerate(header):
            if name in (h or "").replace("\n", " "):
                return i
        return default
    date_col   = col("업로드 일자", 0)
    topic_col  = col("주제 내용", 1)
    url_col    = col("게재 URL", 2)
    maker_col  = col("제작자", 15)
    notion_col = col("노션 프롬프트 URL", 16)

    already_queued = _read_queue_post_urls(ss)
    out = []
    for r in rows[5:]:
        if len(r) <= max(date_col, url_col, notion_col):
            continue
        date_str   = r[date_col]
        d          = _parse_kr_date(date_str)
        topic      = r[topic_col] if len(r) > topic_col else ""
        post_url   = r[url_col] if len(r) > url_col else ""
        notion_url = r[notion_col] if len(r) > notion_col else ""
        maker      = r[maker_col] if len(r) > maker_col else ""
        if not d or d != target_date:
            continue
        if not post_url or "instagram.com" not in post_url:
            continue
        if not notion_url or "notion" not in notion_url:
            continue
        if post_url in already_queued:
            continue
        out.append({
            "date": date_str,
            "campaign": topic.strip(),
            "post_url": post_url.strip(),
            "notion_url": notion_url.strip(),
            "reviewer": maker.strip() or "auto",
        })
    return out


def process_campaign(c: dict, ss) -> dict:
    """단일 캠페인 자동 처리.
    1) 노션 → 프롬프트 + 이미지
    2) Apify → 댓글 CSV
    3) /scan API
    4) 완료 폴링
    """
    log(f"  → 노션 페이지 가져오기: {c['notion_url']}")
    notion = fetch_campaign(c['notion_url'], download_images=True)
    if not notion.get("prompt"):
        raise RuntimeError("노션 페이지에 프롬프트(code 블록)가 없음")
    if not notion.get("images"):
        raise RuntimeError("노션 페이지에 첨부 이미지가 없음 (최소 1장 필요)")
    log(f"     프롬프트 {len(notion['prompt'])}자, 이미지 {len(notion['images'])}장")

    log(f"  → 댓글 수집 (Apify): {c['post_url']}")
    csv_bytes = fetch_comments_csv(c['post_url'], limit=1500)
    log(f"     CSV 크기: {len(csv_bytes)} bytes")

    log(f"  → /scan API 호출")
    files = [("comment_file", (f"comments_{c['campaign']}.csv", csv_bytes, "text/csv"))]
    for i, img in enumerate(notion["images"][:5], 1):
        files.append((f"reference_image_{i}", (f"ref_{i}.jpg", img, "image/jpeg")))
    data = {
        "post_url": c["post_url"],
        "prompt_text": notion["prompt"],
        "campaign_name": c["campaign"],
        "reviewer": c["reviewer"],
        "lenient_mode": "true",
    }
    r = requests.post(f"{API_BASE}/scan", data=data, files=files, timeout=60)
    if r.status_code >= 300:
        raise RuntimeError(f"/scan HTTP {r.status_code}: {r.text[:300]}")
    log(f"     스캔 시작됨")

    # 완료 폴링 (최대 3시간 — 583명대 대형 스캔은 ~70분 걸림)
    deadline = time.time() + 10800
    stats = None
    while time.time() < deadline:
        time.sleep(30)
        rr = requests.get(f"{API_BASE}/results", timeout=15)
        d = rr.json() if rr.status_code == 200 else {}
        st = d.get("status")
        prog = d.get("progress", 0)
        if st == "done":
            stats = d.get("stats", {})
            log(f"     완료. stats={stats}")
            break
        elif st in ("error", "failed"):
            raise RuntimeError(f"스캔 실패: {d.get('step', '?')}")
        log(f"     progress={prog}% | {d.get('step', '')}")
    if stats is None:
        raise RuntimeError("스캔 완료 폴링 timeout (3시간)")
    return stats


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="대상 게시일 (YYYY-MM-DD). 기본: 어제")
    args = p.parse_args()
    today = datetime.date.today()
    if args.date:
        yesterday = datetime.date.fromisoformat(args.date)
    else:
        yesterday = today - datetime.timedelta(days=1)
    log(f"=== 자동 스캔 시작 — 대상 게시일: {yesterday} ===")

    try:
        ss = _sheet_client()
        targets = find_target_campaigns(yesterday)
    except Exception as e:
        log(f"❌ 마스터 시트 읽기 실패: {e}")
        notify(f"⚠️ [UGC 모니터] 자동 스캔 시작 실패\n```{str(e)[:300]}```")
        return

    if not targets:
        log(f"대상 캠페인 0건 — 종료 (어제 {yesterday} 게시 + 노션URL 있음 + 미등록 row 없음)")
        return

    log(f"대상 캠페인 {len(targets)}건:")
    for c in targets:
        log(f"  · {c['campaign']} — {c['post_url']}")

    # 각 캠페인 처리
    for c in targets:
        log(f"\n[{c['campaign']}] 처리 시작")
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 큐에 queued 행 추가
        _queue_append(ss, [
            now, c["campaign"], c["date"], c["post_url"],
            "",  # 프롬프트 (요약만 들어가도 됨, 지금은 빈 값)
            "",  # 레퍼런스폴더 (노션이라서 빈 값)
            c["reviewer"], "TRUE", "running", "", "", ""
        ])
        try:
            stats = process_campaign(c, ss)
            feed    = stats.get("feed", 0)
            story   = stats.get("story", 0)
            profile = stats.get("profile", 0)
            total   = feed + story + profile
            result_str = f"감지 {total}명 (피드 {feed} / 스토리 {story} / 프사 {profile})"
            _queue_update(ss, c["post_url"], "done", result=result_str)
            notify_scan_done(c["campaign"], stats, dashboard_url=DASHBOARD_URL)
            log(f"  ✅ 완료: {result_str}")
        except Exception as e:
            tb = traceback.format_exc()
            err_msg = f"{type(e).__name__}: {e}"
            log(f"  ❌ 실패: {err_msg}\n{tb}")
            _queue_update(ss, c["post_url"], "failed", error=err_msg)
            notify_scan_error(c["campaign"], err_msg)

    log(f"\n=== 자동 스캔 종료 ===\n")


if __name__ == "__main__":
    main()
