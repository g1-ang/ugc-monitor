#!/usr/bin/env bash
# UGC Monitor — 최초 설치 스크립트
# 사용법: ./install.sh
#
# 한 번만 실행하면 됨. 끝나면 ./start.sh 로 서버 시작.

set -e
cd "$(dirname "$0")"

echo "════════════════════════════════════════════════"
echo "  UGC Monitor — 최초 설치"
echo "════════════════════════════════════════════════"
echo

# 1. Python 3.11-3.13 체크 (3.14+ 는 일부 패키지 wheel 미지원)
echo "1️⃣  Python 버전 체크..."
# python3.12 우선, 없으면 python3
PY_BIN=""
for v in 3.12 3.13 3.11; do
    if command -v "python$v" >/dev/null 2>&1; then
        PY_BIN="python$v"
        break
    fi
done
if [ -z "$PY_BIN" ] && command -v python3 >/dev/null 2>&1; then
    PYV=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PYMAJOR=$(echo $PYV | cut -d. -f1)
    PYMINOR=$(echo $PYV | cut -d. -f2)
    if [ "$PYMAJOR" = "3" ] && [ "$PYMINOR" -ge 11 ] && [ "$PYMINOR" -le 13 ]; then
        PY_BIN="python3"
    fi
fi
if [ -z "$PY_BIN" ]; then
    echo "   ❌ Python 3.11/3.12/3.13 이 필요합니다 (3.14+ 는 sentence-transformers/Pillow wheel 미지원)"
    echo "   👉 설치: brew install python@3.12"
    echo "      또는 python.org/downloads 에서 3.12 인스톨러"
    exit 1
fi
echo "   ✅ $PY_BIN 사용"

# 2. venv 생성
echo
echo "2️⃣  가상환경 생성..."
if [ -d "venv" ]; then
    echo "   venv 폴더 이미 있음 — 건너뜀"
else
    $PY_BIN -m venv venv
    echo "   ✅ venv 생성됨 ($PY_BIN)"
fi

# 3. 의존성 설치
echo
echo "3️⃣  Python 패키지 설치 중 (5-10분 소요, sentence-transformers + torch 다운로드)..."
source venv/bin/activate
pip install --upgrade pip --quiet
# macOS 는 어차피 CPU 빌드만 사용 (NVIDIA GPU 없음) — 버전 고정 X, PyPI 최신
pip install --no-cache-dir torch --quiet
pip install -r ugc-monitor-api/requirements.txt --quiet
echo "   ✅ 패키지 설치 완료"

# 4. .env 안내
echo
echo "4️⃣  환경 변수 (.env) 설정..."
if [ -f ".env" ]; then
    echo "   .env 파일 이미 있음 — 건너뜀"
else
    cp .env.example .env
    echo "   ✅ .env 파일 생성됨 (.env.example 복사)"
    echo "   👉 텍스트 에디터로 .env 열어서 시크릿 값 채우기:"
    echo "      open .env"
fi

# 5. Google Credentials 안내
echo
echo "5️⃣  Google Credentials (config/google_credentials.json) 안내..."
if [ -f "config/google_credentials.json" ]; then
    echo "   ✅ Google credentials 파일 발견"
else
    mkdir -p config
    echo "   ⚠️  config/google_credentials.json 없음"
    echo "   👉 전지원(angela.jeon@snowcorp.com) 에게 service account JSON 요청"
    echo "      → 받은 파일을 config/google_credentials.json 에 저장"
fi

echo
echo "════════════════════════════════════════════════"
echo "  설치 끝!"
echo "════════════════════════════════════════════════"
echo
echo "📋 다음 단계:"
echo "  1) .env 파일에 시크릿 값 채우기 (전지원에게 요청)"
echo "  2) config/google_credentials.json 파일 받기"
echo "  3) NAVER 사내망 VPN 연결"
echo "  4) ./start.sh 실행 → 브라우저 자동 오픈"
echo
