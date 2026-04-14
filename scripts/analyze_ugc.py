"""
analyze_ugc.py  [Phase 3 — NAVER Open Models (Qwen2.5-VL) 판별]
────────────────────────────────────────────────────
phase3_candidates.json의 유저 이미지를 레퍼런스 이미지와 비교해
스타일 유사도를 Qwen2.5-VL-32B-Instruct로 판별하고
Google Sheets를 업데이트합니다.

실행 방법:
  python analyze_ugc.py --reference reference.jpg
  python analyze_ugc.py --reference ref.png --data candidates.json
"""

import os, sys, json, time, argparse, requests, base64, mimetypes, io
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from dotenv import load_dotenv
from PIL import Image
import gspread
from google.oauth2.service_account import Credentials

CONCURRENT_USERS = 5  # 동시에 처리할 유저 수

load_dotenv()

NAVER_API_URL      = os.getenv("NAVER_API_URL", "").rstrip("/")
NAVER_API_KEY      = os.getenv("NAVER_API_KEY")
SPREADSHEET_ID     = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS_PATH", "config/google_credentials.json")
SHEET_TAB_NAME     = "ugc_users"
MODEL_NAME         = "Qwen2.5-VL-32B-Instruct"

PROMPT_FEED = """[이미지 1]은 특정 AI 프롬프트로 만든 레퍼런스 결과물입니다.
[이미지 2]는 유저가 올린 판별 대상 피드 게시물입니다.

배경: 이 프롬프트는 여러 유저가 자기 얼굴로 동일하게 생성하는 구조입니다.
동일 프롬프트로 만든 이미지는 **얼굴만 다르고 장면·구도·무드가 거의 동일**합니다.

얼굴(인물 identity)은 무시하고, 아래 6가지 요소 중 [이미지 1]과 [이미지 2]에서 얼마나 유사한지 판단:
1. 배경·장소 (같은 씬/공간 유형)
2. 의상·소품 (같은 착장이나 핵심 소품)
3. 카메라 구도/앵글 (셀피·하이앵글·거울샷 등)
4. 조명·노출 (광원 방향, 밝기, 분위기)
5. 색감·톤 (팔레트, 화이트밸런스)
6. 전체적 무드/스타일

판별 규칙:
- 얼굴이 달라도 상관없음
- 위 6가지 중 **핵심 3가지 이상**이 명확히 유사하면 YES
- 단순히 "AI 이미지"거나 "여성 셀카"라는 이유만으로는 NO
- 배경과 구도 둘 다 완전히 다르면 NO

반드시 YES 또는 NO 한 단어만 답하세요."""

PROMPT_PROFILE = """[이미지 1]은 특정 AI 프롬프트로 만든 레퍼런스 결과물입니다.
[이미지 2]는 유저의 프로필 사진입니다 (보통 150×150 저해상도).

배경: 이 프롬프트는 여러 유저가 자기 얼굴로 동일하게 생성하는 구조입니다.
프로필 사진은 작고 크롭되어 있을 수 있지만, 핵심 구도/배경이 같으면 같은 프롬프트로 판단합니다.

얼굴은 완전히 무시하고, 아래 요소가 얼마나 비슷한지만 봅니다:
1. 배경·장소 (같은 씬/공간 유형)
2. 의상 또는 소품 (있다면)
3. 카메라 구도·앵글 (거울샷, 셀피, 하이앵글 등)
4. 전체 분위기·무드

판별 규칙:
- 저해상도·크롭이어도 핵심 구도·배경이 유사하면 YES
- 위 4가지 중 **2가지 이상**이 명확히 유사하면 YES
- "둘 다 AI 이미지" 또는 "둘 다 셀카"라는 표면적 공통점만으론 NO
- 배경과 구도 둘 다 완전히 다르면 NO

반드시 YES 또는 NO 한 단어만 답하세요."""

# 후방 호환 (import하는 곳이 있을 수 있음)
PROMPT = PROMPT_FEED


# ── 1. 이미지 로딩 ─────────────────────────────
def file_to_data_uri(path: str, max_side: int = 1024) -> str:
    """로컬 이미지 파일 → data URI (리사이즈 후 base64)"""
    with open(path, "rb") as f:
        raw = f.read()
    img = Image.open(io.BytesIO(raw))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


# ── 2. NAVER Open Models 호출 ─────────────────
def call_model(reference_data_uri: str, target_url: str, img_type: str = "feed", max_retries: int = 3) -> bool | None:
    """레퍼런스(data URI) vs 타겟(URL) 비교. img_type='profile'이면 저해상도 전용 프롬프트 사용"""
    prompt = PROMPT_PROFILE if img_type in ("profile", "story") else PROMPT_FEED
    payload = {
        "model": MODEL_NAME,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "text", "text": "[이미지 1] 레퍼런스:"},
                {"type": "image_url", "image_url": {"url": reference_data_uri}},
                {"type": "text", "text": "[이미지 2] 판별 대상:"},
                {"type": "image_url", "image_url": {"url": target_url}},
            ],
        }],
        "temperature": 0.1,
        "max_tokens": 10,
    }
    headers = {
        "Authorization": f"Bearer {NAVER_API_KEY}",
        "Content-Type": "application/json",
    }
    endpoint = f"{NAVER_API_URL}/chat/completions"

    for attempt in range(max_retries):
        try:
            resp = requests.post(endpoint, json=payload, headers=headers, timeout=90)
            if resp.status_code in (429, 503):
                wait = 2 ** (attempt + 2)
                print(f" [{resp.status_code} 재시도 {wait}s]", end="", flush=True)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            answer = resp.json()["choices"][0]["message"]["content"].strip().upper()
            return "YES" in answer
        except Exception as e:
            print(f"    ⚠️  호출 실패: {e}")
            return None
    print(f"    ⚠️  재시도 초과")
    return None


# ── 3. Google Sheets 업데이트 ─────────────────
def get_sheet():
    creds = Credentials.from_service_account_file(
        GOOGLE_CREDENTIALS,
        scopes=["https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"],
    )
    return gspread.authorize(creds).open_by_key(SPREADSHEET_ID).worksheet(SHEET_TAB_NAME)


def col_letter(headers: list, name: str) -> str | None:
    if name not in headers:
        return None
    idx = headers.index(name)
    result = ""
    n = idx
    while True:
        result = chr(65 + n % 26) + result
        n = n // 26 - 1
        if n < 0:
            break
    return result


def update_sheet(sheet, username: str, ugc_type: str, is_detected: bool):
    all_values = sheet.get_all_values()
    headers    = all_values[0]

    for i, row in enumerate(all_values[1:], start=2):
        if row and row[0].strip().lower() == username.lower():
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            updates = []
            cl_detected    = col_letter(headers, "is_ugc_detected")
            cl_type        = col_letter(headers, "ugc_type")
            cl_detected_at = col_letter(headers, "ugc_detected_at")

            if cl_detected:
                updates.append({"range": f"'{SHEET_TAB_NAME}'!{cl_detected}{i}", "values": [["TRUE" if is_detected else "FALSE"]]})
            if cl_type and is_detected:
                updates.append({"range": f"'{SHEET_TAB_NAME}'!{cl_type}{i}", "values": [[ugc_type]]})
            if cl_detected_at and is_detected:
                updates.append({"range": f"'{SHEET_TAB_NAME}'!{cl_detected_at}{i}", "values": [[now]]})

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
    parser.add_argument("--reference", type=str, required=True,
                        help="레퍼런스 이미지 파일 경로")
    args = parser.parse_args()

    print("=" * 55)
    print(f"  UGC Monitor — Phase 3: {MODEL_NAME} 판별")
    print("=" * 55)

    missing = [k for k, v in [
        ("NAVER_API_URL", NAVER_API_URL),
        ("NAVER_API_KEY", NAVER_API_KEY),
        ("SPREADSHEET_ID", SPREADSHEET_ID),
    ] if not v]
    if missing:
        print(f"❌ .env 파일에 없는 값: {', '.join(missing)}")
        sys.exit(1)

    if not os.path.exists(args.reference):
        print(f"❌ 레퍼런스 이미지 없음: {args.reference}")
        sys.exit(1)

    if not os.path.exists(args.data):
        print(f"❌ 후보 파일 없음: {args.data}")
        sys.exit(1)

    # 레퍼런스 로드 (data URI)
    ref_uri = file_to_data_uri(args.reference)
    print(f"✅ 레퍼런스 로드: {args.reference} ({len(ref_uri)//1024} KB base64)")

    with open(args.data, encoding="utf-8") as f:
        candidates = json.load(f)

    print(f"\n판별 대상: {len(candidates)}명")
    print("─" * 55)

    sheet = get_sheet()

    confirmed_ugc = []
    results_log   = []
    sheet_lock    = Lock()
    progress      = {"done": 0}

    def process_user(user):
        username = user.get("username", "")
        images_to_check = []
        if user.get("profile_url"):
            images_to_check.append(("profile", user["profile_url"], ""))
        # 스토리 이미지 배열 (최대 10장) 순차 판별
        story_urls = user.get("story_image_urls") or ([user["story_image_url"]] if user.get("story_image_url") else [])
        if user.get("has_story"):
            for s_url in story_urls:
                images_to_check.append(("story", s_url, ""))
        feed_items = user.get("latest_feed_items") or []
        feed_urls  = user.get("latest_feed_urls") or []
        if feed_items:
            for item in feed_items:
                img = item.get("image_url")
                if img:
                    images_to_check.append(("feed", img, item.get("post_url", "")))
        elif feed_urls:
            for img in feed_urls:
                images_to_check.append(("feed", img, ""))

        if not images_to_check:
            return {"username": username, "is_ugc": False, "ugc_type": "none", "matched_post": "", "skipped": True}

        is_ugc       = False
        ugc_type     = "none"
        matched_post = ""

        for img_type, img_url, post_url in images_to_check:
            result = call_model(ref_uri, img_url, img_type)
            if result is True:
                is_ugc, ugc_type, matched_post = True, img_type, post_url
                break

        with sheet_lock:
            update_sheet(sheet, username, ugc_type, is_ugc)

        return {"username": username, "is_ugc": is_ugc, "ugc_type": ugc_type,
                "matched_post": matched_post, "skipped": False}

    total = len(candidates)
    with ThreadPoolExecutor(max_workers=CONCURRENT_USERS) as ex:
        futures = {ex.submit(process_user, u): u for u in candidates}
        for fut in as_completed(futures):
            r = fut.result()
            progress["done"] += 1
            uname = r["username"]
            if r.get("skipped"):
                status = "→ 스킵"
            elif r["is_ugc"]:
                status = f"✅ {r['ugc_type']} 일치"
            else:
                status = "❌ 해당 없음"
            print(f"[{progress['done']}/{total}] @{uname} {status}")

            if r["is_ugc"]:
                confirmed_ugc.append({
                    "username": uname,
                    "ugc_type": r["ugc_type"],
                    "feed_url": r["matched_post"] or "",
                })
            results_log.append({"username": uname, "is_ugc": r["is_ugc"], "ugc_type": r["ugc_type"]})

    print(f"\n{'='*55}")
    print(f"  Phase 3 완료 — 판별 결과")
    print(f"{'='*55}")
    print(f"  판별 완료: {len(results_log)}명")
    print(f"  UGC 확인:  {len(confirmed_ugc)}명")
    print("─" * 55)

    if confirmed_ugc:
        print(f"\n  ✅ 확인된 UGC 유저:")
        for u in confirmed_ugc:
            type_label = {"feed": "피드", "story": "스토리", "profile": "프사변경"}.get(u["ugc_type"], u["ugc_type"])
            print(f"     · @{u['username']} → {type_label}")
            if u.get("feed_url"):
                print(f"       {u['feed_url'][:80]}...")
    else:
        print(f"\n  이번 스캔에서 UGC가 확인되지 않았습니다.")

    with open("phase3_results.json", "w", encoding="utf-8") as f:
        json.dump({"confirmed_ugc": confirmed_ugc, "all_results": results_log}, f,
                  ensure_ascii=False, indent=2)
    print(f"\n  결과 저장: phase3_results.json")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()
