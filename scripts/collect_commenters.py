"""
collect_commenters.py  [v3 — source_post_url 컬럼 추가]
────────────────────────────────────────────────────
특정 인스타그램 게시물 URL의 댓글 유저를
Apify로 수집해서 Google Sheets 'ugc_users' 탭에 저장합니다.
어느 게시물 댓글인지 source_post_url 컬럼과 함께 기록합니다.

실행 방법:
  python collect_commenters.py --url https://www.instagram.com/p/ABC123/
  python collect_commenters.py --url https://www.instagram.com/p/AAA/,https://www.instagram.com/p/BBB/
"""

import os, sys, time, argparse, requests
from datetime import datetime, timezone
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials

load_dotenv()

APIFY_API_TOKEN    = os.getenv("APIFY_API_TOKEN")
SPREADSHEET_ID     = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS_PATH", "config/google_credentials.json")
SHEET_TAB_NAME     = "ugc_users"
MY_IG_ID           = "pitapat_prompt"
POST_URLS_ENV      = os.getenv("POST_URLS", "")
ACTOR_COMMENTS     = "apify~instagram-comment-scraper"
APIFY_BASE         = "https://api.apify.com/v2"

# 헤더 정의 (source_post_url 추가)
HEADERS = [
    "username", "instagram_id", "added_at", "last_checked",
    "last_profile_url", "has_story", "is_ugc_detected",
    "ugc_type", "ugc_detected_at", "source_post_url", "notes"
]


# ── 1. Apify 댓글 수집 ─────────────────────────
def scrape_comments(post_urls: list[str]) -> list[dict]:
    print(f"\n[1단계] 댓글 수집 — {len(post_urls)}개 게시물")
    for u in post_urls:
        print(f"  · {u}")

    resp = requests.post(
        f"{APIFY_BASE}/acts/{ACTOR_COMMENTS}/runs?token={APIFY_API_TOKEN}",
        json={"directUrls": post_urls, "resultsLimit": 500},
        timeout=30,
    )
    resp.raise_for_status()

    run_id = resp.json()["data"]["id"]
    print(f"  → Run ID: {run_id}  대기 중", end="", flush=True)

    deadline = time.time() + 200
    status_data = {}
    while time.time() < deadline:
        time.sleep(5)
        r = requests.get(f"{APIFY_BASE}/actor-runs/{run_id}?token={APIFY_API_TOKEN}", timeout=15)
        status_data = r.json()["data"]
        print(".", end="", flush=True)
        if status_data["status"] == "SUCCEEDED":
            print(" 완료!")
            break
        if status_data["status"] in ("FAILED", "ABORTED", "TIMED-OUT"):
            print(f"\n  ❌ 실패: {status_data['status']}")
            return []

    dataset_id = status_data.get("defaultDatasetId", "")
    if not dataset_id:
        print("  ❌ 데이터셋 ID 없음")
        return []

    items = requests.get(
        f"{APIFY_BASE}/datasets/{dataset_id}/items?token={APIFY_API_TOKEN}",
        timeout=30,
    ).json()
    print(f"  ✅ {len(items)}개 댓글 수집")
    return items


# ── 2. 유저명 추출 (게시물 URL 매핑 포함) ────────
def extract_usernames(comments: list[dict]) -> dict[str, str]:
    """{ username: source_post_url } 딕셔너리 반환"""
    user_map = {}
    for c in comments:
        u = (
            c.get("ownerUsername")
            or c.get("username")
            or (c.get("owner") or {}).get("username")
        )
        if u and u.lower() != MY_IG_ID.lower():
            # 댓글이 달린 게시물 URL 추출
            post_url = (
                c.get("postUrl")
                or c.get("url")
                or (c.get("postShortCode") and f"https://www.instagram.com/p/{c['postShortCode']}/")
                or ""
            )
            if u not in user_map:
                user_map[u] = post_url
    print(f"  ✅ 고유 유저 {len(user_map)}명 (본인 계정 제외)")
    return user_map


# ── 3. Sheets 연결 ─────────────────────────────
def get_sheet():
    creds = Credentials.from_service_account_file(
        GOOGLE_CREDENTIALS,
        scopes=["https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"],
    )
    client = gspread.authorize(creds)
    ss = client.open_by_key(SPREADSHEET_ID)
    try:
        sheet = ss.worksheet(SHEET_TAB_NAME)
        print(f"  ✅ 기존 탭 '{SHEET_TAB_NAME}' 연결")
        # 기존 시트에 source_post_url 컬럼 없으면 추가
        existing_headers = sheet.row_values(1)
        if "source_post_url" not in existing_headers:
            col_idx = len(existing_headers) + 1
            sheet.update_cell(1, col_idx, "source_post_url")
            print(f"  ✅ 'source_post_url' 컬럼 추가 (열 {col_idx})")
    except gspread.exceptions.WorksheetNotFound:
        sheet = ss.add_worksheet(title=SHEET_TAB_NAME, rows=2000, cols=12)
        sheet.append_row(HEADERS)
        print(f"  ✅ 새 탭 '{SHEET_TAB_NAME}' 생성")
    return sheet


# ── 4. Sheets 저장 ─────────────────────────────
def save_users(sheet, user_map: dict[str, str], post_urls: list[str]) -> dict:
    all_values = sheet.get_all_values()
    headers = all_values[0] if all_values else HEADERS

    # source_post_url 컬럼 인덱스 찾기
    try:
        src_col_idx = headers.index("source_post_url")
    except ValueError:
        src_col_idx = len(headers) - 1  # fallback

    existing_usernames = {r[0].strip() for r in all_values[1:] if r and r[0]}
    to_add   = {u: v for u, v in user_map.items() if u not in existing_usernames}
    skipped  = len(user_map) - len(to_add)

    # 입력된 post_url을 source로 사용 (Apify가 못 준 경우 대비)
    source_url = post_urls[0] if post_urls else ""

    print(f"\n[3단계] 기존 {len(existing_usernames)}명 | 신규 {len(user_map)}명 → 추가 {len(to_add)}명 | 스킵 {skipped}명")

    if to_add:
        now  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = []
        for u, detected_url in sorted(to_add.items()):
            final_src = detected_url or source_url
            row = [""] * len(HEADERS)
            row[HEADERS.index("username")]        = u
            row[HEADERS.index("added_at")]        = now
            row[HEADERS.index("ugc_type")]        = "none"
            row[HEADERS.index("source_post_url")] = final_src
            rows.append(row)

        sheet.append_rows(rows, value_input_option="RAW")
        print(f"  ✅ {len(to_add)}명 추가!")
    else:
        print("  → 추가할 신규 유저 없음")

    return {"added": len(to_add), "skipped": skipped, "total": len(existing_usernames) + len(to_add)}


# ── 메인 ───────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", type=str, default=None,
                        help="게시물 URL (쉼표로 여러 개 가능)")
    args = parser.parse_args()

    print("=" * 55)
    print("  UGC Monitor — Phase 1: 댓글 유저 수집")
    print("=" * 55)

    missing = [k for k, v in [("APIFY_API_TOKEN", APIFY_API_TOKEN),
                                ("SPREADSHEET_ID", SPREADSHEET_ID)] if not v]
    if missing:
        print(f"❌ .env 파일에 없는 값: {', '.join(missing)}")
        sys.exit(1)
    if not os.path.exists(GOOGLE_CREDENTIALS):
        print(f"❌ Google 인증 파일 없음: {GOOGLE_CREDENTIALS}")
        sys.exit(1)

    raw = args.url or POST_URLS_ENV
    if not raw:
        print("❌ 게시물 URL을 입력해주세요.")
        print("   python collect_commenters.py --url https://www.instagram.com/p/XXX/")
        sys.exit(1)
    post_urls = [u.strip() for u in raw.split(",") if u.strip()]

    comments = scrape_comments(post_urls)
    if not comments:
        print("수집된 댓글 없음. 종료합니다.")
        sys.exit(0)

    print(f"\n[2단계] 유저명 추출 중...")
    user_map = extract_usernames(comments)

    print(f"\n[2단계] Google Sheets 연결 중...")
    sheet = get_sheet()
    stats = save_users(sheet, user_map, post_urls)

    print("\n" + "=" * 55)
    print(f"  ✅ 완료! 추가 {stats['added']}명 | 스킵 {stats['skipped']}명 | 전체 {stats['total']}명")
    print("=" * 55)


if __name__ == "__main__":
    main()
