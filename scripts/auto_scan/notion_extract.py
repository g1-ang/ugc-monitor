"""노션 페이지에서 프롬프트(code 블록) + 첨부 이미지 자동 추출.

사용:
    from notion_extract import fetch_campaign
    data = fetch_campaign("https://www.notion.so/...")
    # → {"prompt": "...", "images": [<bytes>, <bytes>, ...], "title": "..."}
"""
from __future__ import annotations
import os, re, requests
from typing import Optional


NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_API   = "https://api.notion.com/v1"
HEADERS = lambda: {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


def _extract_page_id(url_or_id: str) -> str:
    """노션 URL 또는 ID에서 32자 hex ID 추출 → UUID 형식."""
    s = (url_or_id or "").strip()
    # URL → 마지막 슬래시 뒤
    if "notion." in s or "/" in s:
        s = s.rstrip("/").split("/")[-1]
        # 쿼리 / fragment 제거
        s = s.split("?")[0].split("#")[0]
        # ?p=xxxx 형태
        if "p=" in url_or_id:
            s = re.search(r"p=([0-9a-f]+)", url_or_id, re.I).group(1)
    # title-{id} 형태면 마지막 32자 hex
    m = re.search(r"([0-9a-f]{32})$", s, re.I)
    if m:
        s = m.group(1)
    # UUID 포맷
    if len(s) == 32:
        return f"{s[0:8]}-{s[8:12]}-{s[12:16]}-{s[16:20]}-{s[20:32]}"
    return s  # 이미 UUID 형식이거나 다른 경우


def _get_children(block_id: str) -> list:
    """페이지/블록의 자식 블록 전체 가져오기 (페이지네이션 자동)."""
    out = []
    cursor = None
    while True:
        url = f"{NOTION_API}/blocks/{block_id}/children?page_size=100"
        if cursor:
            url += f"&start_cursor={cursor}"
        r = requests.get(url, headers=HEADERS(), timeout=30)
        r.raise_for_status()
        d = r.json()
        out.extend(d.get("results", []))
        if not d.get("has_more"):
            break
        cursor = d.get("next_cursor")
    return out


def _block_text(rich_text: list) -> str:
    """rich_text 배열 → plain text 합치기."""
    return "".join(t.get("plain_text", "") for t in (rich_text or []))


def _image_url(block: dict) -> Optional[str]:
    """image 블록 → 다운로드 가능한 URL.
    Notion file URL 은 signed URL (1h expiry). 외부 URL 은 그대로.
    """
    img = block.get("image", {})
    if img.get("type") == "external":
        return img.get("external", {}).get("url")
    elif img.get("type") == "file":
        return img.get("file", {}).get("url")
    return None


def _collect_column_list_images(block_id: str) -> list[str]:
    """column_list 블록의 모든 자식 column → 그 안의 image 블록 URL 수집."""
    urls = []
    columns = _get_children(block_id)
    for col in columns:
        if col.get("type") != "column":
            continue
        col_children = _get_children(col["id"])
        for c in col_children:
            if c.get("type") == "image":
                u = _image_url(c)
                if u:
                    urls.append(u)
    return urls


def fetch_campaign(page_url_or_id: str, download_images: bool = True) -> dict:
    """캠페인 노션 페이지 → 프롬프트 + 레퍼런스 이미지 추출.

    규칙:
    - 프롬프트: 가장 긴 code 블록의 내용
    - 레퍼런스 이미지: **column_list 안의 image 블록만** (3컬럼 그리드 규약)
      → callout / toggle 안의 이미지 (가이드, 채팅 스샷 등)는 자동 제외

    반환: {
      "page_id":  "...",
      "title":    "바다 인생샷 프롬프트 🌊",
      "prompt":   "[Subject Reference] ...",
      "images":   [<bytes>, <bytes>, ...]   # download_images=True 일 때
      "image_urls": [...]                   # download_images=False 일 때 (1h 만료)
    }
    """
    if not NOTION_TOKEN:
        raise RuntimeError("NOTION_TOKEN 환경 변수 미설정")
    page_id = _extract_page_id(page_url_or_id)

    # 1. 페이지 메타 (title)
    pr = requests.get(f"{NOTION_API}/pages/{page_id}", headers=HEADERS(), timeout=30)
    pr.raise_for_status()
    title = ""
    props = pr.json().get("properties", {}).get("title", {})
    if props.get("type") == "title":
        title = _block_text(props.get("title", []))

    # 2. 자식 블록들 순회
    blocks = _get_children(page_id)
    prompt_text = ""
    image_urls = []
    for b in blocks:
        t = b.get("type")
        if t == "code":
            txt = _block_text(b.get("code", {}).get("rich_text", []))
            if len(txt) > len(prompt_text):  # 가장 긴 code 블록 채택
                prompt_text = txt
        elif t == "column_list":
            # column_list 안의 image만 레퍼런스로 인정
            image_urls.extend(_collect_column_list_images(b["id"]))

    result = {
        "page_id": page_id,
        "title":   title,
        "prompt":  prompt_text.strip(),
    }
    if download_images:
        images = []
        for u in image_urls:
            try:
                r = requests.get(u, timeout=30)
                r.raise_for_status()
                images.append(r.content)
            except Exception as e:
                print(f"  ⚠️ 이미지 다운로드 실패: {e}")
        result["images"] = images
    else:
        result["image_urls"] = image_urls
    return result


if __name__ == "__main__":
    # 테스트: 바다 인생샷
    import sys, json
    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.notion.so/36005501-0211-8005-b28d-eec590df8bc5"
    d = fetch_campaign(url, download_images=False)
    print(f"title:  {d['title']}")
    print(f"prompt: {len(d['prompt'])} chars")
    print(f"        앞 200자: {d['prompt'][:200]}")
    print(f"images: {len(d['image_urls'])} 개")
    for u in d['image_urls']:
        print(f"  - {u[:120]}")
