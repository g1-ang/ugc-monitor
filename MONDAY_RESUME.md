# 월요일 작업 재개 — Claude용 시작 가이드

> 사용자: 월요일 아침 Claude에게 이 파일 보여주거나 "MONDAY_RESUME.md 따라 진행해줘" 한 줄만.

---

## 컨텍스트 (Apr 17~18 작업 요약)

**현황**:
- UGC Monitor — Instagram 댓글러 중 동일 AI 프롬프트로 콘텐츠 만든 사람 자동 검출
- Phase 1 (댓글 수집) → Phase 2 (Apify 프로필/스토리/피드 스캔) → Phase 3 (Gemini 이미지 판별) 파이프라인
- 4/17 작업으로 정확도 11% → **87.5%** 달성 (하이브리드: 프롬프트 텍스트 + 레퍼런스 이미지 3장 + 2/3 다수결)

**미해결 이슈 (월요일 결정 필요)**:
1. **Apify 결제 끊겨 있음** — Phase 1, 2 못 돎. 재결제 vs 우회 결정
2. **NAMC가 NAVER 사내망 전용** — Render(외부 클라우드) 배포 불가. 로컬 모드로 전환됨
3. **회사 환경에서 Render가 NAMC 접근 가능한지 확인** — 가능하면 Render 유지, 불가능하면 로컬 전용 확정

**4/17 마지막 Phase 3 run 미완료**: 605/913에서 NAMC 다운으로 멈춤. Resume 시 313명만 처리.

---

## 시작 시 Claude가 해야 할 순서

### Step 1: 환경 검증 (병렬 실행 가능)
```bash
# 1-a. git 최신인지 확인 (양쪽 repo)
cd "/path/to/ai prompt ugc monitoring" && git log --oneline -3
cd ugc-monitor-api && git log --oneline -3

# 1-b. NAMC 연결 (회사 환경에서는 VPN 없이도 가능할 수도?)
curl -s -o /dev/null -w "NAMC: %{http_code} (%{time_total}s)\n" --max-time 5 https://namc-aigw.io.naver.com/

# 1-c. .env 존재 + 키 채워졌나
grep -E "^NAVER_API_KEY=|^NAVER_API_URL=|^APIFY_API_TOKEN=|^SPREADSHEET_ID=" .env | sed 's|=.*|=<set>|'

# 1-d. Google credentials
ls -la config/google_credentials.json
```

### Step 2: 의존성 설치 (필요시)
```bash
# venv가 있으면 활성화, 없으면 만들기
python -m venv venv && source venv/bin/activate
pip install -r ugc-monitor-api/requirements.txt
```

### Step 3: NAMC + Gemini 빠른 sanity test
```bash
python -c "
import os, requests
from dotenv import load_dotenv
load_dotenv()
r = requests.post(os.getenv('NAVER_API_URL').rstrip('/')+'/chat/completions',
  json={'model':'gemini-2.0-flash','target_model_names':'gemini-2.0-flash',
        'messages':[{'role':'user','content':[{'type':'text','text':'hi'}]}],'max_tokens':10},
  headers={'Authorization':'Bearer '+os.getenv('NAVER_API_KEY'),'custom-llm-provider':'vertex_ai'},
  timeout=15)
print(r.status_code, r.text[:120])
"
# 기대: 200 + 'Hi there...'
```

### Step 4: 사용자에게 우선순위 확인
다음 3가지 중 무엇부터 할지 물어봄:

#### Option A: 회사 환경에서 Render 접근 테스트 (10분, 본인 결정)
- 회사 PC가 사내망인지 확인 (`curl namc-aigw.io.naver.com`이 외부 인터넷에서도 되는지)
- Render 서버가 NAMC 접근 가능한지 확인 → `curl https://ugc-monitor-api.onrender.com/health` + 작은 스캔 시도
- 결과: 가능하면 Vercel 배포 유지, 불가능하면 로컬 전용 확정

#### Option B: 4/17 미완료 Phase 3 마무리 (5-10분, 무료)
- `phase3_candidates.json` 그대로 (913명, 600명 처리됨, 313명 남음)
- ```bash
  python scripts/analyze_ugc.py --reference "ref_0416_1.jpg,ref_0416_2.jpg,ref_0416_3.jpg" --skip-stories
  ```
- 끝나면 → `python scripts/export_phase3_matched.py` (CSV + Sheets 새 탭 생성)
- Apify 불필요 (Phase 1, 2 스킵)

#### Option C: Apify 재결제 후 새 댓글 파일로 처음부터
- Apify 결제 재활성화: https://console.apify.com/account/billing
- 대시보드 (`./start.sh`) 또는 CLI로 진행

### Step 5: 사용자 답변 따라 진행

---

## 주요 파일 위치
- **메인 스크립트**: `scripts/analyze_ugc.py`
- **재시도 스크립트**: `scripts/retry_phase3_failures.py` (실패한 사용자만 다시 — 이미지를 직접 다운받아 base64로 보냄)
- **결과 export**: `scripts/export_phase3_matched.py` (CSV + Google Sheets 새 탭 `phase3_matched`)
- **정확도 측정**: `scripts/retest_phase3_labeled.py` (ground truth와 비교)
- **로컬 서버**: `./start.sh` → http://localhost:8000
- **백엔드**: `ugc-monitor-api/main.py` (FastAPI)
- **프롬프트 텍스트**: `reference_prompt.txt` (하이브리드 매칭의 핵심)
- **Ground truth**: `phase3_ground_truth_20260417.json` (63명 사용자 라벨)
- **이전 결과 백업**: `phase3_results_*.json.bak`, `phase3_failures_v1.json.bak`

## 참고 메모리
사용자의 자동 메모리 시스템(`/Users/user/.claude/projects/-Users-user-Desktop-claude-code-ai-prompt-ugc-monitoring/memory/`)에 추가 컨텍스트가 있을 수 있음. 항상 거기 먼저 확인.

## 절대 잊지 말 것
- **NAMC = NAVER 사내망 전용** (10.x.x.x 사설 IP). 외부 클라우드는 절대 못 씀.
- **하이브리드 매칭이 정확도의 핵심**: 프롬프트 텍스트 누락하면 41% precision으로 떨어짐
- **2/3 다수결 매칭 로직**: 1/3로 바꾸면 false positive 폭증
- **Apify 결제 상태**: 4/18 시점 OFF. 변경 여부 사용자 확인.
- **Render 배포는 거의 작동 안 했음**: 4/18까지 한 번도 정상 스캔 못 했을 가능성 높음
