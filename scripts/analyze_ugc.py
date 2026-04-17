from __future__ import annotations
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
from threading import Lock, Semaphore
from dotenv import load_dotenv
from PIL import Image
import gspread
from google.oauth2.service_account import Credentials

CONCURRENT_USERS = 2  # 동시에 처리할 유저 수 (rate limit 회피용으로 낮춤)
MAX_CONCURRENT_API_CALLS = 6  # 전체 API 호출 동시 한도 (글로벌 세마포어)
API_SEMAPHORE = Semaphore(MAX_CONCURRENT_API_CALLS)
RESULTS_FILE = "phase3_results.json"
FAILURES_FILE = "phase3_failures.json"

load_dotenv()

NAVER_API_URL      = os.getenv("NAVER_API_URL", "").rstrip("/")
NAVER_API_KEY      = os.getenv("NAVER_API_KEY")
SPREADSHEET_ID     = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS_PATH", "config/google_credentials.json")
SHEET_TAB_NAME     = "ugc_users"
MODEL_NAME         = "gemini-2.0-flash"

PROMPT_FEED_TEMPLATE = """원본 AI 프롬프트 (이 프롬프트로 [이미지 1] 레퍼런스가 만들어짐):
═══════════════════════════════════════════════════════
{prompt_text}
═══════════════════════════════════════════════════════

[이미지 1]: 위 프롬프트로 생성된 레퍼런스 결과물
[이미지 2]: 유저가 올린 판별 대상 피드 게시물

**핵심 질문**: [이미지 2]가 위 AI 프롬프트로 다른 사람의 얼굴로 생성된 것처럼 보이나요?

판별 방법 — 위 프롬프트의 핵심 요소를 [이미지 2]에서 얼마나 만족하는지 봅니다:
1. **장면/배경**: 프롬프트가 명시한 환경과 일치하는가? (예: "차 안 뒷좌석"이면 [이미지 2]도 차 안 뒷좌석이어야 함)
2. **의상**: 프롬프트에 적힌 의상이 보이는가? (구체적인 옷 종류·색·실루엣)
3. **자세/구도**: 프롬프트가 명시한 포즈·앵글·프레이밍이 일치?
4. **색감/톤**: 프롬프트의 색감 가이드 (차가운 톤, 저채도 등) 일치?
5. **전체 인상/질감**: 프롬프트가 의도한 느낌 (예: "구형 폰카 저화질")?

**자주 오판하는 케이스 (모두 NO)**:
- 둘 다 AI풍 셀카지만 위 프롬프트의 핵심 장면이 아님 → NO
- 둘 다 자연광 인물 사진이지만 의상·구도·장소가 다름 → NO
- 위 프롬프트의 요소 중 한두 개만 부분적으로 맞음 → NO
- 단순히 "AI 셀카", "여성 인물" 같은 표면적 공통점만 겹침 → NO
- 위 프롬프트의 명시된 "절대 금지" 항목이 [이미지 2]에 보임 → NO
- 비슷해 보이지만 정말 이 프롬프트로 만든 거라 확신 안 섬 → NO

**YES 조건**: 위 프롬프트의 주요 요소들(장면, 의상, 자세, 색감, 질감) 대부분이 명확히 보이고, "이 프롬프트로 다른 사람으로 다시 생성한 결과"라고 강하게 확신될 때만.

확신 안 서면 NO. 반드시 YES 또는 NO 한 단어만 답하세요."""

PROMPT_PROFILE_TEMPLATE = """원본 AI 프롬프트 (이 프롬프트로 [이미지 1] 레퍼런스가 만들어짐):
═══════════════════════════════════════════════════════
{prompt_text}
═══════════════════════════════════════════════════════

[이미지 1]: 위 프롬프트로 생성된 고화질 레퍼런스
[이미지 2]: 유저의 프로필 사진 (저해상도 150×150, 크롭 가능)

**핵심 질문**: 이 작은 프로필 사진이 위 AI 프롬프트로 다른 사람의 얼굴로 생성된 결과물의 일부로 보이나요?

판별 방법 — 저해상도지만 다음을 확인:
1. **장면/배경의 종류**: 프롬프트가 명시한 환경(예: "차 안 뒷좌석")이 작게라도 인식되는가?
2. **의상**: 프롬프트의 의상(예: "흰 끈나시 + 회색 가디건")이 작게라도 인식되는가?
3. **포즈/앵글**: 프롬프트가 지정한 자세나 손 위치(예: "주먹으로 코+입 가림")가 보이는가?
4. **색감**: 프롬프트의 톤(예: "차가운 톤, 노란기 금지") 일치?

**저해상도이므로 매우 엄격하게**:
- 디테일이 안 보여서 확신 못 하면 무조건 NO
- 둘 다 AI풍이라는 공통점만으로는 NO
- 둘 다 여성 셀카·자연광·클로즈업 같은 표면적 공통점만으론 NO
- 배경 종류가 프롬프트와 다르면 (예: 차 안이 아니라 카페/방/거울 앞) 무조건 NO
- 위 프롬프트의 "절대 금지" 항목이 보이면 NO
- 확신이 80% 미만이면 NO

**YES 조건**: 작은 썸네일이지만 위 프롬프트의 주요 요소들이 명확히 인식되고, "이 프롬프트로 다른 사람으로 만든 결과를 작게 크롭한 것"처럼 보일 때만.

확신 안 서면 NO. 반드시 YES 또는 NO 한 단어만 답하세요."""

# 후방 호환 변수 (프롬프트 텍스트 없을 때 기본 prompt — 거의 안 쓰지만 import 대비)
PROMPT_FEED    = PROMPT_FEED_TEMPLATE.format(prompt_text="(프롬프트 텍스트 없음 — 이미지 비교만 수행)")
PROMPT_PROFILE = PROMPT_PROFILE_TEMPLATE.format(prompt_text="(프롬프트 텍스트 없음 — 이미지 비교만 수행)")

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


def video_url_to_data_uri(video_url: str) -> str | None:
    """비디오 URL → 첫 프레임 → data URI. 음원 추가로 mp4로 저장된 스토리 처리용"""
    try:
        import imageio.v3 as iio
        frame = iio.imread(video_url, index=0, plugin="pyav")
        img = Image.fromarray(frame)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.thumbnail((1024, 1024), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception as e:
        print(f"    ⚠️  비디오 프레임 추출 실패: {str(e)[:80]}")
        return None


def target_to_qwen_url(url: str) -> str | None:
    """타겟 URL이 이미지면 그대로, 비디오(.mp4)면 첫 프레임 data URI"""
    if not url:
        return None
    lower = url.lower().split("?")[0]
    if lower.endswith((".mp4", ".mov", ".webm")):
        data_uri = video_url_to_data_uri(url)
        return data_uri  # None이면 skip
    return url


# ── 2. NAVER Open Models 호출 ─────────────────
def call_model(reference_data_uri: str, target_url: str, img_type: str = "feed",
               prompt_text: str = "", max_retries: int = 5) -> bool | None:
    """레퍼런스(data URI) vs 타겟(URL) 비교. img_type='profile'/'story'면 저해상도 전용 템플릿 사용.
    prompt_text가 있으면 원본 AI 프롬프트 텍스트를 함께 전달 (하이브리드 방식)."""
    template = PROMPT_PROFILE_TEMPLATE if img_type in ("profile", "story") else PROMPT_FEED_TEMPLATE
    prompt = template.format(prompt_text=prompt_text or "(프롬프트 텍스트 없음 — 이미지 비교만 수행)")
    # 비디오면 첫 프레임 추출
    target = target_to_qwen_url(target_url)
    if not target:
        return None
    payload = {
        "model": MODEL_NAME,
        "target_model_names": MODEL_NAME,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "text", "text": "[이미지 1] 레퍼런스:"},
                {"type": "image_url", "image_url": {"url": reference_data_uri}},
                {"type": "text", "text": "[이미지 2] 판별 대상:"},
                {"type": "image_url", "image_url": {"url": target}},
            ],
        }],
        "temperature": 0.1,
        "max_tokens": 10,
    }
    headers = {
        "Authorization": f"Bearer {NAVER_API_KEY}",
        "custom-llm-provider": "vertex_ai",
        "Content-Type": "application/json",
    }
    endpoint = f"{NAVER_API_URL}/chat/completions"

    for attempt in range(max_retries):
        try:
            with API_SEMAPHORE:
                resp = requests.post(endpoint, json=payload, headers=headers, timeout=90)
            if resp.status_code in (429, 503):
                wait = min(60, 2 ** (attempt + 2))
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
                        help="레퍼런스 이미지 파일 경로 (쉼표로 여러 장 지정 가능)")
    parser.add_argument("--prompt-file", type=str, default="reference_prompt.txt",
                        help="원본 AI 프롬프트 텍스트 파일 (하이브리드 판별용)")
    parser.add_argument("--skip-stories", action="store_true",
                        help="스토리 이미지 검사 생략 (피드+프사만)")
    args = parser.parse_args()

    # 프롬프트 텍스트 로드 (없으면 빈 문자열 → 이미지만으로 비교)
    prompt_text = ""
    if args.prompt_file and os.path.exists(args.prompt_file):
        with open(args.prompt_file, encoding="utf-8") as f:
            prompt_text = f.read().strip()
        print(f"📝 프롬프트 텍스트 로드: {args.prompt_file} ({len(prompt_text)} 글자)")
    else:
        print(f"⚠️  프롬프트 파일 없음 ({args.prompt_file}) — 이미지만으로 비교 (정확도 ↓)")

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

    ref_paths = [p.strip() for p in args.reference.split(",") if p.strip()]
    for rp in ref_paths:
        if not os.path.exists(rp):
            print(f"❌ 레퍼런스 이미지 없음: {rp}")
            sys.exit(1)

    if not os.path.exists(args.data):
        print(f"❌ 후보 파일 없음: {args.data}")
        sys.exit(1)

    # 레퍼런스 로드 (data URI 리스트)
    ref_uris = []
    for rp in ref_paths:
        uri = file_to_data_uri(rp)
        ref_uris.append(uri)
        print(f"✅ 레퍼런스 로드: {rp} ({len(uri)//1024} KB base64)")
    print(f"   총 {len(ref_uris)}장의 레퍼런스 사용 (하나라도 YES → UGC)")

    with open(args.data, encoding="utf-8") as f:
        candidates = json.load(f)

    print(f"\n판별 대상: {len(candidates)}명")
    print("─" * 55)

    # ── Resume: 기존 결과/실패 기록 로드 → 처리한 사용자는 스킵
    confirmed_ugc = []
    results_log   = []
    failures      = []
    processed_usernames: set[str] = set()

    if os.path.exists(RESULTS_FILE):
        try:
            with open(RESULTS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            results_log    = data.get("all_results", [])
            confirmed_ugc  = data.get("confirmed_ugc", [])
            processed_usernames |= {r["username"] for r in results_log}
            print(f"  ↩ Resume: {len(results_log)}명 이전 결과 로드 (그 중 매치 {len(confirmed_ugc)}명)")
        except Exception as e:
            print(f"  ⚠️  {RESULTS_FILE} 로드 실패, 새로 시작: {e}")

    if os.path.exists(FAILURES_FILE):
        try:
            with open(FAILURES_FILE, encoding="utf-8") as f:
                failures = json.load(f)
            failed_usernames = {x["username"] for x in failures}
            processed_usernames |= failed_usernames
            print(f"  ⚠ 실패 기록: {len(failures)}명 (이번엔 스킵 — 별도 재시도 가능)")
        except Exception as e:
            print(f"  ⚠️  {FAILURES_FILE} 로드 실패: {e}")

    remaining = [u for u in candidates if u.get("username") not in processed_usernames]
    print(f"  처리 대상: {len(remaining)}명 (전체 {len(candidates)}명 중)")

    if not remaining:
        print("  ✓ 모두 처리됨, 종료.")
        return

    sheet = get_sheet()
    sheet_lock = Lock()
    save_lock  = Lock()

    def save_state():
        with save_lock:
            with open(RESULTS_FILE, "w", encoding="utf-8") as f:
                json.dump({"confirmed_ugc": confirmed_ugc, "all_results": results_log},
                          f, ensure_ascii=False, indent=2)
            with open(FAILURES_FILE, "w", encoding="utf-8") as f:
                json.dump(failures, f, ensure_ascii=False, indent=2)

    def process_user(user):
        username = user.get("username", "")
        images_to_check = []
        if user.get("profile_url"):
            images_to_check.append(("profile", user["profile_url"], ""))
        if not args.skip_stories:
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
            return {"username": username, "is_ugc": False, "ugc_type": "none",
                    "matched_post": "", "skipped": True, "had_error": False}

        is_ugc       = False
        ugc_type     = "none"
        matched_post = ""
        had_error    = False

        for img_type, img_url, post_url in images_to_check:
            with ThreadPoolExecutor(max_workers=len(ref_uris)) as ref_ex:
                futures = [ref_ex.submit(call_model, ru, img_url, img_type, prompt_text)
                           for ru in ref_uris]
                results = [fut.result() for fut in as_completed(futures)]
            yes_count = sum(1 for r in results if r is True)
            if any(r is None for r in results):
                had_error = True
            # 2/3 다수결: 3장 레퍼런스 중 2장 이상 YES → 매치
            if yes_count >= 2:
                is_ugc, ugc_type, matched_post = True, img_type, post_url
                break

        with sheet_lock:
            update_sheet(sheet, username, ugc_type, is_ugc)

        return {"username": username, "is_ugc": is_ugc, "ugc_type": ugc_type,
                "matched_post": matched_post, "skipped": False, "had_error": had_error}

    total      = len(remaining)
    progress   = {"done": 0}
    save_every = 10

    with ThreadPoolExecutor(max_workers=CONCURRENT_USERS) as ex:
        futures = {ex.submit(process_user, u): u for u in remaining}
        for fut in as_completed(futures):
            r = fut.result()
            progress["done"] += 1
            uname     = r["username"]
            had_error = r.get("had_error", False)

            if r.get("skipped"):
                status = "→ 스킵"
            elif r["is_ugc"]:
                status = f"✅ {r['ugc_type']} 일치"
            elif had_error:
                status = "⚠️  에러로 실패 (failures에 기록)"
            else:
                status = "❌ 해당 없음"
            print(f"[{progress['done']}/{total}] @{uname} {status}")

            if r["is_ugc"]:
                confirmed_ugc.append({
                    "username": uname,
                    "ugc_type": r["ugc_type"],
                    "feed_url": r["matched_post"] or "",
                })
                results_log.append({"username": uname, "is_ugc": True, "ugc_type": r["ugc_type"]})
            elif had_error and not r.get("skipped"):
                # 에러로 인한 결과 — 진짜 매치를 놓쳤을 가능성. 재시도용으로 분리.
                failures.append({"username": uname, "reason": "API errors during eval"})
            else:
                results_log.append({"username": uname, "is_ugc": False, "ugc_type": r["ugc_type"]})

            if progress["done"] % save_every == 0:
                save_state()

    save_state()  # final flush

    print(f"\n{'='*55}")
    print(f"  Phase 3 완료 — 판별 결과")
    print(f"{'='*55}")
    print(f"  판별 완료: {len(results_log)}명")
    print(f"  UGC 확인:  {len(confirmed_ugc)}명")
    print(f"  실패 (에러): {len(failures)}명")
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

    print(f"\n  결과 저장: {RESULTS_FILE}")
    if failures:
        print(f"  실패 저장: {FAILURES_FILE} (재시도 시 별도 처리 필요)")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()
