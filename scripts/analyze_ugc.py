"""
analyze_ugc.py  [Phase 3 — Gemini Vision 판별]
────────────────────────────────────────────────────
phase3_candidates.json의 유저 이미지를 Gemini Vision으로 분석해
우리 AI 스타일인지 판별하고 Google Sheets를 업데이트합니다.

실행 방법:
  python analyze_ugc.py                        # phase3_candidates.json 사용
  python analyze_ugc.py --data candidates.json # 다른 파일 지정
"""

import os, sys, json, time, argparse, requests, base64
from datetime import datetime, timezone
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials

load_dotenv()

GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY")
SPREADSHEET_ID     = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS_PATH", "config/google_credentials.json")
SHEET_TAB_NAME     = "ugc_users"

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-1.5-flash:generateContent"
)

# ── pitapat_prompt 스타일 판별 프롬프트 ──────────
STYLE_PROMPT = """이 이미지를 분석해주세요.

판별 기준:
- AI로 생성된 인물 이미지인가? (실제 사진이 아닌 AI 생성 이미지)
- 동양인 여성 캐릭터 또는 인물이 주인공인가?
- 사실적이지만 약간 이상적으로 보정된 피부, 눈, 얼굴 비율인가?
- 셀카 스타일 또는 포트레이트 구도인가?
- 전반적으로 한국 뷰티/패션 인스타그램 감성인가?

위 기준에 해당하면 YES, 아니면 NO로만 답하세요.
반드시 YES 또는 NO 한 단어만 답하세요."""


# ── 1. 이미지 URL → base64 변환 ────────────────
def url_to_base64(image_url: str) -> tuple[str, str]:
    """이미지 URL을 base64로 변환. (data, mime_type) 반환"""
    try:
        resp = requests.get(image_url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0"
        })
        resp.raise_for_status()
        mime_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0]
        data = base64.b64encode(resp.content).decode("utf-8")
        return data, mime_type
    except Exception as e:
        print(f"    ⚠️  이미지 다운로드 실패: {e}")
        return None, None


# ── 2. Gemini Vision 호출 ──────────────────────
def analyze_image(image_url: str) -> bool | None:
    """이미지가 AI 스타일인지 판별. True/False/None(판별불가) 반환"""
    if not image_url:
        return None

    img_data, mime_type = url_to_base64(image_url)
    if not img_data:
        return None

    payload = {
        "contents": [{
            "parts": [
                {"text": STYLE_PROMPT},
                {"inline_data": {"mime_type": mime_type, "data": img_data}}
            ]
        }],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 10,
        }
    }

    try:
        resp = requests.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        answer = result["candidates"][0]["content"]["parts"][0]["text"].strip().upper()
        return "YES" in answer
    except Exception as e:
        print(f"    ⚠️  Gemini 호출 실패: {e}")
        return None


# ── 3. Google Sheets 업데이트 ──────────────────
def get_sheet():
    creds = Credentials.from_service_account_file(
        GOOGLE_CREDENTIALS,
        scopes=["https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"],
    )
    return gspread.authorize(creds).open_by_key(SPREADSHEET_ID).worksheet(SHEET_TAB_NAME)


def update_sheet(sheet, username: str, ugc_type: str, is_detected: bool):
    """해당 유저 행에 UGC 판별 결과 업데이트"""
    all_values = sheet.get_all_values()
    headers    = all_values[0]

    # 컬럼 인덱스 찾기
    def col_letter(name):
        if name not in headers: return None
        idx = headers.index(name)
        result = ""
        n = idx
        while True:
            result = chr(65 + n % 26) + result
            n = n // 26 - 1
            if n < 0: break
        return result

    # username 행 찾기
    for i, row in enumerate(all_values[1:], start=2):
        if row and row[0].strip().lower() == username.lower():
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            updates = []

            cl_detected  = col_letter("is_ugc_detected")
            cl_type      = col_letter("ugc_type")
            cl_detected_at = col_letter("ugc_detected_at")

            if cl_detected:
                updates.append({"range": f"{cl_detected}{i}",   "values": [["TRUE" if is_detected else "FALSE"]]})
            if cl_type and is_detected:
                updates.append({"range": f"{cl_type}{i}",       "values": [[ugc_type]]})
            if cl_detected_at and is_detected:
                updates.append({"range": f"{cl_detected_at}{i}", "values": [[now]]})

            if updates:
                sheet.spreadsheet.values_batch_update({
                    "valueInputOption": "RAW",
                    "data": updates,
                })
            return True
    return False


# ── 메인 ───────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=str, default="phase3_candidates.json")
    args = parser.parse_args()

    print("=" * 55)
    print("  UGC Monitor — Phase 3: Gemini Vision 판별")
    print("=" * 55)

    # 환경변수 체크
    missing = [k for k, v in [("GEMINI_API_KEY", GEMINI_API_KEY),
                                ("SPREADSHEET_ID", SPREADSHEET_ID)] if not v]
    if missing:
        print(f"❌ .env 파일에 없는 값: {', '.join(missing)}")
        sys.exit(1)

    if not os.path.exists(args.data):
        print(f"❌ 파일 없음: {args.data}")
        print("   Phase 2를 먼저 실행해주세요: python scripts/scan_profiles.py")
        sys.exit(1)

    with open(args.data, encoding="utf-8") as f:
        candidates = json.load(f)

    print(f"\n판별 대상: {len(candidates)}명")
    print(f"{'─'*55}")

    sheet = get_sheet()

    confirmed_ugc = []
    results_log   = []

    for i, user in enumerate(candidates, 1):
        username = user.get("username", "")
        print(f"\n[{i}/{len(candidates)}] @{username}")

        # 판별할 이미지 목록 (우선순위: 피드 > 프사 > 스토리)
        images_to_check = []
        if user.get("has_feed") and user.get("latest_feed_url"):
            images_to_check.append(("feed", user["latest_feed_url"]))
        if user.get("profile_changed") and user.get("profile_url"):
            images_to_check.append(("profile", user["profile_url"]))
        if user.get("has_story"):
            # 스토리는 URL이 없는 경우가 많아 프사로 대체 판별
            if not images_to_check and user.get("profile_url"):
                images_to_check.append(("story", user["profile_url"]))

        if not images_to_check:
            print(f"  → 판별할 이미지 없음, 스킵")
            continue

        is_ugc     = False
        ugc_type   = "none"

        for img_type, img_url in images_to_check:
            print(f"  → {img_type} 이미지 분석 중...", end="", flush=True)
            result = analyze_image(img_url)

            if result is True:
                print(f" ✅ AI 스타일 감지!")
                is_ugc   = True
                ugc_type = img_type
                break
            elif result is False:
                print(f" ❌ 해당 없음")
            else:
                print(f" ? 판별 불가")

            # API 호출 간격
            time.sleep(1)

        # Sheets 업데이트
        update_sheet(sheet, username, ugc_type, is_ugc)

        if is_ugc:
            confirmed_ugc.append({
                "username": username,
                "ugc_type": ugc_type,
                "feed_url": user.get("latest_feed_url", ""),
            })

        # 결과 로그
        results_log.append({
            "username": username,
            "is_ugc":   is_ugc,
            "ugc_type": ugc_type,
        })

        time.sleep(0.5)

    # ── 최종 결과 ───────────────────────────────
    print(f"\n{'='*55}")
    print(f"  Phase 3 완료 — Gemini Vision 판별 결과")
    print(f"{'='*55}")
    print(f"  판별 완료: {len(results_log)}명")
    print(f"  UGC 확인:  {len(confirmed_ugc)}명")
    print(f"{'─'*55}")

    if confirmed_ugc:
        print(f"\n  ✅ 확인된 UGC 유저:")
        for u in confirmed_ugc:
            type_label = {"feed": "피드", "story": "스토리", "profile": "프사변경"}.get(u["ugc_type"], u["ugc_type"])
            print(f"     · @{u['username']} → {type_label}")
            if u.get("feed_url"):
                print(f"       {u['feed_url']}")
    else:
        print(f"\n  이번 스캔에서 UGC가 확인되지 않았습니다.")

    # 결과 저장
    with open("phase3_results.json", "w", encoding="utf-8") as f:
        json.dump({"confirmed_ugc": confirmed_ugc, "all_results": results_log}, f,
                  ensure_ascii=False, indent=2)
    print(f"\n  결과 저장: phase3_results.json")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()
