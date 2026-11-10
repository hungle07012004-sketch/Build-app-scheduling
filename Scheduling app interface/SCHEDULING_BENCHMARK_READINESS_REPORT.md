# Kiểm tra convergence và mức độ sẵn sàng benchmark V5

Ngày 07/11/2026. Tiếp nối bản sửa incumbent/permutation; không chạy benchmark chính thức.

**Cập nhật 08/11/2026:** Đã bổ sung runner dùng solver App và kiểm thử. Xem [báo cáo Benchmark Runner](SCHEDULING_BENCHMARK_RUNNER_REPORT.md). Nội dung bên dưới giữ nguyên kết quả lịch sử ngày 07/11.

## Kết quả

| Hạng mục | Kết quả | Bằng chứng / giới hạn |
|---|---|---|
| Điểm cuối convergence | PASS | Khớp epoch, evaluations, Cmax, evaluations/time-to-best và stop reason với summary từng run. |
| Dừng giữa batch, restart/immigrant, hết decode budget | PASS | Regression bao gồm nghiệm cải thiện ở evaluation cuối và trường hợp tìm được nghiệm khả thi đầu tiên. |
| Không tìm được nghiệm | PASS | Giữ summary và trace để chẩn đoán; không biến kết quả thành feasible. |
| Tắt/bật convergence | PASS | Không đổi chromosome, schedule, KPI, crossover counts hoặc evaluation count trong kiểm thử cùng seed và ngân sách epochs/evaluations. |
| Dispatch của App | PASS | Adapter gọi đúng module SS Micro-GA Fast và CP-SAT; truyền seed, thời gian và cấu hình hiện tại. |
| 300/600/900 giây | PASS về truyền tham số | Test mock xác nhận giá trị đến đúng solver; KHÔNG chạy thử đủ 300/600/900 giây. |
| Cùng input/calendar/objective | PASS trên fixture | Hai adapter đọc cùng file JSON, SHA-256 không đổi; objective `MIN_CMAX_FINAL_2C`, output qua validator hiện có. |
| Tái lập | PASS trên fixture nhỏ | Mỗi thuật toán chạy hai lần, seed 42, CP-SAT một worker; lịch và KPI giống nhau. Không suy rộng sang wall-clock runs lớn. |
| CP-SAT proven optimal | PASS sau sửa | Không còn tuyên bố chứng minh tối ưu chỉ vì status OPTIMAL khi tolerance còn cho phép gap. |
| Toàn bộ backend | PASS | **250/250**, 0 fail/error/skip; 69,33 giây. |
| Runner ngoài App tìm thấy | FAIL cho mục tiêu benchmark SS Micro-GA vs CP-SAT | `correct workstation/run_experiments.py` gọi GA cũ và không phân bổ cùng giới hạn thời gian cho GA/CP-SAT. |
| Runner thực sự đang dùng cho luận văn | CHƯA XÁC MINH | Đã hỏi đường dẫn; chưa nhận xác nhận. Không coi runner tìm thấy là chắc chắn runner đang được sử dụng. |
| Benchmark dài hạn, nhiều seed, 180–300+ jobs | CHƯA XÁC MINH trong lượt này | Không chạy theo yêu cầu. |

## Thay đổi tối thiểu

1. `backend/solver_v5/solve_ss_micro_ga_fast_instance.py:331`: hàm ghi convergence dùng cache, không decode thêm hay lấy thêm số ngẫu nhiên. Cuối run luôn ghi trạng thái cuối; cập nhật điểm cùng epoch thay vì tạo bản ghi trùng. Thêm cột `Stop_Reason` (trống ở các điểm trung gian).
2. Cùng file, dòng 581: ghi điểm cuối sau mọi nhánh dừng. Dòng 840/890: tạo summaries trước nhánh không có nghiệm và giữ traces cho nhánh này. Nguyên nhân cũ: ghi theo chu kỳ và kiểm tra deadline/patience trước điểm ghi, nên trace thiếu hoặc cũ hơn summary.
3. `backend/solver_v5/solve_milp_instance.py:745`: `proven_optimal` cần status OPTIMAL và incumbent trùng lower bound. Dòng 766 lưu tolerance đã dùng. Không đổi model, tham số CP-SAT, objective, lịch hay công thức gap.
4. `backend/tests/test_micro_integrity.py`: bổ sung kiểm tra điểm cuối vào 6 ca rescue đã có.
5. Thêm `backend/tests/test_micro_convergence.py`: 10 regression cases.
6. Thêm `backend/tests/test_v5_benchmark_contract.py`: 5 cases về dispatch/config, gap và hai thuật toán trên cùng input.
7. Báo cáo này.

Không thay đổi selection/crossover/mutation/repair, decoder, physical constraints, Working Calendar, horizon, UI, settings của App hoặc dữ liệu lưu.

## Bằng chứng trước/sau sửa

- Convergence trước sửa: **15 FAIL, 1 PASS** trong tập 16 ca mục tiêu. Sau sửa: các ca này PASS.
- Gap trước sửa: **1 FAIL, 4 PASS**. CP-SAT với tolerance 0,9 trả status OPTIMAL, Cmax 139, bound 65, gap 0,532374 nhưng `proven_optimal=true`. Sau sửa trả false khi bound chưa khép kín.
- Toàn bộ nhóm mục tiêu sau sửa: **95/95 PASS**.
- Toàn bộ backend sau sửa: **250/250 PASS**; gồm 235 cases trước lượt này và 15 cases mới, không cộng lần chạy lặp vào tổng số cases.
- Một cảnh báo Starlette/httpx deprecation đã tồn tại; không có test bị skip.

Kiểm thử thật trên fixture V5 **4 jobs, calendar 24 giờ**, budget tối đa 5 giây mỗi solver, seed 42:

| Chỉ tiêu | CP-SAT | SS Micro-GA Fast |
|---|---:|---:|
| Cmax | 104 | 121 |
| Lower bound | 104 | Không có chứng nhận riêng |
| CP gap ratio | 0 | Không áp dụng |
| GA gap so với optimum đã chứng minh của cùng fixture | — | 16,34615385% |

GA dừng `generation_limit`; đây là smoke test 7 epochs, không phải đánh giá chất lượng thuật toán. Hai lần lặp cho cùng lịch/KPI. Các test Working Calendar 60 ngày hiện có cũng PASS trong full backend; không thay fixture nhỏ thành dữ liệu sản xuất.

## Runner và ngân sách: những việc còn lại

- `backend/tests/benchmark_micro_stopping.py` chỉ so sánh cấu hình early-stop Micro-GA. Không có CP-SAT nên không phải runner so sánh hai thuật toán. Output dùng tên cố định có thể ghi đè nếu chạy lại; lượt này không chạy helper đó.
- `D:/back up giga/thesis/flowlien dataset/correct workstation/run_experiments.py:21` import `solve_ga_instance`, **không phải** SS Micro-GA Fast. Dòng 22 import CP-SAT từ bản file bên ngoài App. Các bản solver bên ngoài khác hash bản App hiện tại.
- Runner này truyền cùng `inst_file` cho hai solver khi chạy mới, nhưng không xác nhận hash nội dung; `Source_SHA256` chỉ lấy từ index. `skip_existing` có thể dùng output cũ mà không xác thực input/config/version.
- GA dùng population/generations/runs theo profile, không có cùng wall-clock limit với CP-SAT. Nhiều attempts/workers không được tính thành cùng tổng compute budget. Không được gọi đây là equal-budget comparison.
- Runner đã phân biệt chênh lệch so với incumbent và gap so với optimum theo `proven_optimal`, nhưng bản solver bên ngoài chưa nhận bản sửa nhãn trong App.
- `run_al13_18jobs.ps1` là replay/search riêng cho 18 jobs, không chạy CP-SAT và tự ghi cảnh báo rằng không tái hiện budget 120 giây của App.
- CLI hiện có của Micro-GA trong App chưa expose các tham số time/patience của Python API. Defaults CLI cũng khác App ở số attempts. Không suy ra chạy CLI mặc định tương đương chạy App.

Trước benchmark chính thức cần xác định runner dùng thật và cập nhật có kiểm soát: gọi đúng module App đã kiểm thử; một frozen input/hash cho hai thuật toán; lưu source/config/package versions; thư mục output mới cho mỗi experiment; không tái dùng kết quả chỉ dựa trên tên file. Không chỉnh sửa hoặc tự chạy các runner bên ngoài trong lượt này.

Ngân sách cần được công bố rõ:

- Micro-GA có ngân sách thời gian chung qua các attempts, gồm seed refinement; `max_decodes` là mỗi attempt và không bao gồm mọi decode của seed refinement/final validation. Search có thể dừng sớm bởi epochs, decode budget hoặc patience.
- CP-SAT giới hạn thời gian trong `Solve()`, không bao gồm toàn bộ model construction và xuất/validate. Cùng con số giây không bảo đảm cùng tổng thời gian end-to-end.
- Benchmark cần ghi cả search budget, actual elapsed, attempts, evaluations, workers và stop reason. Giữ nguyên thuật toán, nhưng chốt protocol so sánh trước khi chạy. Multi-worker CP-SAT và wall-clock stopping không bảo đảm tái lập tuyệt đối dù cùng seed.

## Cách hiểu gap

- CP-SAT: `(U − L) / U` là ratio, với U là feasible incumbent, L là lower bound. Muốn phần trăm nhân 100.
- GA so với proven optimum của **cùng input/calendar/objective**: `100 × (GA − C*) / C*`.
- Nếu CP-SAT chưa chứng minh optimum, chỉ gọi là chênh lệch với incumbent, không gọi optimality gap. Nếu thiếu feasible result hoặc bound/reference hợp lệ, để trống; không thay bằng 0.
- Nhãn `status_name` của OR-Tools được giữ nguyên; benchmark phải dùng chứng nhận `proven_optimal` cùng bound/gap, không suy luận từ chuỗi OPTIMAL riêng lẻ.

## Evidence và bảo toàn dữ liệu

Artifacts: `.tmp-qa/2026-11-07-convergence/`.

- [Trước sửa convergence](.tmp-qa/2026-11-07-convergence/before.xml)
- [Trước sửa gap](.tmp-qa/2026-11-07-convergence/contract-before.xml)
- [Nhóm mục tiêu sau sửa](.tmp-qa/2026-11-07-convergence/target-final.xml)
- [Full backend](.tmp-qa/2026-11-07-convergence/backend-final.xml), [log](.tmp-qa/2026-11-07-convergence/backend-final.log)
- [Kết quả fixture cùng input](.tmp-qa/2026-11-07-convergence/backend-final-temp/test_same_frozen_input_feasibi0/comparison.json)
- [Hash verification](.tmp-qa/2026-11-07-convergence/verification.json)

Baseline 259 file: chỉ 2 production files và 1 test file đã có thay đổi; 256 file còn lại giữ nguyên hash, gồm database/storage, decoder và các bản sửa incumbent/permutation trước đó. Hai test files mới được thêm như liệt kê trên. Các file external đã đọc giữ nguyên hash. Không ghi đè benchmark cũ. Tests dùng `SCHEDULING_DATA_DIR` và `tmp_path` riêng.

**Kết luận:** App đã qua regression cho các thay đổi logging/optimality nêu trên, sẵn sàng cho bước xác nhận protocol và smoke-test runner đúng phiên bản. **Chưa khuyến nghị benchmark luận văn chính thức bằng runner ngoài App hiện tìm thấy** cho đến khi xử lý các điểm runner/ngân sách/provenance ở trên. Không tuyên bố bản vá này chứng minh tính ổn định dài hạn hoặc chất lượng nghiệm trên mọi quy mô.
