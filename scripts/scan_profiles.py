"""
scan_profiles.py  [Phase 2 — 프로필 스캔]
──────────────────────────────────────────────────────
Google Sheets의 ugc_users 탭에서 유저 목록을 읽어
Apify Profile Scraper로 프사 변경 / 스토리 / 피드를 스캔합니다.
변화가 감지된 유저만 Sheets에 업데이트하고 Phase 3 대상으로 표시합니다.

실행 방법:
  python scan_profiles.py              # 전체 유저 스캔
  python scan_profiles.py --limit 20   # 최근 추가된 20명만 스캔 (테스트용)
"""

import os, sys, time, argparse, requests, json
from datetime import datetime, timezone
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials

load_dotenv()

APIFY_API_TOKEN    = os.getenv("APIFY_API_TOKEN")
SPREADSHEET_ID     = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS_PATH", "config/google_credentials.json")
SHEET_TAB_NAME     = "ugc_users"
ACTOR_PROFILE      = "apify~instagram-profile-scraper"
APIFY_BASE         = "https://api.apify.com/v2"

# Sheets 헤더 컬럼 인덱스 (0-based)
COL = {
    "username":         0,
    "instagram_id":     1,
    "added_at":         2,
    "last_checked":     3,
    "last_profile_url": 4,
    "has_story":        5,
    "is_ugc_detected":  6,
    "ugc_type":         7,
    "ugc_detected_at":  8,
    "source_post_url":  9,
    "notes":            10,
    # Phase 2에서 추가될 컬럼
    "latest_feed_url":  11,
    "profile_changed":  12,
    "scan_status":      13,
}


# ── 1. Sheets에서 유저 목록 읽기 ──────────────────
def get_sheet_and_users(limit: int = None):
    creds = Credentials.from_service_account_file(
        GOOGLE_CREDENTIALS,
        scopes=["https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"],
    )
    client = gspread.authorize(creds)
    ss     = client.open_by_key(SPREADSHEET_ID)
    sheet  = ss.worksheet(SHEET_TAB_NAME)

    all_values = sheet.get_all_values()
    headers    = all_values[0]
    rows       = all_values[1:]

    # Phase 2 전용 컬럼 없으면 추가
    new_cols = ["latest_feed_url", "profile_changed", "scan_status"]
    for col_name in new_cols:
        if col_name not in headers:
            headers.append(col_name)
            col_num = len(headers)
            sheet.update_cell(1, col_num, col_name)
            print(f"  컬럼 추가: '{col_name}' (열 {col_num})")

    # 헤더 → 인덱스 매핑 (동적으로)
    h = {name: i for i, name in enumerate(headers)}

    # 유저 목록 파싱
    users = []
    for i, row in enumerate(rows):
        # 행 길이 맞추기
        while len(row) < len(headers):
            row.append("")
        username = row[h.get("username", 0)].strip()
        if not username:
            continue
        users.append({
            "row_index": i + 2,  # 1-based, 헤더 제외
            "username":  username,
            "last_profile_url": row[h.get("last_profile_url", 4)],
            "headers_map": h,
        })

    if limit:
        users = users[:limit]

    print(f"  총 {len(users)}명 스캔 대상")
    return sheet, users, h


# ── 2. Apify Profile Scraper 실행 ─────────────────
def scrape_profiles(usernames: list[str]) -> list[dict]:
    print(f"\n[2단계] Apify 프로필 스캔 — {len(usernames)}명")

    # Apify는 한 번에 50명씩 처리 (안정적)
    all_results = []
    chunks = [usernames[i:i+50] for i in range(0, len(usernames), 50)]

    for chunk_idx, chunk in enumerate(chunks):
        print(f"  배치 {chunk_idx+1}/{len(chunks)} ({len(chunk)}명) 실행 중...")

        resp = requests.post(
            f"{APIFY_BASE}/acts/{ACTOR_PROFILE}/runs?token={APIFY_API_TOKEN}",
            json={"usernames": chunk, "resultsLimit": 1, "_triggeredBy": "지원", "_project": "프롬프트 오가닉 모니터링"},
            timeout=30,
        )
        resp.raise_for_status()

        run_id = resp.json()["data"]["id"]
        print(f"  → Run ID: {run_id}  대기 중", end="", flush=True)

        deadline = time.time() + 180
        status_data = {}
        while time.time() < deadline:
            time.sleep(6)
            r = requests.get(
                f"{APIFY_BASE}/actor-runs/{run_id}?token={APIFY_API_TOKEN}",
                timeout=15
            )
            status_data = r.json()["data"]
            print(".", end="", flush=True)
            if status_data["status"] == "SUCCEEDED":
                print(" 완료!")
                break
            if status_data["status"] in ("FAILED", "ABORTED", "TIMED-OUT"):
                print(f"\n  ❌ 배치 실패: {status_data['status']}")
                break

        dataset_id = status_data.get("defaultDatasetId", "")
        if dataset_id:
            items = requests.get(
                f"{APIFY_BASE}/datasets/{dataset_id}/items?token={APIFY_API_TOKEN}",
                timeout=30,
            ).json()
            all_results.extend(items)

        # 배치 간 딜레이 (API 부하 방지)
        if chunk_idx < len(chunks) - 1:
            time.sleep(3)

    print(f"  ✅ 총 {len(all_results)}개 프로필 데이터 수집")
    return all_results


# ── 3. 변화 감지 및 Sheets 업데이트 ──────────────
def detect_and_update(sheet, users: list[dict], profiles: list[dict], headers_map: dict):
    # username → 프로필 데이터 매핑
    profile_map = {}
    for p in profiles:
        uname = p.get("username") or p.get("inputUrl", "").split("/")[-2]
        if uname:
            profile_map[uname.lower()] = p

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    changed_users = []
    updates = []

    for user in users:
        username  = user["username"]
        row_idx   = user["row_index"]
        prev_url  = user["last_profile_url"]
        h         = user["headers_map"]

        profile = profile_map.get(username.lower())
        if not profile:
            # 프로필 못 가져온 경우 — 비공개 또는 삭제 계정
            updates.append({"range": f"D{row_idx}", "values": [[now]]})  # last_checked
            continue

        # 데이터 추출
        curr_profile_url = profile.get("profilePicUrl") or profile.get("profilePicUrlHD") or ""
        has_story        = profile.get("hasPublicStory", False)
        latest_posts     = profile.get("latestPosts") or profile.get("posts") or []
        latest_feed_url  = latest_posts[0].get("url") or latest_posts[0].get("displayUrl", "") if latest_posts else ""
        is_private       = profile.get("isPrivate", False)

        # 변화 감지
        profile_changed = bool(curr_profile_url and prev_url and curr_profile_url != prev_url)
        has_activity    = has_story or bool(latest_feed_url) or profile_changed

        # Sheets 업데이트 준비 (행 전체를 한번에)
        # 컬럼 위치 동적으로 찾기
        def col_letter(col_name):
            idx = h.get(col_name)
            if idx is None: return None
            # 숫자 → 엑셀 컬럼 문자 변환 (A=0, B=1, ...)
            result = ""
            n = idx
            while True:
                result = chr(65 + n % 26) + result
                n = n // 26 - 1
                if n < 0: break
            return result

        # 개별 셀 업데이트 배치
        cell_updates = {
            "last_checked":     now,
            "last_profile_url": curr_profile_url,
            "has_story":        "TRUE" if has_story else "FALSE",
            "latest_feed_url":  latest_feed_url,
            "profile_changed":  "TRUE" if profile_changed else "FALSE",
            "scan_status":      "scanned",
        }

        for col_name, value in cell_updates.items():
            cl = col_letter(col_name)
            if cl:
                updates.append({"range": f"{cl}{row_idx}", "values": [[value]]})

        if has_activity and not is_private:
            changed_users.append({
                "username":        username,
                "profile_changed": profile_changed,
                "has_story":       has_story,
                "has_feed":        bool(latest_feed_url),
                "profile_url":     curr_profile_url,
                "latest_feed_url": latest_feed_url,
            })

    # Sheets 배치 업데이트 (한 번에 전송)
    if updates:
        print(f"\n  Sheets 업데이트 중 ({len(updates)}개 셀)...")
        # 100개씩 나눠서 전송
        for i in range(0, len(updates), 100):
            chunk = updates[i:i+100]
            sheet.spreadsheet.values_batch_update({
                "valueInputOption": "RAW",
                "data": chunk,
            })
        print(f"  ✅ Sheets 업데이트 완료")

    return changed_users


# ── 4. 결과 출력 ──────────────────────────────────
def print_results(changed_users: list[dict]):
    print(f"\n{'='*55}")
    print(f"  Phase 2 완료 — 변화 감지 결과")
    print(f"{'='*55}")

    profile_changed = [u for u in changed_users if u["profile_changed"]]
    has_story       = [u for u in changed_users if u["has_story"]]
    has_feed        = [u for u in changed_users if u["has_feed"]]

    print(f"  프사 변경:   {len(profile_changed)}명")
    print(f"  스토리 있음: {len(has_story)}명")
    print(f"  피드 있음:   {len(has_feed)}명")
    print(f"  (중복 포함 — 한 유저가 여러 항목 해당 가능)")
    print(f"\n  → Phase 3 Gemini 판별 대상: {len(changed_users)}명")
    print(f"{'='*55}")

    if changed_users:
        print("\n  감지된 유저 목록:")
        for u in changed_users:
            flags = []
            if u["profile_changed"]: flags.append("프사변경")
            if u["has_story"]:       flags.append("스토리")
            if u["has_feed"]:        flags.append("피드")
            print(f"  · @{u['username']} — {' / '.join(flags)}")

    # Phase 3에 넘길 JSON 저장
    output_path = "phase3_candidates.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(changed_users, f, ensure_ascii=False, indent=2)
    print(f"\n  Phase 3 대상 저장: {output_path}")


# ── 메인 ───────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                        help="스캔할 유저 수 제한 (테스트용, 기본값: 전체)")
    args = parser.parse_args()

    print("=" * 55)
    print("  UGC Monitor — Phase 2: 프로필 스캔")
    print("=" * 55)

    missing = [k for k, v in [("APIFY_API_TOKEN", APIFY_API_TOKEN),
                                ("SPREADSHEET_ID", SPREADSHEET_ID)] if not v]
    if missing:
        print(f"❌ .env 파일에 없는 값: {', '.join(missing)}")
        sys.exit(1)
    if not os.path.exists(GOOGLE_CREDENTIALS):
        print(f"❌ Google 인증 파일 없음: {GOOGLE_CREDENTIALS}")
        sys.exit(1)

    print(f"\n[1단계] Google Sheets 유저 목록 읽는 중...")
    sheet, users, headers_map = get_sheet_and_users(limit=args.limit)

    if not users:
        print("스캔할 유저가 없습니다. Phase 1을 먼저 실행해주세요.")
        sys.exit(0)

    usernames = [u["username"] for u in users]
    profiles  = scrape_profiles(usernames)

    print(f"\n[3단계] 변화 감지 및 Sheets 업데이트 중...")
    changed_users = detect_and_update(sheet, users, profiles, headers_map)

    print_results(changed_users)


if __name__ == "__main__":
    main()
