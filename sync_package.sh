#!/usr/bin/env bash
# sync_package.sh
# 본체 repo의 최신 코드를 팀 배포용 패키지 폴더(ugc_monitor_v1_260422/수정금지/)로 복사합니다.
# 내정보/, 캠페인자료/, venv/ 같은 유저 고유 데이터는 건드리지 않아요.

set -e

SRC="$(cd "$(dirname "$0")" && pwd)"
DST="$SRC/../ugc_monitor_v1_260422/수정금지"

if [ ! -d "$DST" ]; then
  echo "❌ 패키지 폴더 없음: $DST"
  exit 1
fi

echo "════════════════════════════════════════════════"
echo "  패키지 동기화 (본체 → 수정금지)"
echo "════════════════════════════════════════════════"
echo "  source : $SRC"
echo "  target : $DST"
echo

# 1. index.html
echo "📄 index.html 복사"
cp "$SRC/index.html" "$DST/index.html"

# 2. scripts/ 전체 (venv/ __pycache__/ 발표용 스크립트는 제외)
echo "📁 scripts/ 동기화"
rsync -a --delete \
  --exclude="__pycache__" --exclude="*.pyc" \
  --exclude="build_presentation_pptx.py" \
  "$SRC/scripts/" "$DST/scripts/"

# 3. ugc-monitor-api/ (google_credentials.json은 유저 고유 파일이라 제외)
echo "📁 ugc-monitor-api/ 동기화"
rsync -a --delete \
  --exclude="__pycache__" --exclude="*.pyc" \
  --exclude=".git" --exclude=".gitignore" \
  --exclude="config/google_credentials.json" \
  "$SRC/ugc-monitor-api/" "$DST/ugc-monitor-api/"

echo
echo "════════════════════════════════════════════════"
echo "  ✅ 동기화 완료"
echo "════════════════════════════════════════════════"
echo
echo "다음 단계:"
echo "  1. 변경 사항 확인:  diff -rq \"$SRC/scripts\" \"$DST/scripts\""
echo "  2. zip 재생성:      (cd ..; zip -rq ugc_monitor_v1_260422.zip ugc_monitor_v1_260422 -x '*.DS_Store' '*__pycache__*')"
