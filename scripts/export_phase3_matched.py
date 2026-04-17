"""
export_phase3_matched.py
────────────────────────
phase3_results.json의 매치된 UGC 유저를:
  1. CSV 파일로 저장 (Excel/Sheets에서 바로 열기 가능)
  2. Google Sheets의 새 탭("phase3_matched")으로도 업로드

컬럼: 아이디 | 유형 | URL
  - 유형: 피드 / 스토리 / 프사
  - URL: 피드면 게시물 링크, 그 외엔 프로필 링크 (더블체크용)

실행:
  python scripts/export_phase3_matched.py
  python scripts/export_phase3_matched.py --no-sheets    # CSV만
"""

from __future__ import annotations
import os, csv, json, argparse
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials

load_dotenv()

SPREADSHEET_ID     = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS_PATH", "config/google_credentials.json")
NEW_TAB_NAME       = "phase3_matched"

TYPE_KO = {"feed": "피드", "story": "스토리", "profile": "프사"}
TYPE_ORDER = {"feed": 0, "story": 1, "profile": 2}


def build_rows(confirmed: list) -> list:
    rows = []
    for u in confirmed:
        uname    = u["username"]
        utype    = u["ugc_type"]
        post_url = u.get("feed_url") or ""
        if utype == "feed" and post_url:
            url = post_url
        else:
            url = f"https://www.instagram.com/{uname}/"
        rows.append([uname, TYPE_KO.get(utype, utype), url, utype])  # 마지막은 정렬용
    rows.sort(key=lambda r: (TYPE_ORDER.get(r[3], 99), r[0].lower()))
    return [r[:3] for r in rows]


def write_csv(rows: list, path: str):
    # utf-8-sig: Excel에서 한글 깨짐 방지
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["아이디", "유형", "URL"])
        w.writerows(rows)


def write_sheets(rows: list) -> str:
    creds = Credentials.from_service_account_file(
        GOOGLE_CREDENTIALS,
        scopes=["https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"],
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)

    # 기존 탭 있으면 삭제 후 재생성 (clean overwrite)
    try:
        old = sh.worksheet(NEW_TAB_NAME)
        sh.del_worksheet(old)
        print(f"  (기존 탭 '{NEW_TAB_NAME}' 삭제 후 재생성)")
    except gspread.WorksheetNotFound:
        pass

    ws = sh.add_worksheet(title=NEW_TAB_NAME, rows=max(100, len(rows)+10), cols=4)
    ws.update("A1", [["아이디", "유형", "URL"]] + rows, value_input_option="USER_ENTERED")
    # 헤더 굵게
    ws.format("A1:C1", {"textFormat": {"bold": True}, "backgroundColor":
                        {"red": 0.92, "green": 0.92, "blue": 0.92}})
    return f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit#gid={ws.id}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default="phase3_results.json")
    parser.add_argument("--csv",     default="phase3_matched.csv")
    parser.add_argument("--no-sheets", action="store_true",
                        help="Google Sheets 업로드 생략 (CSV만)")
    args = parser.parse_args()

    with open(args.results, encoding="utf-8") as f:
        data = json.load(f)
    confirmed = data.get("confirmed_ugc", [])

    if not confirmed:
        print("매치된 UGC가 없습니다."); return

    rows = build_rows(confirmed)
    type_counts = {}
    for r in rows:
        type_counts[r[1]] = type_counts.get(r[1], 0) + 1

    print(f"매치된 UGC: {len(rows)}명")
    for t, c in type_counts.items():
        print(f"  · {t}: {c}")

    write_csv(rows, args.csv)
    print(f"\n✅ CSV 저장: {args.csv}")

    if not args.no_sheets:
        if not SPREADSHEET_ID:
            print("⚠️  SPREADSHEET_ID 없음 → Sheets 업로드 생략")
        else:
            try:
                link = write_sheets(rows)
                print(f"✅ Google Sheets 새 탭 '{NEW_TAB_NAME}' 생성")
                print(f"   {link}")
            except Exception as e:
                print(f"⚠️  Sheets 업로드 실패: {e}")
                print(f"   → CSV 파일로 대체 사용 가능")


if __name__ == "__main__":
    main()
