# UGC Monitor — 로컬 실행 가이드

## 한 줄 요약
```bash
./start.sh
```
브라우저가 자동으로 `http://localhost:8000` 열림.

---

## 필수 조건
1. **NAVER 사내망 VPN 연결** (NAMC API 호출에 필요)
2. **Python 3.9+ 설치**
3. `.env` 파일에 다음 키 채워져 있어야 함:
   - `NAVER_API_KEY` — NAMC Commercial Key
   - `NAVER_API_URL` — `https://namc-aigw.io.naver.com` (`/v1` 없이)
   - `APIFY_API_TOKEN`
   - `SPREADSHEET_ID`
   - `GEMINI_API_KEY` (옵션)
4. `config/google_credentials.json` — Google Sheets service account 키

---

## 사용 방식 2가지

### 방식 1: 대시보드 (브라우저 UI) — 권장
```bash
./start.sh
```
- 자동으로 의존성 설치 + 서버 시작 + 브라우저 오픈
- UI에서 게시물 URL, 댓글 파일, 레퍼런스 이미지, 프롬프트 텍스트 입력 → "수집 시작"

### 방식 2: CLI (스크립트 직접 실행) — 빠름
```bash
# Phase 3만 실행 (이미 phase3_candidates.json 있을 때)
python scripts/analyze_ugc.py \
  --reference "ref_0416_1.jpg,ref_0416_2.jpg,ref_0416_3.jpg" \
  --skip-stories

# 결과를 CSV + Google Sheets 새 탭으로 export
python scripts/export_phase3_matched.py

# 실패한 사용자 재시도
python scripts/retry_phase3_failures.py \
  --reference "ref_0416_1.jpg,ref_0416_2.jpg,ref_0416_3.jpg"

# 라벨링된 정답에 대해 정확도 측정 (개발/디버깅용)
python scripts/retest_phase3_labeled.py \
  --reference "ref_0416_1.jpg,ref_0416_2.jpg,ref_0416_3.jpg"
```

`reference_prompt.txt`가 있으면 자동으로 하이브리드 매칭 (텍스트 + 이미지) 적용됨.

---

## 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| `start.sh` 실행 시 NAMC 연결 실패 | NAVER VPN 미연결 → 연결 후 재실행 |
| `pip install` 에러 | `python -m venv venv && source venv/bin/activate` 후 재시도 |
| 401/403 응답 | `.env`의 `NAVER_API_KEY`가 Commercial Key인지 확인 |
| 400 에러 다수 | Instagram CDN URL 만료 (시간 갭 큼) → 새로 Phase 1+2 돌리고 즉시 Phase 3 |
| `phase3_candidates.json` 없음 | Phase 1/2 먼저 실행 필요 (대시보드는 통합 처리) |

---

## 파일 구조
```
ai prompt ugc monitoring/
├── start.sh                       # 로컬 실행 스크립트
├── index.html                     # 대시보드 UI (FastAPI가 서빙)
├── reference_prompt.txt           # 하이브리드 매칭용 프롬프트 텍스트
├── ref_0416_*.jpg                 # 레퍼런스 이미지 3장
├── .env                           # API keys (gitignore)
├── config/google_credentials.json # Google Sheets 인증 (gitignore)
├── scripts/
│   ├── analyze_ugc.py             # Phase 3 메인 스크립트
│   ├── retry_phase3_failures.py   # 실패 사용자 재시도
│   ├── retest_phase3_labeled.py   # 정확도 측정 (ground truth 필요)
│   ├── export_phase3_matched.py   # CSV + Google Sheets 탭 생성
│   └── build_phase3_pptx.py       # PPTX 리포트 (옵션)
└── ugc-monitor-api/
    └── main.py                    # FastAPI 백엔드
```
