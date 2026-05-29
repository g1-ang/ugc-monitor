"""Apify 댓글 actor 호출 → 댓글 CSV 파일 생성."""
from __future__ import annotations
import os, csv, io, requests
from typing import Optional


APIFY_TOKEN = os.getenv("APIFY_API_TOKEN")
ACTOR_ID = "apify~instagram-comment-scraper"


def fetch_comments(post_url: str, limit: int = 1000) -> list[dict]:
    """게시물 URL → 댓글 list. status 2xx 면 정상."""
    if not APIFY_TOKEN:
        raise RuntimeError("APIFY_API_TOKEN 미설정")
    r = requests.post(
        f"https://api.apify.com/v2/acts/{ACTOR_ID}/run-sync-get-dataset-items"
        f"?token={APIFY_TOKEN}&timeout=180",
        json={
            "directUrls": [post_url],
            "resultsLimit": limit,
            "includeNestedComments": False,
        },
        timeout=240,
    )
    if r.status_code >= 300:
        raise RuntimeError(f"Apify HTTP {r.status_code}: {r.text[:200]}")
    return r.json() or []


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


def fetch_comments_csv(post_url: str, limit: int = 1000) -> bytes:
    """게시물 URL → CSV bytes (한 번에)."""
    comments = fetch_comments(post_url, limit=limit)
    return comments_to_csv_bytes(comments)


if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.instagram.com/p/DY1yMdflP7M/"
    comments = fetch_comments(url, limit=20)
    print(f"댓글 {len(comments)}개")
    csv_bytes = comments_to_csv_bytes(comments)
    print(f"CSV 크기: {len(csv_bytes)} bytes")
    print(f"앞 200자:\n{csv_bytes[:200].decode('utf-8')}")
