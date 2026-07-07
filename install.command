#!/usr/bin/env bash
# 더블클릭으로 실행 가능한 최초 설치 wrapper
cd "$(dirname "$0")"
./install.sh
echo
echo "════════════════════════════════════════════════"
echo "  설치 완료! 이제 'run.command' 파일을 더블클릭해서"
echo "  UGC 모니터를 실행하세요."
echo "  이 창은 Cmd+W 로 닫을 수 있어요."
echo "════════════════════════════════════════════════"
