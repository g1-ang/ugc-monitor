"""댓글 CSV 수집 — Chrome 확장 다운로드 우선, 없으면 Apify 폴백."""
from __future__ import annotations
import os, re, csv, io, time, glob, requests
from pathlib import Path
from typing import Optional


APIFY_TOKEN = os.getenv("APIFY_API_TOKEN")
ACTOR_ID = "apify~instagram-comment-scraper"
APIFY_BASE = "https://api.apify.com/v2"
DOWNLOADS_DIR = Path.home() / "Downloads"


def _extract_shortcode(post_url: str) -> Optional[str]:
    """게시물 URL → shortcode (예: DY6qpO6TQDj)."""
    m = re.search(r"/(?:p|reel)/([A-Za-z0-9_-]+)", post_url)
    return m.group(1) if m else None


def find_local_comments_csv(post_url: str) -> Optional[Path]:
    """Chrome 확장이 ~/Downloads/@{user}/comments/ 에 저장한 CSV 중
    같은 shortcode 가진 가장 최근 파일 반환. 없으면 None.

    파일명 규칙: {YYYYMMDD_HHMMSS}_{shortcode}.csv"""
    sc = _extract_shortcode(post_url)
    if not sc:
        return None
    # ~/Downloads/@*/comments/*{shortcode}*.csv  glob
    pattern = str(DOWNLOADS_DIR / "@*" / "comments" / f"*_{sc}.csv")
    matches = glob.glob(pattern)
    if not matches:
        return None
    # 가장 최근 mtime
    matches.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return Path(matches[0])


def fetch_comments(post_url: str, limit: int = 1500, timeout_sec: int = 900) -> list[dict]:
    """게시물 URL → 댓글 list. 비동기 시작 + 폴링 (최대 timeout_sec 초).
    run-sync 는 180초로 고정이라 댓글 많은 게시물엔 부족 → 직접 폴링."""
    if not APIFY_TOKEN:
        raise RuntimeError("APIFY_API_TOKEN 미설정")
    # 1. run 시작
    r = requests.post(
        f"{APIFY_BASE}/acts/{ACTOR_ID}/runs?token={APIFY_TOKEN}",
        json={
            "directUrls": [post_url],
            "resultsLimit": limit,
            "includeNestedComments": False,
        },
        timeout=30,
    )
    if r.status_code >= 300:
        raise RuntimeError(f"Apify run start HTTP {r.status_code}: {r.text[:200]}")
    run_id = r.json()["data"]["id"]

    # 2. 폴링 (6초 간격)
    deadline = time.time() + timeout_sec
    status_data = {}
    while time.time() < deadline:
        time.sleep(6)
        s = requests.get(f"{APIFY_BASE}/actor-runs/{run_id}?token={APIFY_TOKEN}", timeout=15)
        status_data = s.json().get("data", {})
        st = status_data.get("status", "")
        if st == "SUCCEEDED":
            break
        if st in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify run {st} (run_id={run_id})")
    else:
        raise RuntimeError(f"Apify 폴링 timeout {timeout_sec}s (run_id={run_id})")

    # 3. 데이터셋 조회
    dataset_id = status_data.get("defaultDatasetId")
    if not dataset_id:
        return []
    items_r = requests.get(
        f"{APIFY_BASE}/datasets/{dataset_id}/items?token={APIFY_TOKEN}",
        timeout=60,
    )
    return items_r.json() if items_r.status_code < 300 else []


def comments_to_csv_bytes(comments: list[dict]) -> bytes:
    """댓글 list → UGC monitor가 읽을 수 있는 CSV (UTF-8 BOM 포함).
    헤더 = "작성자" → main.py 의 USERNAME_HEADERS 매칭."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["번호", "작성 시각", "작성자", "이름", "댓글 내용", "좋아요 수", "답글 수"])
    for i, c in enumerate(comments, 1):
        w.writerow([
            i,
            c.get("timestamp", ""),
            c.get("ownerUsername", ""),
            (c.get("owner") or {}).get("full_name", ""),
            c.get("text", ""),
            c.get("likesCount", 0),
            c.get("repliesCount", 0),
        ])
    # UTF-8 with BOM (Excel/openpyxl 호환)
    return ("﻿" + buf.getvalue()).encode("utf-8")


def fetch_comments_csv(post_url: str, limit: int = 1000) -> tuple[bytes, str]:
    """게시물 URL → (CSV bytes, source).
    source: "chrome_extension" 또는 "apify"

    1) ~/Downloads/@*/comments/ 에서 shortcode 매칭 CSV 찾으면 그거 사용 (무료, 정확)
    2) 못 찾으면 Apify 호출 (비용 ~$0.75, 보장)"""
    local = find_local_comments_csv(post_url)
    if local:
        with open(local, "rb") as f:
            return f.read(), f"chrome_extension ({local.name})"
    # 폴백: Apify
    comments = fetch_comments(post_url, limit=limit)
    return comments_to_csv_bytes(comments), "apify"


if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.instagram.com/p/DY1yMdflP7M/"
    comments = fetch_comments(url, limit=20)
    print(f"댓글 {len(comments)}개")
    csv_bytes = comments_to_csv_bytes(comments)
    print(f"CSV 크기: {len(csv_bytes)} bytes")
    print(f"앞 200자:\n{csv_bytes[:200].decode('utf-8')}")
