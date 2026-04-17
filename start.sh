#!/usr/bin/env bash
# UGC Monitor 로컬 실행 스크립트
# 사용법: ./start.sh
# 사전: NAVER 사내망 VPN 연결 필수 (NAMC API 호출용)

set -e
cd "$(dirname "$0")"

echo "════════════════════════════════════════════════"
echo "  UGC Monitor — 로컬 실행"
echo "════════════════════════════════════════════════"

# 1. VPN 안내
echo "⚠️  NAVER 사내망 VPN 연결 확인 (NAMC API 호출에 필요)"
echo

# 2. 가상환경 활성화 (있으면)
if [ -d "venv" ]; then
    echo "🐍 venv 활성화"
    source venv/bin/activate
fi

# 3. 의존성 확인 (fastapi 없으면 설치)
if ! python -c "import fastapi" 2>/dev/null; then
    echo "📦 백엔드 의존성 설치 중..."
    pip install -r ugc-monitor-api/requirements.txt
fi

# 4. NAMC 연결 사전 체크 (5초)
echo "🔍 NAMC 연결 체크..."
NAMC_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 https://namc-aigw.io.naver.com/ 2>/dev/null || echo "000")
if [ "$NAMC_CODE" = "000" ]; then
    echo "   ❌ NAMC 연결 안됨 — VPN 확인 후 다시 실행"
    echo "   (계속 진행은 가능하지만 스캔 시 실패합니다)"
    echo
fi

# 5. 서버 시작 + 2초 후 브라우저 자동 오픈
echo "🚀 서버 시작 → http://localhost:8000"
echo "   (Ctrl+C로 종료)"
echo

(sleep 2 && open http://localhost:8000) &

cd ugc-monitor-api
exec uvicorn main:app --host 127.0.0.1 --port 8000
