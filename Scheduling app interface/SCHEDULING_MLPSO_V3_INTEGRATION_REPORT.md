# Tích hợp MLPSO V3 với decoder V5 của App

Ngày 10/11/2026. Phạm vi: solver package và kiểm thử; chưa đăng ký API, chưa mở MLPSO trên UI, chưa sửa benchmark runner và chưa chạy benchmark chính thức.

## A. File được tạo và bảo toàn

- `backend/solver_v5/solve_mlpso_v3_instance.py`: đóng gói bản MLPSO V3 có sẵn từ `correct workstation/fast decode/solve_mlpso_v3_instance.py`, chỉ điều chỉnh tương thích và các lỗi correctness/termination bên dưới.
- `backend/tests/test_mlpso_v3.py`: 53 regression cases.
- Báo cáo này và artifacts mới trong `.tmp-qa/mlpso-v3-integration-20261110/`.

Không sửa production source có sẵn. Hash của decoder Fast/Reference, GA, CP-SAT, MLPSO legacy, frontend, cấu hình và frozen inputs giữ nguyên. Source V3 bên ngoài giữ nguyên SHA-256 `55aa0545e35289472c06ce0fcd966efa504ec89c15613367e08742c63927392a`.

**Giới hạn xác nhận database:** so sánh baseline phát hiện SQLite/WAL/SHM thay đổi hoặc biến mất trong lúc App đang mở. Kiểm tra SQLite ở chế độ `mode=ro&immutable=1` cho thấy run `182741f9f6874dcfb8a7debff0b834c5` được tạo lúc 15:59:45 và hoàn tất 16:01:30 giờ Việt Nam, sau baseline 15:56:35; thời điểm hoàn tất trùng mtime SQLite. Có process Python khởi động từ 15:25:56, trước lượt này. Các lệnh pytest của lượt này đặt `SCHEDULING_DATA_DIR` vào thư mục QA; test smoke chỉ ghi JSON/CSV trong `tmp_path`, không gọi API lưu run. Bằng chứng phù hợp với App hoạt động đồng thời, nhưng không có snapshot nội dung trước đó để quy mọi thay đổi database cho một nguồn. Vì vậy **không tuyên bố database giữ nguyên từng byte**, không restore hay ghi đè database hiện tại. Chi tiết: `integrity.json`, `database-observation.json`.

## B. Shared decoder

`solve_mlpso_v3_instance.py:77`: `_decoder_api(True)` dùng relative import từ `.benchmark_common_fast`; tùy chọn reference dùng `.benchmark_common`. Bỏ `sys.path` và tìm decoder bên ngoài.

Test runtime xác nhận class identity:

```python
mlpso._decoder_api(True)[0] is micro.V5ScheduleDecoder
# module: backend.solver_v5.benchmark_common_fast
```

Particle là permutation theo mapping `decoder.jobs`/`decoder.job_index`, không suy từ hậu tố Job ID. Fitness dùng decoder hiện có và objective `MIN_CMAX_FINAL_2C`. So sánh 24 permutations trên fixture 4 jobs: cùng input/calendar/chromosome cho cùng fitness; lịch canonical của các candidate khả thi khớp nhau. Hai chromosome tốt nhất trên frozen 20/40 jobs cũng được đánh giá lại qua đường GA, Cmax khớp trong tolerance 1e-6 phút.

## C. Thay đổi tương thích và correctness

| Vị trí | Nguyên nhân và xử lý |
|---|---|
| `_decoder_api`, dòng 77 | Ghim decoder package của App; không phụ thuộc đường dẫn external. |
| `BudgetEvaluator`, dòng 178 | Validate permutation trước cache/fitness: thiếu, trùng, ngoài miền, bool/float hoặc type không hợp lệ nhận fitness nội bộ vô hạn; không decode, không cache và không crash. Tránh bool/float alias vào cache key của chromosome hợp lệ. |
| `BudgetEvaluator.evaluate`, dòng 201 | Lưu mọi incumbent hữu hạn tốt hơn ngay khi đánh giá, gồm evaluation cuối trong local search/polish. Ghi evaluation, iteration, phase và time-to-best thực tế. |
| `_effective_population_size`, dòng 373 | Từ chối population không thể tạo đủ permutation duy nhất trên instance rất nhỏ, thay vì treo initialization. Không tự đổi population. |
| `local_search_gbest`, dòng 479 | Commit nghiệm cải thiện ở round hiện tại trước khi thoát vì hết budget. Trước sửa có thể bỏ nghiệm tốt ở decode cuối. |
| `elite_polish_phase`, dòng 527 | Dừng sau `max(32, stall_rounds*4)` round liên tiếp không phát sinh decode mới, tránh vòng lặp chỉ truy cache; báo `no_new_candidates`. Đây là thay đổi điều kiện dừng cho trường hợp không tiêu được budget, không thêm operator mới. |
| `run_one_mlpso`, dòng 731 | Reconcile incumbent từ evaluator, xử lý an toàn toàn bộ candidate infeasible; re-decode nghiệm cuối và đối chiếu Cmax. Không dựng lịch nếu chưa có fitness hữu hạn. Từ chối iterations âm. |
| `run_one_mlpso` và convergence CSV | Điểm cuối khớp iteration/evaluations/Cmax/stop reason; thay điểm cùng iteration, không tạo iteration polish giả. Không dùng thêm RNG hay decode chỉ để logging. |
| `solve_instance`, dòng 1064 | Hash input trước/sau; xuất metadata decoder và hash source, feasibility, best iteration/phase, stop reason và số decode kiểm tra cuối. Reconstruct lịch bằng decoder và kiểm tra Cmax, runtime bao gồm bước này. |
| `_json_finite`, dòng 86; CLI, dòng 1339 | Diagnostic không hữu hạn xuất JSON `null`, không dùng 0 thay cho missing. Nhánh không có nghiệm giữ schedule rỗng, không tuyên bố input infeasible. CLI xuất JSON/CSV an toàn cho cả thành công và chưa tìm được nghiệm. |

Giữ thuật toán V3: particle initialization, Pbest/subgroup Gbest, migration, repair, screening và các operator mutation/crossover. 21 top-level functions có AST giống source gốc. Test chạy source V3 gốc với decoder được thay bằng App decoder trong bộ nhớ cho thấy đường search bình thường vẫn cùng chromosome, schedule, evaluation và migration count. Đây không phải chứng minh tương đương mọi đường chạy: các lỗi incumbent, validation và cache-only termination ở trên được sửa có chủ ý.

Repair product remainder giữ nguyên, được đối chiếu với repair của GA. Repair chỉ điều chỉnh thứ tự; không bảo đảm mọi permutation sau repair có lịch khả thi. Decoder vẫn quyết định feasibility vật lý; candidate không feasible không được chọn làm best feasible.

## D. Kết quả smoke thực tế

Nguồn: đúng hai `frozen.json` của pilot ngày 08/11, không regenerate. Một run, seed 42, population 6/subpops 3, iterations tối đa 4, search decode cap 60, migration_every=1, ls_every=1, polish_budget=12; serial. Đây là cấu hình smoke riêng, không sửa settings App. MR/PCR/GCR và operator giữ defaults V3.

| Instance | Jobs | Thực thi/validator | Cmax (phút) | Runtime solver (s) | Search evaluations | Stop reason | Events |
|---|---:|---|---:|---:|---:|---|---:|
| AL13_n020_i01 | 20 | PASS / feasible | 8.835,181 | 0,476 | 60 | decoder_budget | 1.240 |
| AL13_n040_i01 | 40 | PASS / feasible | 16.551,093 | 0,899 | 60 | decoder_budget | 2.454 |

Kết quả lấy từ lần full backend cuối, không chọn lần chạy có Cmax tốt hơn. Runtime là `total_runtime_sec` trong solver, không gồm pytest và bước validator ngoài solver. Ngoài 60 search evaluations, mỗi solver call có một decode kiểm tra metrics cuối run và một decode tái dựng schedule; được ghi riêng. Test còn decode lại độc lập và qua đường đánh giá GA.

SHA-256 input:

- 20 jobs: `62d8d830242ae6948fee677c9706a77feb24a1d00db5bd70c62b6fc572c2b8f8`.
- 40 jobs: `78ff8981bea147558dba7ee6cd3bf5e24d997100b9c712e41592f76b4b4e38b4`.

Cả hai: đủ chromosome và đủ job contributors ở final 2C; schedule sau re-decode khớp; validator độc lập hiện có trả `validation_issues=[]`; terminal convergence khớp summary. Không có chứng nhận optimum nên không báo optimality gap bằng 0 hay tuyên bố chất lượng vượt GA/CP-SAT.

## E. Kiểm chứng ràng buộc

| Nhóm | Bằng chứng và giới hạn |
|---|---|
| 2B, PRE/FINAL streaming và precedence | Lịch thật qua validator V5; regression phá precedence hoặc bỏ operation bị từ chối. Không thay decoder streaming. |
| Workstation, OP_2A/OP_PACK và occupancy | `standardize` dùng validator V5 hiện có; test sai resource assignment bị từ chối. Full suite gồm các test tài nguyên/overlap hiện có. |
| OIL không preempt, capacity, load/auto/unload | Frozen schedules qua validator; test chỉnh load phase hoặc Cycle_Qty=7 bị từ chối. OIL logic giữ trong decoder chung, không dựng row thủ công. |
| Exact 2C, Batch Key, same-key 5+5 | Fixture thật có pooled 5+5 và đủ SETUP/JOB_OP/OIL_CYCLE/2C_BATCH; test sai key hoặc composition bị từ chối; frozen outputs đủ jobs ở 2C. |
| Product setup/clean pipeline | Repair/operator so với GA, lịch qua validator; bỏ setup bị từ chối. Không biến repair thành kiểm tra feasibility toàn diện. |
| Calendar, break, downtime | Fixture có availability gaps; đặt operation vào gap bị từ chối. Frozen lịch chỉ được chứng nhận theo availability đã lưu trong input. |
| Cmax/tardiness/flow | Output qua chuẩn hóa và KPI validation hiện có, decode lại Cmax, test chỉnh Cmax bị từ chối. Không đổi công thức KPI hay objective. |

**Known calendar issue vẫn còn:** hai frozen datasets đã được pilot ghi nhận chồng weekly shutdown từng mô tả. PASS theo frozen calendar không đồng nghĩa tuân thủ một weekly rule khác. Không sửa calendar hay concept để làm kết quả PASS. Các test trên và full suite là bằng chứng hữu hạn, không phải chứng minh mọi instance luôn khả thi.

## F. Regression và tái lập

- Trước sửa: 4/4 regression mục tiêu FAIL: decoder identity/import, invalid-cache alias, mất cải thiện ở decode cuối local search, crash khi toàn bộ infeasible. `before-final.xml`.
- Sau sửa: **53/53 PASS** test MLPSO mới; không skip. `target-final.xml`.
- Full backend: **325/325 PASS**, không fail/error/skip, **80,13 giây**. Bao gồm 272 test hiện có và 53 test mới; không cộng các lần chạy lặp thành số test mới.
- Một warning Starlette/httpx deprecation có sẵn. Không chạy frontend tests vì không chỉnh frontend.
- Reproducibility trên fixture nhỏ: lặp cùng seeds 42/1051, hai attempts, budget evaluations/iterations cho cùng chromosome/schedule/KPI và evaluations. Không suy rộng sang parallel process hay wall-clock benchmark lớn; không claim bitwise reproducibility của runtime.
- Kiểm tra CLI package cho cả feasible và chưa có nghiệm, strict JSON và CSV PASS. Kiểm tra permutation/operator/repair/decode/fitness, cache/budget, global-best migration và trace cuối PASS.

## G. Việc còn lại

1. Chưa đăng ký MLPSO V3 vào backend dispatch/result lifecycle, API availability hoặc UI; MLPSO V5 trên UI vẫn khóa theo yêu cầu.
2. `backend.benchmark_v5` chưa chạy MLPSO. Cần tích hợp riêng có tests cho dispatch, fingerprint/provenance, output và gap.
3. V3 hiện có evaluation/iteration/patience limits, **chưa có wall-clock search cap**. Không dùng cùng một số giây rồi tuyên bố equal-budget. Cần chốt protocol trước: cùng frozen input/hash/calendar/objective, seeds, attempts/workers và ngân sách; ghi search calls, cache hits, init/refinement/polish và các decode validation/reconstruction theo cùng quy ước.
4. Chưa benchmark dài hạn, multi-seed trên 166–180 jobs hay đánh giá thống kê chất lượng. Không thêm time cap hoặc tuning trong nhiệm vụ này.
5. Cần xác nhận frozen calendar là calendar chuẩn của nghiên cứu; conflict với weekly rule lịch sử chưa được giải quyết trong phạm vi tích hợp solver.
6. Byte identity của database đang được App dùng không được chứng nhận; không ghi đè/restore để che khác biệt.

## H. Kết luận

**PASS ở cấp solver package:** MLPSO V3 dùng đúng class decoder của GA, chạy được frozen 20/40 jobs, tạo lịch đủ jobs được validator V5 chấp nhận và cùng chromosome nhận cùng Cmax qua hai đường fitness. Sẵn sàng cho bước tích hợp backend có kiểm thử.

**Chưa sẵn sàng benchmark GA–MLPSO chính thức hoặc bật UI ngay:** còn API/runner integration, protocol ngân sách và xác nhận calendar. Không thay đổi physical constraints, decoder, GA/CP-SAT hoặc dữ liệu frozen; không tuyên bố ưu thế thuật toán từ smoke test.

## Artifacts

Tất cả dưới `.tmp-qa/mlpso-v3-integration-20261110/`:

- `baseline.json`, `integrity.json`, `database-observation.json`.
- `before-final.xml`/`.log`, `target-final.xml`/`.log`, `backend-final.xml`/`.log`.
- `full-temp/test_original_frozen_smoke_20_0/` và `full-temp/test_original_frozen_smoke_40_0/`: `raw.json`, `validated.json`, `verification.json`.
