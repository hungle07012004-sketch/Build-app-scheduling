# Benchmark Runner V5 — kết quả ngày 08/11/2026

## Kết luận

**Runner mới đã sẵn sàng về mặt kỹ thuật để chạy benchmark theo protocol được công bố.** Chưa chạy benchmark chính thức; kiểm thử nhỏ không chứng minh chất lượng nghiệm hoặc độ ổn định dài hạn trên 180–300+ jobs.

Entrypoint chuẩn trong project: `python -B -m backend.benchmark_v5`.
Không dùng `run_experiments.py` ngoài App để đại diện phiên bản solver này.

## Xác định đường chạy

- Luồng scheduling của App gọi `backend/services/algorithms/v5_adapter.py`:
  `solve_micro` → `backend.solver_v5.solve_ss_micro_ga_fast_instance.solve_instance`;
  `solve_exact` → `backend.solver_v5.solve_milp_instance.solve_instance` (CP-SAT).
- Helper cũ `backend/tests/benchmark_micro_stopping.py` chỉ chạy Micro-GA để so sánh stopping configuration, không phải paired benchmark.
- Runner ngoài App được kiểm tra ở lượt trước gọi GA cũ. Không có bằng chứng mới xác định lịch sử người dùng đã chạy lệnh nào, nên không tuyên bố script đó là runner thực tế của mọi benchmark trước đây.
- Runner mới gọi trực tiếp hai adapter App trên; không copy solver bên ngoài vào project, không sửa helper hoặc benchmark cũ.

## Protocol so sánh

`paired-single-worker-search-cap-v1`:

1. Một frozen V5 JSON, sao chép nguyên bytes; cùng jobs, calendar, processing/setup và objective `MIN_CMAX_FINAL_2C` cho cả hai thuật toán. Không regenerate hoặc mở rộng calendar. Input được validate trước khi chạy.
2. Mỗi seed: một attempt Micro-GA và một CP-SAT, mỗi thuật toán một worker, chạy tuần tự. Đảo thứ tự hai thuật toán giữa các cặp. `--repeats` cho phép kiểm tra lặp lại seed.
3. Cùng nominal search-time cap. Đây **không phải cùng end-to-end wall time, CPU-seconds hoặc evaluation count**:
   - Micro-GA tính seed refinement trong budget; dừng hợp tác giữa các decode. Epoch/decode/patience limits vẫn có thể dừng sớm.
   - CP-SAT áp dụng giới hạn bên trong `Solve()`, không bao gồm model construction.
   - Final reconstruction/validation có thể làm tổng thời gian dài hơn budget.
4. Ghi runtime do solver báo cáo và runtime end-to-end của lời gọi + output validation riêng. Không tính thời gian serialize artifact vào solver runtime.
5. Các settings khác giữ defaults App, trừ khi cung cấp `--settings`. Protocol override seed, runs=1, workers=1, hai time limits trong biến cục bộ; không thay cấu hình App. Cả requested/effective settings đều được lưu.

Hai thuật toán **được so sánh đúng điều kiện của protocol này**. Không nên mô tả kết quả là equal-total-compute benchmark. Trước chạy chính thức, cố định danh sách datasets/seeds, budget và settings; ghi rõ protocol trong luận văn và báo cáo các lần dừng sớm thay vì giả định chúng dùng hết thời gian.

## Artifacts và chống dùng nhầm kết quả

- `manifest.json`: input SHA-256, calendar SHA-256, objective, protocol, settings theo seed, defaults của signatures solver, Git HEAD/status, hash source backend, Python/package/platform versions, metadata input, experiment fingerprint.
- `frozen.json`: bản copy nguyên trạng; kiểm tra hash trước/sau mỗi lần gọi solver. Source backend cũng được kiểm tra; phát hiện thay đổi thì dừng comparison.
- Mỗi attempt có log và JSON: settings, raw result, standardized/validated result, seed/repeat, runtime, GA evaluations, seed-refinement evaluations, epochs, stop reason, Cmax/tardiness/flow, feasibility và validation issues.
- CP-SAT không có decoder-evaluation count tương đương GA: để null, ghi branches/conflicts riêng. Nếu exact stopping cause không được adapter cung cấp, ghi status và `cause_not_exposed`; không suy đoán timeout.
- `results.json`: tất cả runs và paired comparisons; `results.csv`: bảng metrics từng attempt.
- CP gap là ratio `(U-L)/U`. GA gap so với proven optimum chỉ có khi cả hai lịch qua validation, cùng pair và CP incumbent khớp bound. Nếu CP chưa optimal, chỉ ghi difference to incumbent. Thiếu kết quả hợp lệ thì để null, không gán 0.
- Không có cache/resume. **Từ chối mọi output directory đã tồn tại**, kể cả cùng input/config/code. Không ghi đè kết quả cũ. Khi một solver lỗi, lưu lỗi và tiếp tục thuật toán còn lại; cặp thiếu hai nghiệm hợp lệ không có gap.

## Thay đổi file

| File | Thay đổi |
|---|---|
| `backend/benchmark_v5.py` | Thêm runner độc lập; dùng adapters/validators hiện có. |
| `backend/tests/test_benchmark_runner.py` | Thêm 22 regression/integration cases. |
| `README.md` | Hướng dẫn CLI, protocol, artifacts và giới hạn. |
| `SCHEDULING_BENCHMARK_READINESS_REPORT.md` | Chỉ thêm liên kết cập nhật; giữ báo cáo lịch sử ngày 07/11. |
| Báo cáo này | Kết quả và evidence ngày 08/11. |

Không sửa solver, decoder, objective, production constraints, Working Calendar, UI, cấu hình App hoặc dữ liệu lịch sử.

## Kiểm thử thực tế

| Kiểm tra | Kết quả |
|---|---|
| Targeted runner tests | **PASS 22/22**, 5,73 giây |
| Toàn bộ backend | **PASS 272/272**, 71,39 giây, 0 fail/error/skip |
| CLI `--help` | PASS |
| CLI smoke thực tế | PASS, exit 0; 8/8 feasible, 4/4 paired comparisons hợp lệ |
| Chống overwrite/reuse | PASS cho input/settings/seed/code thay đổi và cả khi không đổi |
| Input/code thay đổi giữa benchmark | PASS, abort comparison |
| Một solver lỗi | PASS, giữ kết quả thuật toán còn lại, không tính gap sai |
| Tái lập fixture | PASS cùng schedule/KPI/evaluations/stop reason khi lặp cùng seed |
| Bảo toàn baseline | PASS: 261/261 file hiện có trong backend/code/src/tests giữ nguyên hash, gồm storage/database và solvers |

Một cảnh báo Starlette/httpx deprecation đã tồn tại; không có test skip. Không chạy frontend vì không có thay đổi frontend.

Smoke dùng **fixture tổng hợp 4 jobs, calendar 24 giờ**, không phải benchmark sản xuất 60 ngày. Hai seed 42/1051, mỗi seed 2 lần, nominal cap 5 giây/solver. Settings riêng cho smoke: Micro-GA population=4, generations=7; các giá trị này không thay App defaults.

| Algorithm | Seed | Cmax | Evaluations | Kết quả dừng | Thời gian end-to-end hai lần (giây) |
|---|---:|---:|---:|---|---|
| CP-SAT | 42 | 104 | Không tương đương GA | Optimal, bound=104 | 0,570 / 0,568 |
| CP-SAT | 1051 | 104 | Không tương đương GA | Optimal, bound=104 | 0,021 / 0,020 |
| SS Micro-GA Fast | 42 | 121 | 16 mỗi lần, chưa cộng seed refinement | generation_limit | 0,142 / 0,148 |
| SS Micro-GA Fast | 1051 | 121 | 15 mỗi lần, chưa cộng seed refinement | generation_limit | 0,142 / 0,146 |

GA gap so với optimum fixture: **16,34615385%**. Đây là smoke test 7 epochs; không dùng số này để kết luận hiệu quả GA trong luận văn. Fixed seed với wall-clock stopping ở quy mô lớn vẫn có thể khác nhau giữa lần chạy.

## Evidence

- [Targeted JUnit](.tmp-qa/2026-11-08-runner/target.xml)
- [Backend JUnit](.tmp-qa/2026-11-08-runner/backend.xml), [log](.tmp-qa/2026-11-08-runner/backend.log)
- [CLI manifest](.tmp-qa/2026-11-08-runner/cli-smoke/manifest.json)
- [CLI results](.tmp-qa/2026-11-08-runner/cli-smoke/results.json), [CSV](.tmp-qa/2026-11-08-runner/cli-smoke/results.csv)
- [Hash/reproducibility verification](.tmp-qa/2026-11-08-runner/verification.json)

Chỉ tạo artifacts mới trong `.tmp-qa/2026-11-08-runner/`. Tests dùng isolated data directory và `tmp_path`. Không chạy benchmark chính thức dài hạn hoặc ghi đè output cũ.

Lệnh thực tế của CLI smoke:

```powershell
.venv/Scripts/python.exe -B -m backend.benchmark_v5 --instance .tmp-qa/2026-11-08-runner/smoke-input.json --output-dir .tmp-qa/2026-11-08-runner/cli-smoke --seconds 5 --seeds 42 1051 --repeats 2 --settings .tmp-qa/2026-11-08-runner/smoke-settings.json
```

Thư mục smoke trên đã tồn tại nên chạy lại lệnh nguyên trạng sẽ bị từ chối; phải chọn thư mục mới.
