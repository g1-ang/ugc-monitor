"""
retest_phase3_labeled.py
────────────────────────
phase3_ground_truth_*.json (사용자 라벨링 정답)에 대해
새로운 프롬프트 + 매칭 로직(2/3 다수결)으로 재평가하고
정확도(precision/recall/F1)를 계산.

기본 동작:
  - 사용자가 라벨링한 63명 + 그들의 type 사용
  - 이미지는 phase3_candidates.json에서 lookup, retry 스크립트의 url_to_data_uri로 다운로드
  - 새 프롬프트(분석 strict) + 2/3 다수결로 재판정
  - 결과를 ground truth와 비교 → 변화 보고서

실행:
  python scripts/retest_phase3_labeled.py --reference "ref_0416_1.jpg,ref_0416_2.jpg,ref_0416_3.jpg"
"""

from __future__ import annotations
import os, sys, json, argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze_ugc import file_to_data_uri  # 새 프롬프트 + 새 로직 적용된 모듈
from retry_phase3_failures import url_to_data_uri, call_model_b64

load_dotenv()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--reference", required=True)
    p.add_argument("--prompt-file", default="reference_prompt.txt")
    p.add_argument("--ground-truth", default="phase3_ground_truth_20260417.json")
    p.add_argument("--candidates",   default="phase3_candidates.json")
    p.add_argument("--out",          default="phase3_retest_results.json")
    args = p.parse_args()

    prompt_text = ""
    if args.prompt_file and os.path.exists(args.prompt_file):
        with open(args.prompt_file, encoding="utf-8") as f:
            prompt_text = f.read().strip()
        print(f"📝 프롬프트 텍스트: {len(prompt_text)} 글자 (하이브리드)")
    else:
        print(f"⚠️  프롬프트 파일 없음 — 이미지만으로 비교")

    print("=" * 60)
    print("  Phase 3 재평가 — 새 프롬프트 + 2/3 다수결 vs ground truth")
    print("=" * 60)

    # 레퍼런스
    refs = [r.strip() for r in args.reference.split(",") if r.strip()]
    ref_uris = [file_to_data_uri(rp) for rp in refs]
    print(f"레퍼런스 {len(ref_uris)}장 로드")

    # ground truth & candidates
    with open(args.ground_truth, encoding="utf-8") as f:
        gt = json.load(f)
    labels = gt["labels"]  # username → {type, label, [url]}

    with open(args.candidates, encoding="utf-8") as f:
        cands = json.load(f)
    cand_map = {u["username"]: u for u in cands}

    # 평가 대상 추출 (스토리는 24h 만료 가능성 → user 요청대로 제외 옵션 가능하나, 일단 다 평가)
    targets = []
    skipped_no_image = []
    for uname, info in labels.items():
        utype = info["type"]
        cand = cand_map.get(uname, {})

        # 같은 type의 이미지 URL 가져오기
        img_url = None
        if utype == "profile":
            img_url = cand.get("profile_url")
        elif utype == "story":
            stories = cand.get("story_image_urls") or (
                [cand["story_image_url"]] if cand.get("story_image_url") else [])
            img_url = stories[0] if stories else None
        elif utype == "feed":
            # ground truth url과 매칭되는 feed item 찾기
            wanted = info.get("url", "")
            for item in cand.get("latest_feed_items") or []:
                if item.get("post_url") == wanted:
                    img_url = item.get("image_url"); break
            if not img_url and cand.get("latest_feed_items"):
                img_url = cand["latest_feed_items"][0].get("image_url")

        if not img_url:
            skipped_no_image.append(uname)
            continue
        targets.append((uname, utype, img_url, info["label"]))

    print(f"평가 대상: {len(targets)}명 (이미지 없음 skip: {len(skipped_no_image)})")
    print("─" * 60)

    # 재평가
    rows = []  # (username, type, gt, new_pred, yes_count, error)

    def evaluate(t):
        uname, utype, img_url, gt_label = t
        target_uri = url_to_data_uri(img_url)
        if not target_uri:
            return (uname, utype, gt_label, "ERROR", -1, True)
        with ThreadPoolExecutor(max_workers=len(ref_uris)) as ex:
            futs = [ex.submit(call_model_b64, ru, target_uri, utype, prompt_text)
                    for ru in ref_uris]
            results = [fut.result() for fut in as_completed(futs)]
        yes_count = sum(1 for r in results if r is True)
        had_err   = any(r is None for r in results)
        new_pred  = "yes" if yes_count >= 2 else "no"
        return (uname, utype, gt_label, new_pred, yes_count, had_err)

    done = 0
    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = {ex.submit(evaluate, t): t for t in targets}
        for fut in as_completed(futs):
            r = fut.result()
            done += 1
            uname, utype, gt_label, new_pred, yes_count, err = r
            mark = "✓" if new_pred == gt_label else "✗"
            err_str = " [에러있음]" if err else ""
            print(f"  [{done}/{len(targets)}] {mark} @{uname:18s} {utype:8s}  "
                  f"gt={gt_label:3s}  new={new_pred:5s}  ({yes_count}/3 YES){err_str}")
            rows.append({
                "username": uname, "type": utype,
                "ground_truth": gt_label, "new_prediction": new_pred,
                "yes_count": yes_count, "had_error": err,
            })

    # 정확도 집계
    print(f"\n{'='*60}")
    print(f"  결과 분석")
    print(f"{'='*60}")

    # 전체
    valid = [r for r in rows if r["new_prediction"] != "ERROR"]
    tp = sum(1 for r in valid if r["ground_truth"] == "yes" and r["new_prediction"] == "yes")
    fp = sum(1 for r in valid if r["ground_truth"] == "no"  and r["new_prediction"] == "yes")
    tn = sum(1 for r in valid if r["ground_truth"] == "no"  and r["new_prediction"] == "no")
    fn = sum(1 for r in valid if r["ground_truth"] == "yes" and r["new_prediction"] == "no")

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1        = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    print(f"\n전체 ({len(valid)}명, 이미지 다운 실패 {len(rows)-len(valid)}명 제외):")
    print(f"  TP (yes→yes): {tp:3d}  ← 정답 매치 유지")
    print(f"  FP (no→yes):  {fp:3d}  ← false positive (낮을수록 좋음)")
    print(f"  TN (no→no):   {tn:3d}  ← 오인을 잘 걸러냄")
    print(f"  FN (yes→no):  {fn:3d}  ← 진짜 매치를 놓침 (낮을수록 좋음)")
    print(f"\n  정확도 (Precision):  {precision*100:5.1f}%   = TP/(TP+FP)")
    print(f"  재현율 (Recall):     {recall*100:5.1f}%   = TP/(TP+FN)")
    print(f"  F1 score:            {f1*100:5.1f}%")

    # 유형별
    print(f"\n유형별 분석:")
    for t in ("feed", "story", "profile"):
        v = [r for r in valid if r["type"] == t]
        if not v:
            print(f"  {t:8s}: 데이터 없음"); continue
        tp_t = sum(1 for r in v if r["ground_truth"] == "yes" and r["new_prediction"] == "yes")
        fp_t = sum(1 for r in v if r["ground_truth"] == "no"  and r["new_prediction"] == "yes")
        tn_t = sum(1 for r in v if r["ground_truth"] == "no"  and r["new_prediction"] == "no")
        fn_t = sum(1 for r in v if r["ground_truth"] == "yes" and r["new_prediction"] == "no")
        prec = tp_t / (tp_t + fp_t) if (tp_t + fp_t) > 0 else 0
        rec  = tp_t / (tp_t + fn_t) if (tp_t + fn_t) > 0 else 0
        print(f"  {t:8s}: TP={tp_t}  FP={fp_t}  TN={tn_t}  FN={fn_t}  "
              f"→ precision={prec*100:5.1f}%, recall={rec*100:5.1f}%")

    # 변화 케이스 자세히
    print(f"\n중요 케이스:")
    if fn > 0:
        print(f"\n  ⚠️  진짜 매치를 놓친 케이스 (FN — 가장 위험):")
        for r in valid:
            if r["ground_truth"] == "yes" and r["new_prediction"] == "no":
                print(f"     · @{r['username']} ({r['type']}) — {r['yes_count']}/3 YES")
    if fp > 0:
        print(f"\n  ⚠️  여전히 false positive (FP):")
        for r in valid:
            if r["ground_truth"] == "no" and r["new_prediction"] == "yes":
                print(f"     · @{r['username']} ({r['type']}) — {r['yes_count']}/3 YES")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({
            "summary": {
                "total": len(valid), "tp": tp, "fp": fp, "tn": tn, "fn": fn,
                "precision": precision, "recall": recall, "f1": f1,
            },
            "rows": rows,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n저장: {args.out}")


if __name__ == "__main__":
    main()
