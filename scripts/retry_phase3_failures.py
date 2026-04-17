"""
retry_phase3_failures.py
────────────────────────
phase3_failures.json에 기록된 사용자만 재시도합니다.
**핵심 차이**: 타겟 이미지를 우리가 먼저 다운로드 → base64로 변환 후 전송
→ Instagram CDN URL 만료/서명 실패로 인한 400 에러 거의 제거

성공한 유저는 phase3_results.json에 옮겨 추가하고 Google Sheets도 업데이트합니다.

실행:
  python scripts/retry_phase3_failures.py --reference "ref_0416_1.jpg,ref_0416_2.jpg,ref_0416_3.jpg"
"""

from __future__ import annotations
import os, sys, json, time, argparse, requests, base64, io
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock, Semaphore
from dotenv import load_dotenv
from PIL import Image
import gspread
from google.oauth2.service_account import Credentials

# analyze_ugc.py 재사용
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze_ugc import (
    PROMPT_FEED_TEMPLATE, PROMPT_PROFILE_TEMPLATE,
    file_to_data_uri, video_url_to_data_uri,
    get_sheet, update_sheet,
    NAVER_API_URL, NAVER_API_KEY, MODEL_NAME,
    SHEET_TAB_NAME, SPREADSHEET_ID,
)

CONCURRENT_USERS = 2
API_SEMAPHORE = Semaphore(6)
RESULTS_FILE  = "phase3_results.json"
FAILURES_FILE = "phase3_failures.json"

load_dotenv()


# ── 새 함수: URL → 다운로드 → 리사이즈 → data URI ──
def url_to_data_uri(url: str, max_side: int = 1024, timeout: int = 15) -> str | None:
    """이미지 URL을 직접 다운로드해서 data URI로 변환. 실패 시 None."""
    if not url:
        return None
    lower = url.lower().split("?")[0]
    if lower.endswith((".mp4", ".mov", ".webm")):
        return video_url_to_data_uri(url)
    try:
        r = requests.get(url, timeout=timeout,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            print(f"    ⚠️  이미지 다운로드 실패 ({r.status_code}): {url[:80]}")
            return None
        img = Image.open(io.BytesIO(r.content))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.thumbnail((max_side, max_side), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{b64}"
    except Exception as e:
        print(f"    ⚠️  이미지 처리 실패: {str(e)[:80]} | {url[:60]}")
        return None


# ── call_model: 타겟도 data URI ──
def call_model_b64(ref_uri: str, target_uri: str, img_type: str = "feed",
                   prompt_text: str = "", max_retries: int = 5) -> bool | None:
    template = PROMPT_PROFILE_TEMPLATE if img_type in ("profile", "story") else PROMPT_FEED_TEMPLATE
    prompt = template.format(prompt_text=prompt_text or "(프롬프트 텍스트 없음 — 이미지 비교만 수행)")
    payload = {
        "model": MODEL_NAME,
        "target_model_names": MODEL_NAME,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "text", "text": "[이미지 1] 레퍼런스:"},
                {"type": "image_url", "image_url": {"url": ref_uri}},
                {"type": "text", "text": "[이미지 2] 판별 대상:"},
                {"type": "image_url", "image_url": {"url": target_uri}},
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
            print(f"    ⚠️  호출 실패: {str(e)[:120]}")
            return None
    print(f"    ⚠️  재시도 초과")
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", required=True,
                        help="레퍼런스 이미지 (쉼표 구분)")
    parser.add_argument("--prompt-file", default="reference_prompt.txt",
                        help="원본 AI 프롬프트 텍스트 파일")
    parser.add_argument("--candidates", default="phase3_candidates.json")
    parser.add_argument("--failures",   default=FAILURES_FILE)
    parser.add_argument("--results",    default=RESULTS_FILE)
    args = parser.parse_args()

    prompt_text = ""
    if args.prompt_file and os.path.exists(args.prompt_file):
        with open(args.prompt_file, encoding="utf-8") as f:
            prompt_text = f.read().strip()
        print(f"📝 프롬프트 텍스트 로드: {len(prompt_text)} 글자")

    print("=" * 55)
    print(f"  Phase 3 재시도 — 실패한 유저만 (이미지 미리 다운로드)")
    print("=" * 55)

    # 레퍼런스 로드
    ref_paths = [p.strip() for p in args.reference.split(",") if p.strip()]
    ref_uris = []
    for rp in ref_paths:
        if not os.path.exists(rp):
            print(f"❌ 레퍼런스 없음: {rp}"); sys.exit(1)
        ref_uris.append(file_to_data_uri(rp))
        print(f"✅ 레퍼런스 로드: {rp}")
    print(f"   총 {len(ref_uris)}장")

    # 데이터 로드
    if not os.path.exists(args.failures):
        print(f"❌ 실패 파일 없음: {args.failures}"); sys.exit(1)
    with open(args.failures, encoding="utf-8") as f:
        failures = json.load(f)
    if not failures:
        print("✓ 재시도할 실패 유저가 없습니다."); return

    with open(args.candidates, encoding="utf-8") as f:
        cands = json.load(f)
    cand_map = {u["username"]: u for u in cands}

    with open(args.results, encoding="utf-8") as f:
        results_data = json.load(f)
    results_log    = results_data.get("all_results", [])
    confirmed_ugc  = results_data.get("confirmed_ugc", [])
    already_in_log = {r["username"] for r in results_log}

    print(f"\n재시도 대상: {len(failures)}명")
    print("─" * 55)

    sheet = get_sheet()
    sheet_lock = Lock()
    save_lock  = Lock()

    new_failures = []
    new_matches  = []

    def process(failed_entry):
        uname = failed_entry["username"]
        u = cand_map.get(uname)
        if not u:
            return {"username": uname, "status": "missing_in_candidates"}

        images = []
        if u.get("profile_url"):
            images.append(("profile", u["profile_url"], ""))
        story_urls = u.get("story_image_urls") or (
            [u["story_image_url"]] if u.get("story_image_url") else [])
        if u.get("has_story"):
            for s in story_urls:
                images.append(("story", s, ""))
        feed_items = u.get("latest_feed_items") or []
        feed_urls  = u.get("latest_feed_urls") or []
        if feed_items:
            for item in feed_items:
                if item.get("image_url"):
                    images.append(("feed", item["image_url"], item.get("post_url", "")))
        elif feed_urls:
            for img in feed_urls:
                images.append(("feed", img, ""))

        if not images:
            return {"username": uname, "status": "no_images"}

        is_ugc = False
        ugc_type = "none"
        matched_post = ""
        had_error = False

        for img_type, img_url, post_url in images:
            target_uri = url_to_data_uri(img_url)
            if not target_uri:
                had_error = True
                continue
            with ThreadPoolExecutor(max_workers=len(ref_uris)) as ex:
                futs = [ex.submit(call_model_b64, ru, target_uri, img_type, prompt_text)
                        for ru in ref_uris]
                results = [fut.result() for fut in as_completed(futs)]
            yes_count = sum(1 for r in results if r is True)
            if any(r is None for r in results):
                had_error = True
            if yes_count >= 2:
                is_ugc, ugc_type, matched_post = True, img_type, post_url
                break

        with sheet_lock:
            update_sheet(sheet, uname, ugc_type, is_ugc)

        return {"username": uname, "is_ugc": is_ugc, "ugc_type": ugc_type,
                "matched_post": matched_post, "had_error": had_error,
                "status": "ok"}

    progress = {"done": 0}
    total = len(failures)

    def save_state():
        with save_lock:
            with open(args.results, "w", encoding="utf-8") as f:
                json.dump({"confirmed_ugc": confirmed_ugc, "all_results": results_log},
                          f, ensure_ascii=False, indent=2)
            with open(args.failures, "w", encoding="utf-8") as f:
                json.dump(new_failures, f, ensure_ascii=False, indent=2)

    with ThreadPoolExecutor(max_workers=CONCURRENT_USERS) as ex:
        futs = {ex.submit(process, fe): fe for fe in failures}
        for fut in as_completed(futs):
            r = fut.result()
            progress["done"] += 1
            uname = r["username"]
            st    = r.get("status", "ok")

            if st == "missing_in_candidates":
                print(f"[{progress['done']}/{total}] @{uname} — candidates에 없음 (스킵)")
                new_failures.append({"username": uname, "reason": "missing in candidates"})
                continue
            if st == "no_images":
                print(f"[{progress['done']}/{total}] @{uname} → 검사할 이미지 없음")
                if uname not in already_in_log:
                    results_log.append({"username": uname, "is_ugc": False, "ugc_type": "none"})
                continue

            had_error = r.get("had_error", False)
            if r["is_ugc"]:
                status = f"✅ {r['ugc_type']} 일치"
                confirmed_ugc.append({
                    "username": uname,
                    "ugc_type": r["ugc_type"],
                    "feed_url": r["matched_post"] or "",
                })
                if uname not in already_in_log:
                    results_log.append({"username": uname, "is_ugc": True, "ugc_type": r["ugc_type"]})
                new_matches.append(uname)
            elif had_error:
                status = "⚠️  여전히 에러 (failures에 남김)"
                new_failures.append({"username": uname, "reason": "still failing after retry"})
            else:
                status = "❌ 해당 없음 (확인 완료)"
                if uname not in already_in_log:
                    results_log.append({"username": uname, "is_ugc": False, "ugc_type": "none"})

            print(f"[{progress['done']}/{total}] @{uname} {status}")
            save_state()

    save_state()

    print(f"\n{'='*55}")
    print(f"  재시도 완료")
    print(f"{'='*55}")
    print(f"  대상: {total}명")
    print(f"  ✅ 새 매치: {len(new_matches)}명  →  {', '.join(['@'+n for n in new_matches]) if new_matches else '(없음)'}")
    print(f"  ⚠️  여전히 실패: {len(new_failures)}명")
    print(f"\n  업데이트: {args.results}, {args.failures}")
    print(f"  Google Sheets도 새 매치 반영됨")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()
