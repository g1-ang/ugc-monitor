#!/usr/bin/env bash
# auto_scan_daily.sh
# 매일 정해진 시간에 launchd 가 호출 — UGC_pending/ 폴더에 있는 모든 스캔 작업을 자동 실행.
#
# 폴더 구조 (사용자가 미리 준비):
#   ~/UGC_pending/
#   └── <campaign_name>/                     예: '자동차샷_4월24일'
#       ├── comments.xlsx                   (필수) 댓글 추출 파일
#       ├── reference_prompt.txt            (필수) AI 프롬프트
#       └── ref_*.jpg / ref_*.png           (필수) 레퍼런스 이미지 1~5장
#
# 실행 후:
#   - 결과: phase3_matched 시트에 자동 저장
#   - 처리된 폴더는 ~/UGC_done/ 으로 이동
#   - 로그: /tmp/ugc_auto_scan_<날짜>.log

set -e

PROJECT_ROOT="/Users/user/Desktop/claude code/ai prompt ugc monitoring"
PENDING_DIR="$HOME/UGC_pending"
DONE_DIR="$HOME/UGC_done"
LOG_FILE="/tmp/ugc_auto_scan_$(date +%Y%m%d).log"

mkdir -p "$PENDING_DIR" "$DONE_DIR"

echo "════════════════════════════════════════════════" | tee -a "$LOG_FILE"
echo "$(date '+%F %T')  자동 스캔 시작" | tee -a "$LOG_FILE"
echo "════════════════════════════════════════════════" | tee -a "$LOG_FILE"

# NAMC VPN 체크
NAMC_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 https://namc-aigw.io.naver.com/ 2>/dev/null || echo "000")
if [ "$NAMC_CODE" = "000" ]; then
    echo "❌ NAMC 연결 실패 — VPN 확인 필요. 스캔 중단." | tee -a "$LOG_FILE"
    exit 1
fi
echo "✓ NAMC OK (HTTP $NAMC_CODE)" | tee -a "$LOG_FILE"

# 처리할 캠페인 폴더 목록
shopt -s nullglob
campaigns=("$PENDING_DIR"/*/)
if [ ${#campaigns[@]} -eq 0 ]; then
    echo "처리할 폴더 없음 (~/UGC_pending/ 비어있음)" | tee -a "$LOG_FILE"
    exit 0
fi

cd "$PROJECT_ROOT"
source venv/bin/activate 2>/dev/null || true

for campaign_dir in "${campaigns[@]}"; do
    campaign_name=$(basename "$campaign_dir")
    echo | tee -a "$LOG_FILE"
    echo "──────── 캠페인: $campaign_name ────────" | tee -a "$LOG_FILE"

    # 필수 파일 체크
    xlsx_file=$(ls "$campaign_dir"*.xlsx 2>/dev/null | head -1)
    prompt_file="$campaign_dir/reference_prompt.txt"
    ref_files=("$campaign_dir"ref_*.jpg "$campaign_dir"ref_*.png)
    ref_files_filtered=()
    for f in "${ref_files[@]}"; do
        [ -f "$f" ] && ref_files_filtered+=("$f")
    done

    if [ -z "$xlsx_file" ]; then
        echo "  ⚠️ 스킵: xlsx 댓글 파일 없음" | tee -a "$LOG_FILE"
        continue
    fi
    if [ ! -f "$prompt_file" ]; then
        echo "  ⚠️ 스킵: reference_prompt.txt 없음" | tee -a "$LOG_FILE"
        continue
    fi
    if [ ${#ref_files_filtered[@]} -eq 0 ]; then
        echo "  ⚠️ 스킵: ref_*.jpg/png 레퍼런스 이미지 없음" | tee -a "$LOG_FILE"
        continue
    fi

    echo "  ✓ 댓글: $(basename "$xlsx_file")" | tee -a "$LOG_FILE"
    echo "  ✓ 프롬프트: reference_prompt.txt ($(wc -c < "$prompt_file") bytes)" | tee -a "$LOG_FILE"
    echo "  ✓ 레퍼런스: ${#ref_files_filtered[@]}장" | tee -a "$LOG_FILE"

    # 대시보드 백엔드 호출 (서버가 실행 중이어야 함)
    if ! curl -s -o /dev/null --max-time 3 http://127.0.0.1:8000/health; then
        echo "  ⚠️ 로컬 서버 미실행 → 시작" | tee -a "$LOG_FILE"
        cd ugc-monitor-api
        nohup uvicorn main:app --host 127.0.0.1 --port 8000 >> "$LOG_FILE" 2>&1 &
        cd ..
        sleep 6
    fi

    # multipart form 으로 /scan 호출
    refs_args=()
    i=1
    for f in "${ref_files_filtered[@]:0:5}"; do
        refs_args+=(-F "reference_image_$i=@$f")
        i=$((i + 1))
    done

    echo "  → 스캔 시작 (백그라운드)" | tee -a "$LOG_FILE"
    curl -s -X POST http://127.0.0.1:8000/scan \
        -F "comment_file=@$xlsx_file" \
        -F "campaign_name=$campaign_name" \
        -F "reviewer=auto-scheduler" \
        -F "prompt_text=$(cat "$prompt_file")" \
        "${refs_args[@]}" | tee -a "$LOG_FILE"
    echo | tee -a "$LOG_FILE"

    # 스캔 완료 폴링 (최대 90분)
    for _ in $(seq 1 540); do
        sleep 10
        status=$(curl -s http://127.0.0.1:8000/results | python3 -c 'import sys,json; print(json.load(sys.stdin).get("status",""))' 2>/dev/null || echo "")
        if [ "$status" = "done" ] || [ "$status" = "error" ]; then
            break
        fi
    done

    final_state=$(curl -s http://127.0.0.1:8000/results)
    matches=$(echo "$final_state" | python3 -c 'import sys,json; print(len(json.load(sys.stdin).get("results",[])))')
    echo "  ✓ 완료 — 매치 ${matches}건" | tee -a "$LOG_FILE"

    # 처리 완료 폴더 이동
    mv "$campaign_dir" "$DONE_DIR/$(date +%Y%m%d)_$campaign_name"
    echo "  → ~/UGC_done/ 으로 이동됨" | tee -a "$LOG_FILE"

    # auto-scheduler 모드는 자동 검수 X — 사람이 나중에 대시보드 들어가서 직접 검수
done

echo | tee -a "$LOG_FILE"
echo "$(date '+%F %T')  자동 스캔 종료" | tee -a "$LOG_FILE"
