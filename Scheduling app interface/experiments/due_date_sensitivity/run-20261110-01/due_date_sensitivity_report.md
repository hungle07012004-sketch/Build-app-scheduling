# Due Date Extension – Simulation & Impact Analysis

TEST ONLY. Không chạy solver. Không thay đổi production.
## A. Current System Audit

- Workbook upload đang dùng: xem `manifest.json` (dataset, Excel, frozen input và hash từng run). V5 `backend/services/v5_service.py:28` đọc workbook, `backend/solver_v5/generate_benchmark_instances.py:301` đọc `Due_DateTime`, dòng 318 đổi sang phút kể từ `origin_datetime`, dòng 320 giữ timestamp gốc. Không đổi timezone hay chuẩn hóa về đầu/cuối ngày.
- `backend/services/input_service.py:93` dựng timestamp origin + minutes, dòng 97 truyền `dueDate` tới frontend. `src/pages/JobsPage.tsx:148` và `src/pages/ScheduleFlow.tsx:83` hiện chỉ ngày qua `split(' ')[0]`; backend vẫn giữ giờ/phút/giây.
- Frozen instance lưu `due_min` và `due_datetime`; request/run, algorithm results và selected result được lưu bởi `backend/services/optimization_service.py:61`, `:140`, `:146`. Mỗi run dùng đúng frozen input của run đó, không lấy due date hiện tại thay cho lịch sử.
- V5 `backend/services/v5_service.py:186` tính `sum(max(0,Cj-dj))`; dòng 188 đếm `Cj>dj+1e-6`. Legacy `backend/services/result_standardizer.py:38`/`:44` dùng cùng công thức với ngưỡng 1e-9. Completion V5 là lần hoàn tất batch 2C cuối cùng có job đóng góp; không nhầm một job có nhiều batch với nhiều jobs.
- V5 GA: `backend/solver_v5/benchmark_common.py:686` chỉ Cmax; Micro-GA gọi fitness chung (`solve_ss_micro_ga_fast_instance.py:70`, `:839`) và khởi tạo qua base GA (`:269`). Base GA có seed EDD ở `solve_improved_ga_instance.py:201`.
- V5 MLPSO: seed EDD tại `backend/solver_v5/solve_mlpso_v3_instance.py:359`, objective Cmax tại `:1223`. Due date tác động diagnostic và có thể tác động đường khởi tạo nếu thay đổi tương đối giữa jobs; không là hard deadline. Gia hạn đồng đều theo phút giữ nguyên thứ tự EDD, nhưng lượt này không chạy lại để khẳng định kết quả search.
- V5 CP-SAT: `backend/solver_v5/solve_milp_instance.py:580` có `model.Minimize(cmax)`; `:684`/`:685` dùng due cho KPI. Không có hard deadline theo due trong model hiện đọc. Due có thể tác động artifact warm-start từ heuristic bên ngoài, không đổi objective Cmax.
- Legacy Improved GA: `code/solve_improved_ga_ig_v3_earlystop_instance.py:230`, `:1484` objective lexicographic Cmax → tardiness → flow. Due có thể tác động seed EDD trong `code/solve_ga_instance.py:185` và tie-break fitness. Legacy MLPSO chỉ Cmax (`code/solve_mlpso_instance.py:843`); legacy MILP chỉ Cmax (`code/solve_milp_instance.py:298`). Không đồng nhất mọi solver thành cùng objective legacy.
- Calendar dùng đúng `resource_windows`/`station_windows` của frozen run. Không regenerate calendar, không apply lại policy hiện tại. Các lịch lịch sử khác input/calendar không được coi là benchmark so sánh thuật toán công bằng.
- Thời gian sản xuất là factory-local naive theo `backend/services/instance_service.py:9`; diễn giải Asia/Ho_Chi_Minh (UTC+07) theo ngữ cảnh App, không chuyển UTC. `created_at` của DB có thể là UTC nhưng không dùng để tính tardiness. Một ngày gia hạn = 1440 phút = 86400 giây, gồm cả cuối tuần; không phải ngày làm việc. Mọi timestamp xuất giữ chính xác, không làm tròn đầu/cuối ngày.
- Phân tích KPI sau lịch cố định không chứng minh toàn bộ physical feasibility lại. Chỉ nhận các kết quả App đánh dấu feasible và kiểm tra mapping, hash, thời gian và KPI. Workbook provenance hiện ghi nguồn/quy tắc gán due date chưa có bằng chứng đầy đủ; không coi due date là cam kết nhà máy đã xác minh.
## B. Baseline Validation
Snapshot: `saved_schedules_snapshot.sqlite3`; SHA-256 `553112d0195564c3ecf09b273bb437efa2416cd7c5a0a5fcb27f4e4bd2097344`. 53 runs; 51 feasible solver schedules kiểm tra; 4 entries không có lịch khả thi được loại, không gán KPI 0.
Đối chiếu từng run/solver trong `baseline_validation.csv`: Cmax, tardy jobs, total tardiness và flow; mapping request/frozen jobs, completion, timestamp-minute consistency, input hash, duplicate events/jobs/contributors. Không tự điền dữ liệu thiếu.
**PASS:** tất cả baseline khớp App trong tolerance 1e-5 phút; count khớp chính xác.
## C. Scenario Comparison
KPI đầy đủ ở `due_date_sensitivity_results.csv`: tổng jobs, on-time, tardy, OTD, tổng/trung bình/max tardiness và Cmax. Average-all chia tất cả jobs; average-tardy chỉ chia jobs trễ theo epsilon của App, để trống khi không có job trễ. Không làm tròn trong CSV; bảng dưới chỉ định dạng để đọc.
| Run ID | Solver | Jobs | Baseline | +1d | +3d | +5d | +7d | OTD baseline → +7d | Cmax phút |
|---|---|---:|---:|---:|---:|---:|---:|---|---:|
| 3cf4cba9e5024ff789e8664bfb0db9c5 | MILP | 10 | 0 | 0 | 0 | 0 | 0 | 100.00% → 100.00% | 1734.910000 |
| 3f1bf327f69c4efc99f714abb4344eaa | MILP | 12 | 0 | 0 | 0 | 0 | 0 | 100.00% → 100.00% | 1734.910000 |
| 624fbda493f04fa4ab612f8096db3d79 | MILP | 12 | 0 | 0 | 0 | 0 | 0 | 100.00% → 100.00% | 1734.910000 |
| 6e4a706a002c4a94b36cef94ca461324 | Improved GA | 136 | 14 | 0 | 0 | 0 | 0 | 89.71% → 100.00% | 14784.140000 |
| 6e4a706a002c4a94b36cef94ca461324 | MLPSO | 136 | 22 | 5 | 1 | 1 | 0 | 83.82% → 100.00% | 14784.140000 |
| bc091d20a1a74f46ae606ee1156d4977 | Improved GA | 136 | 14 | 0 | 0 | 0 | 0 | 89.71% → 100.00% | 14784.140000 |
| bc091d20a1a74f46ae606ee1156d4977 | MLPSO | 136 | 22 | 5 | 1 | 1 | 0 | 83.82% → 100.00% | 14784.140000 |
| 8f661d4db87e42f58b2f80cc294192cc | MILP | 4 | 0 | 0 | 0 | 0 | 0 | 100.00% → 100.00% | 755.550000 |
| bb2d7779819c49bdb448c096b71e966d | CP-SAT | 9 | 1 | 0 | 0 | 0 | 0 | 88.89% → 100.00% | 4801.842000 |
| 39ab6409b8534d68b36b9f226b6a4a36 | CP-SAT | 6 | 0 | 0 | 0 | 0 | 0 | 100.00% → 100.00% | 3955.620000 |
| 0e56b8416af54edb82e4124a99044374 | CP-SAT | 4 | 0 | 0 | 0 | 0 | 0 | 100.00% → 100.00% | 2463.796000 |
| 4eb483040abd49dba2a894a4f862dfc2 | CP-SAT | 4 | 0 | 0 | 0 | 0 | 0 | 100.00% → 100.00% | 2463.796000 |
| dde50d813ab9458ca06a1bf461cb6389 | CP-SAT | 9 | 0 | 0 | 0 | 0 | 0 | 100.00% → 100.00% | 4805.338000 |
| b4995c73b5cf4a6da8f21743060e57f5 | CP-SAT | 10 | 1 | 0 | 0 | 0 | 0 | 90.00% → 100.00% | 5119.500000 |
| 6891c237935945d287bb5e61c01c1547 | SS Micro-GA Fast | 10 | 0 | 0 | 0 | 0 | 0 | 100.00% → 100.00% | 5285.338000 |
| 9c3a933a5204428c96b97f592e66a46e | SS Micro-GA Fast | 10 | 0 | 0 | 0 | 0 | 0 | 100.00% → 100.00% | 5285.338000 |
| 93e3d5cd15514a379fe252b93debac72 | SS Micro-GA Fast | 180 | 148 | 143 | 134 | 124 | 116 | 17.78% → 35.56% | 64020.396000 |
| 8af4452495fb41a19ee8d5a4b3e2f261 | SS Micro-GA Fast | 10 | 0 | 0 | 0 | 0 | 0 | 100.00% → 100.00% | 5285.338000 |
| 3d86640c8d6c449181679c74d1f5994e | SS Micro-GA Fast | 20 | 2 | 1 | 0 | 0 | 0 | 90.00% → 100.00% | 8340.446000 |
| a3a0d47c8daa4e12bcc195905801d0ac | SS Micro-GA Fast | 17 | 1 | 0 | 0 | 0 | 0 | 94.12% → 100.00% | 7814.117000 |
| 5f41d17437ac479c9d59f4d7a9b5cb91 | SS Micro-GA Fast | 180 | 148 | 143 | 134 | 124 | 116 | 17.78% → 35.56% | 64020.396000 |
| f791aaa9ed014b79a6be904cb9599941 | SS Micro-GA Fast | 180 | 143 | 140 | 133 | 121 | 114 | 20.56% → 36.67% | 63163.500000 |
| 7a662d8469c24c1eb8a83e780f770e3a | SS Micro-GA Fast | 180 | 149 | 143 | 134 | 126 | 118 | 17.22% → 34.44% | 64690.110000 |
| f464958c1a07470c9a44599988b26b6e | SS Micro-GA Fast | 10 | 0 | 0 | 0 | 0 | 0 | 100.00% → 100.00% | 5285.338000 |
| 66e4ed58f36b4ffebdbfb11eb55a1382 | SS Micro-GA Fast | 9 | 1 | 1 | 0 | 0 | 0 | 88.89% → 100.00% | 6784.628000 |
| 47bf22ba007c49a6ac96be65d0fe8787 | SS Micro-GA Fast | 11 | 2 | 1 | 0 | 0 | 0 | 81.82% → 100.00% | 7264.628000 |
| 52df5e110f614b159931f7b02c80139d | SS Micro-GA Fast | 18 | 3 | 1 | 0 | 0 | 0 | 83.33% → 100.00% | 9354.670000 |
| 6d16cd92d84d4acd8f08aac1a9e26d0b | SS Micro-GA Fast | 168 | 148 | 144 | 128 | 110 | 101 | 11.90% → 39.88% | 71037.180000 |
| 9fb4e64c47d94cc48c8dd82a40d7cd15 | SS Micro-GA Fast | 168 | 148 | 144 | 128 | 110 | 101 | 11.90% → 39.88% | 71037.180000 |
| 3bcbb6a3d5ca4cec8e746bc087b1984d | SS Micro-GA Fast | 168 | 148 | 144 | 128 | 110 | 101 | 11.90% → 39.88% | 71024.689000 |
| 6a49560570234bfdb2c36f55e49c5265 | SS Micro-GA Fast | 168 | 129 | 118 | 105 | 92 | 75 | 23.21% → 55.36% | 67817.941000 |
| 3dc65aeb040d42d7b9a07f115a00b888 | SS Micro-GA Fast | 180 | 142 | 133 | 119 | 112 | 107 | 21.11% → 40.56% | 68434.500000 |
| b13bc76503bb49f481ee9f2b6a278a8b | SS Micro-GA Fast | 180 | 142 | 133 | 119 | 112 | 107 | 21.11% → 40.56% | 68355.027000 |
| dd9e8a1471ae47f4aefb2980b686bb50 | SS Micro-GA Fast | 163 | 142 | 136 | 117 | 101 | 88 | 12.88% → 46.01% | 67606.714000 |
| 3495dcebb04b4cddb77dd6c8182e6fde | SS Micro-GA Fast | 168 | 148 | 144 | 128 | 110 | 101 | 11.90% → 39.88% | 71042.584000 |
| 873ff366301449b6834a057cd95f8077 | SS Micro-GA Fast | 11 | 2 | 1 | 0 | 0 | 0 | 81.82% → 100.00% | 7264.628000 |
| ff76a891ba5e4697b6b623e5f5479a26 | SS Micro-GA Fast | 6 | 0 | 0 | 0 | 0 | 0 | 100.00% → 100.00% | 3859.652000 |
| 5e0398c1ac814b478f85b1c6ae02f9bf | SS Micro-GA Fast | 12 | 3 | 0 | 0 | 0 | 0 | 75.00% → 100.00% | 7582.500000 |
| 821e545e1aa3440b9646255e24c8223f | SS Micro-GA Fast | 30 | 9 | 3 | 0 | 0 | 0 | 70.00% → 100.00% | 22506.670000 |
| eb3b3b95ecc044298623307c415a8d9d | SS Micro-GA Fast | 20 | 5 | 2 | 1 | 0 | 0 | 75.00% → 100.00% | 10092.061000 |
| 941fd62af4014379a5d6e393ed804f10 | SS Micro-GA Fast | 156 | 132 | 120 | 108 | 94 | 78 | 15.38% → 50.00% | 63453.326000 |
| b0073cbbc54d467d9df25a278e7acf5d | SS Micro-GA Fast | 24 | 10 | 6 | 1 | 0 | 0 | 58.33% → 100.00% | 11264.956000 |
| 7b97a2b8f5874d6d849ff7e56e7abbed | SS Micro-GA Fast | 20 | 5 | 2 | 1 | 0 | 0 | 75.00% → 100.00% | 10092.061000 |
| d609b6ca42974afa8e446ee3b914159d | SS Micro-GA Fast | 20 | 5 | 2 | 1 | 0 | 0 | 75.00% → 100.00% | 10092.061000 |
| 3c86a353b41443bfae949583a96b2f20 | SS Micro-GA Fast | 20 | 5 | 2 | 1 | 0 | 0 | 75.00% → 100.00% | 10092.061000 |
| eca167338094498dabd9985e3282e1cc | SS Micro-GA Fast | 9 | 1 | 1 | 0 | 0 | 0 | 88.89% → 100.00% | 6784.628000 |
| 4013da12e01449adbbae8eae8df7abc3 | SS Micro-GA Fast | 16 | 3 | 2 | 0 | 0 | 0 | 81.25% → 100.00% | 8650.950000 |
| ddcc6e11ae154fbf941b8fb24fa1f549 | SS Micro-GA Fast | 14 | 2 | 1 | 0 | 0 | 0 | 85.71% → 100.00% | 8207.762000 |
| 236e9e6fbb1044fab37acb6064f8b4b2 | SS Micro-GA Fast | 166 | 130 | 121 | 103 | 91 | 86 | 21.69% → 48.19% | 66679.265000 |
| 182741f9f6874dcfb8a7debff0b834c5 | SS Micro-GA Fast | 20 | 5 | 2 | 1 | 0 | 0 | 75.00% → 100.00% | 10092.061000 |
| ae171b2deebb4768823073b51cc3b227 | MLPSO | 12 | 3 | 0 | 0 | 0 | 0 | 75.00% → 100.00% | 7582.500000 |

## D. Job-Level Analysis
Các ID dưới đây là **mới chuyển sang đúng hạn so với scenario ngắn hơn trước đó**. CSV đánh dấu cả recovered_vs_baseline; không nhầm hai cách đếm.
- `3cf4cba9e5024ff789e8664bfb0db9c5` / MILP / +1d: mới 0 [—]; tổng hồi phục so baseline 0.
- `3cf4cba9e5024ff789e8664bfb0db9c5` / MILP / +3d: mới 0 [—]; tổng hồi phục so baseline 0.
- `3cf4cba9e5024ff789e8664bfb0db9c5` / MILP / +5d: mới 0 [—]; tổng hồi phục so baseline 0.
- `3cf4cba9e5024ff789e8664bfb0db9c5` / MILP / +7d: mới 0 [—]; tổng hồi phục so baseline 0.
- `3f1bf327f69c4efc99f714abb4344eaa` / MILP / +1d: mới 0 [—]; tổng hồi phục so baseline 0.
- `3f1bf327f69c4efc99f714abb4344eaa` / MILP / +3d: mới 0 [—]; tổng hồi phục so baseline 0.
- `3f1bf327f69c4efc99f714abb4344eaa` / MILP / +5d: mới 0 [—]; tổng hồi phục so baseline 0.
- `3f1bf327f69c4efc99f714abb4344eaa` / MILP / +7d: mới 0 [—]; tổng hồi phục so baseline 0.
- `624fbda493f04fa4ab612f8096db3d79` / MILP / +1d: mới 0 [—]; tổng hồi phục so baseline 0.
- `624fbda493f04fa4ab612f8096db3d79` / MILP / +3d: mới 0 [—]; tổng hồi phục so baseline 0.
- `624fbda493f04fa4ab612f8096db3d79` / MILP / +5d: mới 0 [—]; tổng hồi phục so baseline 0.
- `624fbda493f04fa4ab612f8096db3d79` / MILP / +7d: mới 0 [—]; tổng hồi phục so baseline 0.
- `6e4a706a002c4a94b36cef94ca461324` / Improved GA / +1d: mới 14 [J020, J029, J064, J065, J066, J067, J068, J069, J070, J072, J113, J114, J115, J116]; tổng hồi phục so baseline 14.
- `6e4a706a002c4a94b36cef94ca461324` / Improved GA / +3d: mới 0 [—]; tổng hồi phục so baseline 14.
- `6e4a706a002c4a94b36cef94ca461324` / Improved GA / +5d: mới 0 [—]; tổng hồi phục so baseline 14.
- `6e4a706a002c4a94b36cef94ca461324` / Improved GA / +7d: mới 0 [—]; tổng hồi phục so baseline 14.
- `6e4a706a002c4a94b36cef94ca461324` / MLPSO / +1d: mới 17 [J020, J022, J040, J062, J064, J065, J066, J067, J068, J070, J078, J079, J114, J115, J116, J118, J124]; tổng hồi phục so baseline 17.
- `6e4a706a002c4a94b36cef94ca461324` / MLPSO / +3d: mới 4 [J030, J031, J032, J097]; tổng hồi phục so baseline 21.
- `6e4a706a002c4a94b36cef94ca461324` / MLPSO / +5d: mới 0 [—]; tổng hồi phục so baseline 21.
- `6e4a706a002c4a94b36cef94ca461324` / MLPSO / +7d: mới 1 [J113]; tổng hồi phục so baseline 22.
- `bc091d20a1a74f46ae606ee1156d4977` / Improved GA / +1d: mới 14 [J020, J029, J064, J065, J066, J067, J068, J069, J070, J072, J113, J114, J115, J116]; tổng hồi phục so baseline 14.
- `bc091d20a1a74f46ae606ee1156d4977` / Improved GA / +3d: mới 0 [—]; tổng hồi phục so baseline 14.
- `bc091d20a1a74f46ae606ee1156d4977` / Improved GA / +5d: mới 0 [—]; tổng hồi phục so baseline 14.
- `bc091d20a1a74f46ae606ee1156d4977` / Improved GA / +7d: mới 0 [—]; tổng hồi phục so baseline 14.
- `bc091d20a1a74f46ae606ee1156d4977` / MLPSO / +1d: mới 17 [J020, J022, J040, J062, J064, J065, J066, J067, J068, J070, J078, J079, J114, J115, J116, J118, J124]; tổng hồi phục so baseline 17.
- `bc091d20a1a74f46ae606ee1156d4977` / MLPSO / +3d: mới 4 [J030, J031, J032, J097]; tổng hồi phục so baseline 21.
- `bc091d20a1a74f46ae606ee1156d4977` / MLPSO / +5d: mới 0 [—]; tổng hồi phục so baseline 21.
- `bc091d20a1a74f46ae606ee1156d4977` / MLPSO / +7d: mới 1 [J113]; tổng hồi phục so baseline 22.
- `8f661d4db87e42f58b2f80cc294192cc` / MILP / +1d: mới 0 [—]; tổng hồi phục so baseline 0.
- `8f661d4db87e42f58b2f80cc294192cc` / MILP / +3d: mới 0 [—]; tổng hồi phục so baseline 0.
- `8f661d4db87e42f58b2f80cc294192cc` / MILP / +5d: mới 0 [—]; tổng hồi phục so baseline 0.
- `8f661d4db87e42f58b2f80cc294192cc` / MILP / +7d: mới 0 [—]; tổng hồi phục so baseline 0.
- `bb2d7779819c49bdb448c096b71e966d` / CP-SAT / +1d: mới 1 [J002]; tổng hồi phục so baseline 1.
- `bb2d7779819c49bdb448c096b71e966d` / CP-SAT / +3d: mới 0 [—]; tổng hồi phục so baseline 1.
- `bb2d7779819c49bdb448c096b71e966d` / CP-SAT / +5d: mới 0 [—]; tổng hồi phục so baseline 1.
- `bb2d7779819c49bdb448c096b71e966d` / CP-SAT / +7d: mới 0 [—]; tổng hồi phục so baseline 1.
- `39ab6409b8534d68b36b9f226b6a4a36` / CP-SAT / +1d: mới 0 [—]; tổng hồi phục so baseline 0.
- `39ab6409b8534d68b36b9f226b6a4a36` / CP-SAT / +3d: mới 0 [—]; tổng hồi phục so baseline 0.
- `39ab6409b8534d68b36b9f226b6a4a36` / CP-SAT / +5d: mới 0 [—]; tổng hồi phục so baseline 0.
- `39ab6409b8534d68b36b9f226b6a4a36` / CP-SAT / +7d: mới 0 [—]; tổng hồi phục so baseline 0.
- `0e56b8416af54edb82e4124a99044374` / CP-SAT / +1d: mới 0 [—]; tổng hồi phục so baseline 0.
- `0e56b8416af54edb82e4124a99044374` / CP-SAT / +3d: mới 0 [—]; tổng hồi phục so baseline 0.
- `0e56b8416af54edb82e4124a99044374` / CP-SAT / +5d: mới 0 [—]; tổng hồi phục so baseline 0.
- `0e56b8416af54edb82e4124a99044374` / CP-SAT / +7d: mới 0 [—]; tổng hồi phục so baseline 0.
- `4eb483040abd49dba2a894a4f862dfc2` / CP-SAT / +1d: mới 0 [—]; tổng hồi phục so baseline 0.
- `4eb483040abd49dba2a894a4f862dfc2` / CP-SAT / +3d: mới 0 [—]; tổng hồi phục so baseline 0.
- `4eb483040abd49dba2a894a4f862dfc2` / CP-SAT / +5d: mới 0 [—]; tổng hồi phục so baseline 0.
- `4eb483040abd49dba2a894a4f862dfc2` / CP-SAT / +7d: mới 0 [—]; tổng hồi phục so baseline 0.
- `dde50d813ab9458ca06a1bf461cb6389` / CP-SAT / +1d: mới 0 [—]; tổng hồi phục so baseline 0.
- `dde50d813ab9458ca06a1bf461cb6389` / CP-SAT / +3d: mới 0 [—]; tổng hồi phục so baseline 0.
- `dde50d813ab9458ca06a1bf461cb6389` / CP-SAT / +5d: mới 0 [—]; tổng hồi phục so baseline 0.
- `dde50d813ab9458ca06a1bf461cb6389` / CP-SAT / +7d: mới 0 [—]; tổng hồi phục so baseline 0.
- `b4995c73b5cf4a6da8f21743060e57f5` / CP-SAT / +1d: mới 1 [J005]; tổng hồi phục so baseline 1.
- `b4995c73b5cf4a6da8f21743060e57f5` / CP-SAT / +3d: mới 0 [—]; tổng hồi phục so baseline 1.
- `b4995c73b5cf4a6da8f21743060e57f5` / CP-SAT / +5d: mới 0 [—]; tổng hồi phục so baseline 1.
- `b4995c73b5cf4a6da8f21743060e57f5` / CP-SAT / +7d: mới 0 [—]; tổng hồi phục so baseline 1.
- `6891c237935945d287bb5e61c01c1547` / SS Micro-GA Fast / +1d: mới 0 [—]; tổng hồi phục so baseline 0.
- `6891c237935945d287bb5e61c01c1547` / SS Micro-GA Fast / +3d: mới 0 [—]; tổng hồi phục so baseline 0.
- `6891c237935945d287bb5e61c01c1547` / SS Micro-GA Fast / +5d: mới 0 [—]; tổng hồi phục so baseline 0.
- `6891c237935945d287bb5e61c01c1547` / SS Micro-GA Fast / +7d: mới 0 [—]; tổng hồi phục so baseline 0.
- `9c3a933a5204428c96b97f592e66a46e` / SS Micro-GA Fast / +1d: mới 0 [—]; tổng hồi phục so baseline 0.
- `9c3a933a5204428c96b97f592e66a46e` / SS Micro-GA Fast / +3d: mới 0 [—]; tổng hồi phục so baseline 0.
- `9c3a933a5204428c96b97f592e66a46e` / SS Micro-GA Fast / +5d: mới 0 [—]; tổng hồi phục so baseline 0.
- `9c3a933a5204428c96b97f592e66a46e` / SS Micro-GA Fast / +7d: mới 0 [—]; tổng hồi phục so baseline 0.
- `93e3d5cd15514a379fe252b93debac72` / SS Micro-GA Fast / +1d: mới 5 [J031, J047, J074, J145, J156]; tổng hồi phục so baseline 5.
- `93e3d5cd15514a379fe252b93debac72` / SS Micro-GA Fast / +3d: mới 9 [J021, J027, J039, J079, J087, J088, J107, J133, J161]; tổng hồi phục so baseline 14.
- `93e3d5cd15514a379fe252b93debac72` / SS Micro-GA Fast / +5d: mới 10 [J020, J026, J051, J068, J075, J104, J106, J135, J136, J155]; tổng hồi phục so baseline 24.
- `93e3d5cd15514a379fe252b93debac72` / SS Micro-GA Fast / +7d: mới 8 [J012, J016, J019, J037, J090, J093, J103, J127]; tổng hồi phục so baseline 32.
- `8af4452495fb41a19ee8d5a4b3e2f261` / SS Micro-GA Fast / +1d: mới 0 [—]; tổng hồi phục so baseline 0.
- `8af4452495fb41a19ee8d5a4b3e2f261` / SS Micro-GA Fast / +3d: mới 0 [—]; tổng hồi phục so baseline 0.
- `8af4452495fb41a19ee8d5a4b3e2f261` / SS Micro-GA Fast / +5d: mới 0 [—]; tổng hồi phục so baseline 0.
- `8af4452495fb41a19ee8d5a4b3e2f261` / SS Micro-GA Fast / +7d: mới 0 [—]; tổng hồi phục so baseline 0.
- `3d86640c8d6c449181679c74d1f5994e` / SS Micro-GA Fast / +1d: mới 1 [J013]; tổng hồi phục so baseline 1.
- `3d86640c8d6c449181679c74d1f5994e` / SS Micro-GA Fast / +3d: mới 1 [J005]; tổng hồi phục so baseline 2.
- `3d86640c8d6c449181679c74d1f5994e` / SS Micro-GA Fast / +5d: mới 0 [—]; tổng hồi phục so baseline 2.
- `3d86640c8d6c449181679c74d1f5994e` / SS Micro-GA Fast / +7d: mới 0 [—]; tổng hồi phục so baseline 2.
- `a3a0d47c8daa4e12bcc195905801d0ac` / SS Micro-GA Fast / +1d: mới 1 [J013]; tổng hồi phục so baseline 1.
- `a3a0d47c8daa4e12bcc195905801d0ac` / SS Micro-GA Fast / +3d: mới 0 [—]; tổng hồi phục so baseline 1.
- `a3a0d47c8daa4e12bcc195905801d0ac` / SS Micro-GA Fast / +5d: mới 0 [—]; tổng hồi phục so baseline 1.
- `a3a0d47c8daa4e12bcc195905801d0ac` / SS Micro-GA Fast / +7d: mới 0 [—]; tổng hồi phục so baseline 1.
- `5f41d17437ac479c9d59f4d7a9b5cb91` / SS Micro-GA Fast / +1d: mới 5 [J031, J047, J074, J145, J156]; tổng hồi phục so baseline 5.
- `5f41d17437ac479c9d59f4d7a9b5cb91` / SS Micro-GA Fast / +3d: mới 9 [J021, J027, J039, J079, J087, J088, J107, J133, J161]; tổng hồi phục so baseline 14.
- `5f41d17437ac479c9d59f4d7a9b5cb91` / SS Micro-GA Fast / +5d: mới 10 [J020, J026, J051, J068, J075, J104, J106, J135, J136, J155]; tổng hồi phục so baseline 24.
- `5f41d17437ac479c9d59f4d7a9b5cb91` / SS Micro-GA Fast / +7d: mới 8 [J012, J016, J019, J037, J090, J093, J103, J127]; tổng hồi phục so baseline 32.
- `f791aaa9ed014b79a6be904cb9599941` / SS Micro-GA Fast / +1d: mới 3 [J031, J047, J088]; tổng hồi phục so baseline 3.
- `f791aaa9ed014b79a6be904cb9599941` / SS Micro-GA Fast / +3d: mới 7 [J027, J039, J079, J087, J107, J133, J161]; tổng hồi phục so baseline 10.
- `f791aaa9ed014b79a6be904cb9599941` / SS Micro-GA Fast / +5d: mới 12 [J020, J026, J051, J068, J075, J103, J104, J106, J127, J135, J136, J155]; tổng hồi phục so baseline 22.
- `f791aaa9ed014b79a6be904cb9599941` / SS Micro-GA Fast / +7d: mới 7 [J012, J016, J019, J037, J082, J090, J165]; tổng hồi phục so baseline 29.
- `7a662d8469c24c1eb8a83e780f770e3a` / SS Micro-GA Fast / +1d: mới 6 [J027, J031, J047, J074, J145, J156]; tổng hồi phục so baseline 6.
- `7a662d8469c24c1eb8a83e780f770e3a` / SS Micro-GA Fast / +3d: mới 9 [J021, J048, J073, J079, J087, J088, J107, J133, J161]; tổng hồi phục so baseline 15.
- `7a662d8469c24c1eb8a83e780f770e3a` / SS Micro-GA Fast / +5d: mới 8 [J020, J029, J051, J068, J075, J106, J135, J155]; tổng hồi phục so baseline 23.
- `7a662d8469c24c1eb8a83e780f770e3a` / SS Micro-GA Fast / +7d: mới 8 [J012, J019, J090, J093, J103, J104, J127, J136]; tổng hồi phục so baseline 31.
- `f464958c1a07470c9a44599988b26b6e` / SS Micro-GA Fast / +1d: mới 0 [—]; tổng hồi phục so baseline 0.
- `f464958c1a07470c9a44599988b26b6e` / SS Micro-GA Fast / +3d: mới 0 [—]; tổng hồi phục so baseline 0.
- `f464958c1a07470c9a44599988b26b6e` / SS Micro-GA Fast / +5d: mới 0 [—]; tổng hồi phục so baseline 0.
- `f464958c1a07470c9a44599988b26b6e` / SS Micro-GA Fast / +7d: mới 0 [—]; tổng hồi phục so baseline 0.
- `66e4ed58f36b4ffebdbfb11eb55a1382` / SS Micro-GA Fast / +1d: mới 0 [—]; tổng hồi phục so baseline 0.
- `66e4ed58f36b4ffebdbfb11eb55a1382` / SS Micro-GA Fast / +3d: mới 1 [J005]; tổng hồi phục so baseline 1.
- `66e4ed58f36b4ffebdbfb11eb55a1382` / SS Micro-GA Fast / +5d: mới 0 [—]; tổng hồi phục so baseline 1.
- `66e4ed58f36b4ffebdbfb11eb55a1382` / SS Micro-GA Fast / +7d: mới 0 [—]; tổng hồi phục so baseline 1.
- `47bf22ba007c49a6ac96be65d0fe8787` / SS Micro-GA Fast / +1d: mới 1 [J009]; tổng hồi phục so baseline 1.
- `47bf22ba007c49a6ac96be65d0fe8787` / SS Micro-GA Fast / +3d: mới 1 [J005]; tổng hồi phục so baseline 2.
- `47bf22ba007c49a6ac96be65d0fe8787` / SS Micro-GA Fast / +5d: mới 0 [—]; tổng hồi phục so baseline 2.
- `47bf22ba007c49a6ac96be65d0fe8787` / SS Micro-GA Fast / +7d: mới 0 [—]; tổng hồi phục so baseline 2.
- `52df5e110f614b159931f7b02c80139d` / SS Micro-GA Fast / +1d: mới 2 [J009, J013]; tổng hồi phục so baseline 2.
- `52df5e110f614b159931f7b02c80139d` / SS Micro-GA Fast / +3d: mới 1 [J005]; tổng hồi phục so baseline 3.
- `52df5e110f614b159931f7b02c80139d` / SS Micro-GA Fast / +5d: mới 0 [—]; tổng hồi phục so baseline 3.
- `52df5e110f614b159931f7b02c80139d` / SS Micro-GA Fast / +7d: mới 0 [—]; tổng hồi phục so baseline 3.
- `6d16cd92d84d4acd8f08aac1a9e26d0b` / SS Micro-GA Fast / +1d: mới 4 [J045, J087, J088, J160]; tổng hồi phục so baseline 4.
- `6d16cd92d84d4acd8f08aac1a9e26d0b` / SS Micro-GA Fast / +3d: mới 16 [J023, J024, J031, J057, J058, J068, J073, J075, J116, J128, J138, J142, J150, J170, J177, J178]; tổng hồi phục so baseline 20.
- `6d16cd92d84d4acd8f08aac1a9e26d0b` / SS Micro-GA Fast / +5d: mới 18 [J013, J026, J036, J037, J047, J048, J051, J052, J097, J098, J111, J117, J125, J141, J147, J152, J157, J158]; tổng hồi phục so baseline 38.
- `6d16cd92d84d4acd8f08aac1a9e26d0b` / SS Micro-GA Fast / +7d: mới 9 [J019, J020, J069, J070, J085, J101, J119, J122, J132]; tổng hồi phục so baseline 47.
- `9fb4e64c47d94cc48c8dd82a40d7cd15` / SS Micro-GA Fast / +1d: mới 4 [J045, J087, J088, J160]; tổng hồi phục so baseline 4.
- `9fb4e64c47d94cc48c8dd82a40d7cd15` / SS Micro-GA Fast / +3d: mới 16 [J023, J024, J031, J057, J058, J068, J073, J075, J116, J128, J138, J142, J150, J170, J177, J178]; tổng hồi phục so baseline 20.
- `9fb4e64c47d94cc48c8dd82a40d7cd15` / SS Micro-GA Fast / +5d: mới 18 [J013, J026, J036, J037, J047, J048, J051, J052, J097, J098, J111, J117, J125, J141, J147, J152, J157, J158]; tổng hồi phục so baseline 38.
- `9fb4e64c47d94cc48c8dd82a40d7cd15` / SS Micro-GA Fast / +7d: mới 9 [J019, J020, J069, J070, J085, J101, J119, J122, J132]; tổng hồi phục so baseline 47.
- `3bcbb6a3d5ca4cec8e746bc087b1984d` / SS Micro-GA Fast / +1d: mới 4 [J045, J087, J088, J160]; tổng hồi phục so baseline 4.
- `3bcbb6a3d5ca4cec8e746bc087b1984d` / SS Micro-GA Fast / +3d: mới 16 [J023, J024, J031, J057, J058, J068, J073, J075, J116, J128, J138, J142, J150, J170, J177, J178]; tổng hồi phục so baseline 20.
- `3bcbb6a3d5ca4cec8e746bc087b1984d` / SS Micro-GA Fast / +5d: mới 18 [J013, J026, J036, J037, J047, J048, J051, J052, J097, J098, J111, J117, J125, J141, J147, J152, J157, J158]; tổng hồi phục so baseline 38.
- `3bcbb6a3d5ca4cec8e746bc087b1984d` / SS Micro-GA Fast / +7d: mới 9 [J019, J020, J069, J070, J085, J101, J119, J122, J132]; tổng hồi phục so baseline 47.
- `6a49560570234bfdb2c36f55e49c5265` / SS Micro-GA Fast / +1d: mới 11 [J018, J021, J031, J036, J060, J087, J139, J150, J159, J170, J177]; tổng hồi phục so baseline 11.
- `6a49560570234bfdb2c36f55e49c5265` / SS Micro-GA Fast / +3d: mới 13 [J025, J065, J073, J116, J117, J128, J138, J141, J142, J152, J157, J158, J173]; tổng hồi phục so baseline 24.
- `6a49560570234bfdb2c36f55e49c5265` / SS Micro-GA Fast / +5d: mới 13 [J024, J038, J086, J089, J097, J098, J111, J125, J146, J149, J162, J168, J176]; tổng hồi phục so baseline 37.
- `6a49560570234bfdb2c36f55e49c5265` / SS Micro-GA Fast / +7d: mới 17 [J014, J035, J050, J069, J070, J078, J085, J101, J102, J119, J122, J132, J134, J147, J151, J156, J161]; tổng hồi phục so baseline 54.
- `3dc65aeb040d42d7b9a07f115a00b888` / SS Micro-GA Fast / +1d: mới 9 [J004, J027, J058, J068, J073, J124, J144, J171, J172]; tổng hồi phục so baseline 9.
- `3dc65aeb040d42d7b9a07f115a00b888` / SS Micro-GA Fast / +3d: mới 14 [J002, J011, J015, J022, J025, J029, J039, J099, J110, J113, J137, J153, J154, J164]; tổng hồi phục so baseline 23.
- `3dc65aeb040d42d7b9a07f115a00b888` / SS Micro-GA Fast / +5d: mới 7 [J021, J080, J084, J096, J109, J112, J120]; tổng hồi phục so baseline 30.
- `3dc65aeb040d42d7b9a07f115a00b888` / SS Micro-GA Fast / +7d: mới 5 [J060, J072, J083, J095, J130]; tổng hồi phục so baseline 35.
- `b13bc76503bb49f481ee9f2b6a278a8b` / SS Micro-GA Fast / +1d: mới 9 [J004, J027, J058, J068, J073, J124, J144, J171, J172]; tổng hồi phục so baseline 9.
- `b13bc76503bb49f481ee9f2b6a278a8b` / SS Micro-GA Fast / +3d: mới 14 [J002, J011, J015, J022, J025, J029, J039, J099, J110, J113, J137, J153, J154, J164]; tổng hồi phục so baseline 23.
- `b13bc76503bb49f481ee9f2b6a278a8b` / SS Micro-GA Fast / +5d: mới 7 [J021, J080, J084, J096, J109, J112, J120]; tổng hồi phục so baseline 30.
- `b13bc76503bb49f481ee9f2b6a278a8b` / SS Micro-GA Fast / +7d: mới 5 [J060, J072, J083, J095, J130]; tổng hồi phục so baseline 35.
- `dd9e8a1471ae47f4aefb2980b686bb50` / SS Micro-GA Fast / +1d: mới 6 [J036, J068, J073, J075, J118, J133]; tổng hồi phục so baseline 6.
- `dd9e8a1471ae47f4aefb2980b686bb50` / SS Micro-GA Fast / +3d: mới 19 [J019, J020, J048, J049, J052, J057, J061, J076, J103, J104, J106, J135, J136, J142, J150, J155, J170, J177, J178]; tổng hồi phục so baseline 25.
- `dd9e8a1471ae47f4aefb2980b686bb50` / SS Micro-GA Fast / +5d: mới 16 [J021, J022, J025, J028, J039, J042, J047, J051, J090, J091, J093, J127, J141, J152, J157, J158]; tổng hồi phục so baseline 41.
- `dd9e8a1471ae47f4aefb2980b686bb50` / SS Micro-GA Fast / +7d: mới 13 [J027, J029, J044, J056, J059, J064, J067, J082, J105, J119, J122, J132, J147]; tổng hồi phục so baseline 54.
- `3495dcebb04b4cddb77dd6c8182e6fde` / SS Micro-GA Fast / +1d: mới 4 [J045, J087, J088, J160]; tổng hồi phục so baseline 4.
- `3495dcebb04b4cddb77dd6c8182e6fde` / SS Micro-GA Fast / +3d: mới 16 [J023, J024, J031, J057, J058, J068, J073, J075, J116, J128, J138, J142, J150, J170, J177, J178]; tổng hồi phục so baseline 20.
- `3495dcebb04b4cddb77dd6c8182e6fde` / SS Micro-GA Fast / +5d: mới 18 [J013, J026, J036, J037, J047, J048, J051, J052, J097, J098, J111, J117, J125, J141, J147, J152, J157, J158]; tổng hồi phục so baseline 38.
- `3495dcebb04b4cddb77dd6c8182e6fde` / SS Micro-GA Fast / +7d: mới 9 [J019, J020, J069, J070, J085, J101, J119, J122, J132]; tổng hồi phục so baseline 47.
- `873ff366301449b6834a057cd95f8077` / SS Micro-GA Fast / +1d: mới 1 [J009]; tổng hồi phục so baseline 1.
- `873ff366301449b6834a057cd95f8077` / SS Micro-GA Fast / +3d: mới 1 [J005]; tổng hồi phục so baseline 2.
- `873ff366301449b6834a057cd95f8077` / SS Micro-GA Fast / +5d: mới 0 [—]; tổng hồi phục so baseline 2.
- `873ff366301449b6834a057cd95f8077` / SS Micro-GA Fast / +7d: mới 0 [—]; tổng hồi phục so baseline 2.
- `ff76a891ba5e4697b6b623e5f5479a26` / SS Micro-GA Fast / +1d: mới 0 [—]; tổng hồi phục so baseline 0.
- `ff76a891ba5e4697b6b623e5f5479a26` / SS Micro-GA Fast / +3d: mới 0 [—]; tổng hồi phục so baseline 0.
- `ff76a891ba5e4697b6b623e5f5479a26` / SS Micro-GA Fast / +5d: mới 0 [—]; tổng hồi phục so baseline 0.
- `ff76a891ba5e4697b6b623e5f5479a26` / SS Micro-GA Fast / +7d: mới 0 [—]; tổng hồi phục so baseline 0.
- `5e0398c1ac814b478f85b1c6ae02f9bf` / SS Micro-GA Fast / +1d: mới 3 [J001, J005, J009]; tổng hồi phục so baseline 3.
- `5e0398c1ac814b478f85b1c6ae02f9bf` / SS Micro-GA Fast / +3d: mới 0 [—]; tổng hồi phục so baseline 3.
- `5e0398c1ac814b478f85b1c6ae02f9bf` / SS Micro-GA Fast / +5d: mới 0 [—]; tổng hồi phục so baseline 3.
- `5e0398c1ac814b478f85b1c6ae02f9bf` / SS Micro-GA Fast / +7d: mới 0 [—]; tổng hồi phục so baseline 3.
- `821e545e1aa3440b9646255e24c8223f` / SS Micro-GA Fast / +1d: mới 6 [J003, J010, J013, J081, J083, J089]; tổng hồi phục so baseline 6.
- `821e545e1aa3440b9646255e24c8223f` / SS Micro-GA Fast / +3d: mới 3 [J002, J085, J092]; tổng hồi phục so baseline 9.
- `821e545e1aa3440b9646255e24c8223f` / SS Micro-GA Fast / +5d: mới 0 [—]; tổng hồi phục so baseline 9.
- `821e545e1aa3440b9646255e24c8223f` / SS Micro-GA Fast / +7d: mới 0 [—]; tổng hồi phục so baseline 9.
- `eb3b3b95ecc044298623307c415a8d9d` / SS Micro-GA Fast / +1d: mới 3 [J001, J008, J009]; tổng hồi phục so baseline 3.
- `eb3b3b95ecc044298623307c415a8d9d` / SS Micro-GA Fast / +3d: mới 1 [J013]; tổng hồi phục so baseline 4.
- `eb3b3b95ecc044298623307c415a8d9d` / SS Micro-GA Fast / +5d: mới 1 [J005]; tổng hồi phục so baseline 5.
- `eb3b3b95ecc044298623307c415a8d9d` / SS Micro-GA Fast / +7d: mới 0 [—]; tổng hồi phục so baseline 5.
- `941fd62af4014379a5d6e393ed804f10` / SS Micro-GA Fast / +1d: mới 12 [J021, J051, J054, J055, J062, J090, J091, J104, J136, J165, J174, J175]; tổng hồi phục so baseline 12.
- `941fd62af4014379a5d6e393ed804f10` / SS Micro-GA Fast / +3d: mới 12 [J027, J042, J053, J056, J059, J061, J064, J067, J076, J105, J127, J131]; tổng hồi phục so baseline 24.
- `941fd62af4014379a5d6e393ed804f10` / SS Micro-GA Fast / +5d: mới 14 [J025, J032, J044, J063, J081, J082, J092, J144, J149, J153, J154, J164, J169, J172]; tổng hồi phục so baseline 38.
- `941fd62af4014379a5d6e393ed804f10` / SS Micro-GA Fast / +7d: mới 16 [J031, J033, J040, J043, J110, J112, J113, J120, J124, J137, J146, J162, J168, J171, J173, J176]; tổng hồi phục so baseline 54.
- `b0073cbbc54d467d9df25a278e7acf5d` / SS Micro-GA Fast / +1d: mới 4 [J008, J014, J016, J021]; tổng hồi phục so baseline 4.
- `b0073cbbc54d467d9df25a278e7acf5d` / SS Micro-GA Fast / +3d: mới 5 [J001, J009, J010, J013, J023]; tổng hồi phục so baseline 9.
- `b0073cbbc54d467d9df25a278e7acf5d` / SS Micro-GA Fast / +5d: mới 1 [J005]; tổng hồi phục so baseline 10.
- `b0073cbbc54d467d9df25a278e7acf5d` / SS Micro-GA Fast / +7d: mới 0 [—]; tổng hồi phục so baseline 10.
- `7b97a2b8f5874d6d849ff7e56e7abbed` / SS Micro-GA Fast / +1d: mới 3 [J001, J008, J009]; tổng hồi phục so baseline 3.
- `7b97a2b8f5874d6d849ff7e56e7abbed` / SS Micro-GA Fast / +3d: mới 1 [J013]; tổng hồi phục so baseline 4.
- `7b97a2b8f5874d6d849ff7e56e7abbed` / SS Micro-GA Fast / +5d: mới 1 [J005]; tổng hồi phục so baseline 5.
- `7b97a2b8f5874d6d849ff7e56e7abbed` / SS Micro-GA Fast / +7d: mới 0 [—]; tổng hồi phục so baseline 5.
- `d609b6ca42974afa8e446ee3b914159d` / SS Micro-GA Fast / +1d: mới 3 [J001, J008, J009]; tổng hồi phục so baseline 3.
- `d609b6ca42974afa8e446ee3b914159d` / SS Micro-GA Fast / +3d: mới 1 [J013]; tổng hồi phục so baseline 4.
- `d609b6ca42974afa8e446ee3b914159d` / SS Micro-GA Fast / +5d: mới 1 [J005]; tổng hồi phục so baseline 5.
- `d609b6ca42974afa8e446ee3b914159d` / SS Micro-GA Fast / +7d: mới 0 [—]; tổng hồi phục so baseline 5.
- `3c86a353b41443bfae949583a96b2f20` / SS Micro-GA Fast / +1d: mới 3 [J001, J008, J009]; tổng hồi phục so baseline 3.
- `3c86a353b41443bfae949583a96b2f20` / SS Micro-GA Fast / +3d: mới 1 [J013]; tổng hồi phục so baseline 4.
- `3c86a353b41443bfae949583a96b2f20` / SS Micro-GA Fast / +5d: mới 1 [J005]; tổng hồi phục so baseline 5.
- `3c86a353b41443bfae949583a96b2f20` / SS Micro-GA Fast / +7d: mới 0 [—]; tổng hồi phục so baseline 5.
- `eca167338094498dabd9985e3282e1cc` / SS Micro-GA Fast / +1d: mới 0 [—]; tổng hồi phục so baseline 0.
- `eca167338094498dabd9985e3282e1cc` / SS Micro-GA Fast / +3d: mới 1 [J005]; tổng hồi phục so baseline 1.
- `eca167338094498dabd9985e3282e1cc` / SS Micro-GA Fast / +5d: mới 0 [—]; tổng hồi phục so baseline 1.
- `eca167338094498dabd9985e3282e1cc` / SS Micro-GA Fast / +7d: mới 0 [—]; tổng hồi phục so baseline 1.
- `4013da12e01449adbbae8eae8df7abc3` / SS Micro-GA Fast / +1d: mới 1 [J008]; tổng hồi phục so baseline 1.
- `4013da12e01449adbbae8eae8df7abc3` / SS Micro-GA Fast / +3d: mới 2 [J005, J009]; tổng hồi phục so baseline 3.
- `4013da12e01449adbbae8eae8df7abc3` / SS Micro-GA Fast / +5d: mới 0 [—]; tổng hồi phục so baseline 3.
- `4013da12e01449adbbae8eae8df7abc3` / SS Micro-GA Fast / +7d: mới 0 [—]; tổng hồi phục so baseline 3.
- `ddcc6e11ae154fbf941b8fb24fa1f549` / SS Micro-GA Fast / +1d: mới 1 [J009]; tổng hồi phục so baseline 1.
- `ddcc6e11ae154fbf941b8fb24fa1f549` / SS Micro-GA Fast / +3d: mới 1 [J005]; tổng hồi phục so baseline 2.
- `ddcc6e11ae154fbf941b8fb24fa1f549` / SS Micro-GA Fast / +5d: mới 0 [—]; tổng hồi phục so baseline 2.
- `ddcc6e11ae154fbf941b8fb24fa1f549` / SS Micro-GA Fast / +7d: mới 0 [—]; tổng hồi phục so baseline 2.
- `236e9e6fbb1044fab37acb6064f8b4b2` / SS Micro-GA Fast / +1d: mới 9 [J021, J054, J069, J070, J078, J085, J096, J124, J172]; tổng hồi phục so baseline 9.
- `236e9e6fbb1044fab37acb6064f8b4b2` / SS Micro-GA Fast / +3d: mới 18 [J017, J018, J025, J053, J055, J062, J071, J072, J084, J095, J113, J137, J153, J154, J163, J164, J179, J180]; tổng hồi phục so baseline 27.
- `236e9e6fbb1044fab37acb6064f8b4b2` / SS Micro-GA Fast / +5d: mới 12 [J050, J061, J077, J109, J112, J126, J129, J140, J148, J160, J166, J167]; tổng hồi phục so baseline 39.
- `236e9e6fbb1044fab37acb6064f8b4b2` / SS Micro-GA Fast / +7d: mới 5 [J029, J039, J130, J139, J159]; tổng hồi phục so baseline 44.
- `182741f9f6874dcfb8a7debff0b834c5` / SS Micro-GA Fast / +1d: mới 3 [J001, J008, J009]; tổng hồi phục so baseline 3.
- `182741f9f6874dcfb8a7debff0b834c5` / SS Micro-GA Fast / +3d: mới 1 [J013]; tổng hồi phục so baseline 4.
- `182741f9f6874dcfb8a7debff0b834c5` / SS Micro-GA Fast / +5d: mới 1 [J005]; tổng hồi phục so baseline 5.
- `182741f9f6874dcfb8a7debff0b834c5` / SS Micro-GA Fast / +7d: mới 0 [—]; tổng hồi phục so baseline 5.
- `ae171b2deebb4768823073b51cc3b227` / MLPSO / +1d: mới 3 [J001, J005, J009]; tổng hồi phục so baseline 3.
- `ae171b2deebb4768823073b51cc3b227` / MLPSO / +3d: mới 0 [—]; tổng hồi phục so baseline 3.
- `ae171b2deebb4768823073b51cc3b227` / MLPSO / +5d: mới 0 [—]; tổng hồi phục so baseline 3.
- `ae171b2deebb4768823073b51cc3b227` / MLPSO / +7d: mới 0 [—]; tổng hồi phục so baseline 3.

## E. Recommendation
Chọn mức gia hạn theo từng lịch và cam kết khách hàng; không có một mức chung bảo đảm OTD cho mọi lịch. Đây là cải thiện KPI do thay deadline, **không phải thuật toán chạy tốt hơn, tăng công suất hay hoàn thành sớm hơn**. Cmax/release/processing/calendar/machine capacity/schedule không đổi trong mọi scenario. Các run lịch sử có thể trùng input hoặc chạy nhiều lần: không coi tổng jobs qua các run là số đơn hàng độc lập; không so chất lượng solver khi input khác nhau.
Với mỗi lịch: mức ngắn nhất đạt 100% OTD trong các mốc thử (nếu có) được liệt kê dưới; đây là lựa chọn sensitivity, không phải đề nghị tự đổi hợp đồng. Với lịch đã đúng hạn toàn bộ, không cần gia hạn.
- `3cf4cba9e5024ff789e8664bfb0db9c5` / MILP: mốc ngắn nhất hết trễ: +0d; giảm count mạnh nhất giữa hai mốc: 0 jobs, tại mốc +1d, +3d, +5d, +7d.
- `3f1bf327f69c4efc99f714abb4344eaa` / MILP: mốc ngắn nhất hết trễ: +0d; giảm count mạnh nhất giữa hai mốc: 0 jobs, tại mốc +1d, +3d, +5d, +7d.
- `624fbda493f04fa4ab612f8096db3d79` / MILP: mốc ngắn nhất hết trễ: +0d; giảm count mạnh nhất giữa hai mốc: 0 jobs, tại mốc +1d, +3d, +5d, +7d.
- `6e4a706a002c4a94b36cef94ca461324` / Improved GA: mốc ngắn nhất hết trễ: +1d; giảm count mạnh nhất giữa hai mốc: 14 jobs, tại mốc +1d.
- `6e4a706a002c4a94b36cef94ca461324` / MLPSO: mốc ngắn nhất hết trễ: +7d; giảm count mạnh nhất giữa hai mốc: 17 jobs, tại mốc +1d.
- `bc091d20a1a74f46ae606ee1156d4977` / Improved GA: mốc ngắn nhất hết trễ: +1d; giảm count mạnh nhất giữa hai mốc: 14 jobs, tại mốc +1d.
- `bc091d20a1a74f46ae606ee1156d4977` / MLPSO: mốc ngắn nhất hết trễ: +7d; giảm count mạnh nhất giữa hai mốc: 17 jobs, tại mốc +1d.
- `8f661d4db87e42f58b2f80cc294192cc` / MILP: mốc ngắn nhất hết trễ: +0d; giảm count mạnh nhất giữa hai mốc: 0 jobs, tại mốc +1d, +3d, +5d, +7d.
- `bb2d7779819c49bdb448c096b71e966d` / CP-SAT: mốc ngắn nhất hết trễ: +1d; giảm count mạnh nhất giữa hai mốc: 1 jobs, tại mốc +1d.
- `39ab6409b8534d68b36b9f226b6a4a36` / CP-SAT: mốc ngắn nhất hết trễ: +0d; giảm count mạnh nhất giữa hai mốc: 0 jobs, tại mốc +1d, +3d, +5d, +7d.
- `0e56b8416af54edb82e4124a99044374` / CP-SAT: mốc ngắn nhất hết trễ: +0d; giảm count mạnh nhất giữa hai mốc: 0 jobs, tại mốc +1d, +3d, +5d, +7d.
- `4eb483040abd49dba2a894a4f862dfc2` / CP-SAT: mốc ngắn nhất hết trễ: +0d; giảm count mạnh nhất giữa hai mốc: 0 jobs, tại mốc +1d, +3d, +5d, +7d.
- `dde50d813ab9458ca06a1bf461cb6389` / CP-SAT: mốc ngắn nhất hết trễ: +0d; giảm count mạnh nhất giữa hai mốc: 0 jobs, tại mốc +1d, +3d, +5d, +7d.
- `b4995c73b5cf4a6da8f21743060e57f5` / CP-SAT: mốc ngắn nhất hết trễ: +1d; giảm count mạnh nhất giữa hai mốc: 1 jobs, tại mốc +1d.
- `6891c237935945d287bb5e61c01c1547` / SS Micro-GA Fast: mốc ngắn nhất hết trễ: +0d; giảm count mạnh nhất giữa hai mốc: 0 jobs, tại mốc +1d, +3d, +5d, +7d.
- `9c3a933a5204428c96b97f592e66a46e` / SS Micro-GA Fast: mốc ngắn nhất hết trễ: +0d; giảm count mạnh nhất giữa hai mốc: 0 jobs, tại mốc +1d, +3d, +5d, +7d.
- `93e3d5cd15514a379fe252b93debac72` / SS Micro-GA Fast: chưa hết trễ dù +7d; giảm count mạnh nhất giữa hai mốc: 10 jobs, tại mốc +5d.
- `8af4452495fb41a19ee8d5a4b3e2f261` / SS Micro-GA Fast: mốc ngắn nhất hết trễ: +0d; giảm count mạnh nhất giữa hai mốc: 0 jobs, tại mốc +1d, +3d, +5d, +7d.
- `3d86640c8d6c449181679c74d1f5994e` / SS Micro-GA Fast: mốc ngắn nhất hết trễ: +3d; giảm count mạnh nhất giữa hai mốc: 1 jobs, tại mốc +1d, +3d.
- `a3a0d47c8daa4e12bcc195905801d0ac` / SS Micro-GA Fast: mốc ngắn nhất hết trễ: +1d; giảm count mạnh nhất giữa hai mốc: 1 jobs, tại mốc +1d.
- `5f41d17437ac479c9d59f4d7a9b5cb91` / SS Micro-GA Fast: chưa hết trễ dù +7d; giảm count mạnh nhất giữa hai mốc: 10 jobs, tại mốc +5d.
- `f791aaa9ed014b79a6be904cb9599941` / SS Micro-GA Fast: chưa hết trễ dù +7d; giảm count mạnh nhất giữa hai mốc: 12 jobs, tại mốc +5d.
- `7a662d8469c24c1eb8a83e780f770e3a` / SS Micro-GA Fast: chưa hết trễ dù +7d; giảm count mạnh nhất giữa hai mốc: 9 jobs, tại mốc +3d.
- `f464958c1a07470c9a44599988b26b6e` / SS Micro-GA Fast: mốc ngắn nhất hết trễ: +0d; giảm count mạnh nhất giữa hai mốc: 0 jobs, tại mốc +1d, +3d, +5d, +7d.
- `66e4ed58f36b4ffebdbfb11eb55a1382` / SS Micro-GA Fast: mốc ngắn nhất hết trễ: +3d; giảm count mạnh nhất giữa hai mốc: 1 jobs, tại mốc +3d.
- `47bf22ba007c49a6ac96be65d0fe8787` / SS Micro-GA Fast: mốc ngắn nhất hết trễ: +3d; giảm count mạnh nhất giữa hai mốc: 1 jobs, tại mốc +1d, +3d.
- `52df5e110f614b159931f7b02c80139d` / SS Micro-GA Fast: mốc ngắn nhất hết trễ: +3d; giảm count mạnh nhất giữa hai mốc: 2 jobs, tại mốc +1d.
- `6d16cd92d84d4acd8f08aac1a9e26d0b` / SS Micro-GA Fast: chưa hết trễ dù +7d; giảm count mạnh nhất giữa hai mốc: 18 jobs, tại mốc +5d.
- `9fb4e64c47d94cc48c8dd82a40d7cd15` / SS Micro-GA Fast: chưa hết trễ dù +7d; giảm count mạnh nhất giữa hai mốc: 18 jobs, tại mốc +5d.
- `3bcbb6a3d5ca4cec8e746bc087b1984d` / SS Micro-GA Fast: chưa hết trễ dù +7d; giảm count mạnh nhất giữa hai mốc: 18 jobs, tại mốc +5d.
- `6a49560570234bfdb2c36f55e49c5265` / SS Micro-GA Fast: chưa hết trễ dù +7d; giảm count mạnh nhất giữa hai mốc: 17 jobs, tại mốc +7d.
- `3dc65aeb040d42d7b9a07f115a00b888` / SS Micro-GA Fast: chưa hết trễ dù +7d; giảm count mạnh nhất giữa hai mốc: 14 jobs, tại mốc +3d.
- `b13bc76503bb49f481ee9f2b6a278a8b` / SS Micro-GA Fast: chưa hết trễ dù +7d; giảm count mạnh nhất giữa hai mốc: 14 jobs, tại mốc +3d.
- `dd9e8a1471ae47f4aefb2980b686bb50` / SS Micro-GA Fast: chưa hết trễ dù +7d; giảm count mạnh nhất giữa hai mốc: 19 jobs, tại mốc +3d.
- `3495dcebb04b4cddb77dd6c8182e6fde` / SS Micro-GA Fast: chưa hết trễ dù +7d; giảm count mạnh nhất giữa hai mốc: 18 jobs, tại mốc +5d.
- `873ff366301449b6834a057cd95f8077` / SS Micro-GA Fast: mốc ngắn nhất hết trễ: +3d; giảm count mạnh nhất giữa hai mốc: 1 jobs, tại mốc +1d, +3d.
- `ff76a891ba5e4697b6b623e5f5479a26` / SS Micro-GA Fast: mốc ngắn nhất hết trễ: +0d; giảm count mạnh nhất giữa hai mốc: 0 jobs, tại mốc +1d, +3d, +5d, +7d.
- `5e0398c1ac814b478f85b1c6ae02f9bf` / SS Micro-GA Fast: mốc ngắn nhất hết trễ: +1d; giảm count mạnh nhất giữa hai mốc: 3 jobs, tại mốc +1d.
- `821e545e1aa3440b9646255e24c8223f` / SS Micro-GA Fast: mốc ngắn nhất hết trễ: +3d; giảm count mạnh nhất giữa hai mốc: 6 jobs, tại mốc +1d.
- `eb3b3b95ecc044298623307c415a8d9d` / SS Micro-GA Fast: mốc ngắn nhất hết trễ: +5d; giảm count mạnh nhất giữa hai mốc: 3 jobs, tại mốc +1d.
- `941fd62af4014379a5d6e393ed804f10` / SS Micro-GA Fast: chưa hết trễ dù +7d; giảm count mạnh nhất giữa hai mốc: 16 jobs, tại mốc +7d.
- `b0073cbbc54d467d9df25a278e7acf5d` / SS Micro-GA Fast: mốc ngắn nhất hết trễ: +5d; giảm count mạnh nhất giữa hai mốc: 5 jobs, tại mốc +3d.
- `7b97a2b8f5874d6d849ff7e56e7abbed` / SS Micro-GA Fast: mốc ngắn nhất hết trễ: +5d; giảm count mạnh nhất giữa hai mốc: 3 jobs, tại mốc +1d.
- `d609b6ca42974afa8e446ee3b914159d` / SS Micro-GA Fast: mốc ngắn nhất hết trễ: +5d; giảm count mạnh nhất giữa hai mốc: 3 jobs, tại mốc +1d.
- `3c86a353b41443bfae949583a96b2f20` / SS Micro-GA Fast: mốc ngắn nhất hết trễ: +5d; giảm count mạnh nhất giữa hai mốc: 3 jobs, tại mốc +1d.
- `eca167338094498dabd9985e3282e1cc` / SS Micro-GA Fast: mốc ngắn nhất hết trễ: +3d; giảm count mạnh nhất giữa hai mốc: 1 jobs, tại mốc +3d.
- `4013da12e01449adbbae8eae8df7abc3` / SS Micro-GA Fast: mốc ngắn nhất hết trễ: +3d; giảm count mạnh nhất giữa hai mốc: 2 jobs, tại mốc +3d.
- `ddcc6e11ae154fbf941b8fb24fa1f549` / SS Micro-GA Fast: mốc ngắn nhất hết trễ: +3d; giảm count mạnh nhất giữa hai mốc: 1 jobs, tại mốc +1d, +3d.
- `236e9e6fbb1044fab37acb6064f8b4b2` / SS Micro-GA Fast: chưa hết trễ dù +7d; giảm count mạnh nhất giữa hai mốc: 18 jobs, tại mốc +3d.
- `182741f9f6874dcfb8a7debff0b834c5` / SS Micro-GA Fast: mốc ngắn nhất hết trễ: +5d; giảm count mạnh nhất giữa hai mốc: 3 jobs, tại mốc +1d.
- `ae171b2deebb4768823073b51cc3b227` / MLPSO: mốc ngắn nhất hết trễ: +1d; giảm count mạnh nhất giữa hai mốc: 3 jobs, tại mốc +1d.

### Nếu được phê duyệt triển khai sau này
Không cần sửa solver để làm chế độ sensitivity trên lịch cố định. Đề xuất thêm endpoint phân tích riêng và tham số extension_days, không mutate frozen run: `backend/models/schemas.py` (request riêng), `backend/api/scheduling.py` (endpoint), một service sensitivity mới dùng completion/KPI hiện có, `src/api.ts` và `src/pages/ScheduleFlow.tsx` hoặc trang kết quả riêng (điều khiển và bảng); `backend/services/excel_service.py` nếu cần xuất báo cáo riêng. Không sửa workbook importer/calendar/decoder/objective. Nếu lưu scenario vào DB thì cần thiết kế/migration riêng được duyệt; thử nghiệm này không cần DB migration.
Nếu mục tiêu là giảm trễ bằng lịch sản xuất tốt hơn, Due Date Extension không thay thế tối ưu tardiness, kiểm tra công suất hay xác minh due date thực tế. Đổi objective/seed khi re-optimize là phạm vi khác, cần phê duyệt riêng. Không triển khai các thay đổi này trong lượt TEST ONLY.

## Bảo toàn và giới hạn
Code đổi hash: []; nguồn input/Excel đổi hash: []; DB giữ nguyên hash/no-WAL: True.
Không claim lịch historical tuân thủ weekly shutdown ngoài calendar đã lưu. Báo cáo chỉ bao phủ snapshot đã ghi và có thể không gồm run phát sinh sau snapshot. Nếu DB thay đổi đồng thời, không chứng nhận bảo toàn byte identity; manifest sẽ ghi FAIL. Không có kết luận cải thiện chất lượng solver từ thử nghiệm này.
