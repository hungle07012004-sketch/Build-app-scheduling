# MLPSO V3 trong Scheduling App V5

Ngày 10/11/2026. Tiếp nối tích hợp solver package. Không chạy benchmark chính thức.

## Thay đổi

- `backend/services/algorithms/mlpso_adapter.py`: đọc schema frozen input; V5 gọi `backend.solver_v5.solve_mlpso_v3_instance`, legacy giữ nguyên đường solver cũ. Truyền population, iterations, MR/PCR/GCR, runs, seed và decode cap; serial attempts, có convergence.
- `backend/services/algorithm_selection.py`: khai báo MLPSO khả dụng khi module V3 tồn tại. Giữ nguyên luật ưu tiên Automatic giữa CP-SAT/GA cho V5; không tự chuyển mọi run sang MLPSO.
- `backend/services/optimization_service.py`: cho phép lựa chọn MLPSO thủ công với V5. Trước tạo run, kiểm tra swarm size bằng guard sẵn có của V3; lỗi nêu rõ cần chỉnh Advanced settings. Cho phép preview/validate trước bước này để người dùng vào được màn hình cấu hình.
- `backend/models/schemas.py`: thêm `mlpso_max_decodes`, default 12000, miền 1–100000. Không đổi giá trị cấu hình đã lưu.
- `src/components/SchedulingMethod.tsx`, `src/algorithms.ts`: mở MLPSO cho V5 và ghi đúng tên V3/shared decoder.
- `src/algorithmSettings.ts`, `src/pages/ScheduleFlow.tsx`: cấu hình decode cap cho từng run; nêu rõ MLPSO không có wall-clock cap. Không hiển thị ô GA seconds cho MLPSO. Swarm V5 phải chia hết cho 3 và không vượt số permutation đủ jobs; không tự điều chỉnh tham số.
- `backend/tests/test_mlpso_app.py`: 4 cases về dispatch/settings, invalid swarm trước khi tạo run và kết quả chưa có nghiệm.
- `backend/tests/test_v5.py`: thêm MLPSO vào test toàn luồng API thật; cập nhật case thuật toán không tương thích.
- `backend/tests/test_algorithm_selection.py`: cập nhật availability và manual preview; vẫn kiểm tra preview không tạo run và Automatic/legacy.
- `tests/e2e/mlpso-v5.spec.ts`: test trình duyệt thật, dùng backend QA riêng.
- Báo cáo này và artifacts mới trong `.tmp-qa/mlpso-app-20261110/`.

Không sửa solver V3, decoder, GA/CP-SAT, legacy solver, objective, constraints, calendar hay horizon. Kiểm tra 11 protected source files theo baseline trước tích hợp V3: không file nào đổi hash. Không gọi API ghi vào App đang dùng; pytest và browser dùng database/thư mục QA riêng. Không restore database đang hoạt động, không ghi đè benchmark cũ.

## Kết quả thực tế

| Kiểm tra | Kết quả |
|---|---|
| Backend mục tiêu | PASS 21/21, 7,52 giây |
| Toàn bộ backend | PASS 330/330, 74,25 giây; 1 warning Starlette/httpx có sẵn |
| TypeScript `tsc --noEmit` | PASS |
| Production Vite build | PASS |
| Browser V5 MLPSO thực tế | PASS 1/1: upload 20-job input, chọn MLPSO, cấu hình, chạy solver, validator, metadata/evaluation cap, export Excel |
| API lifecycle MLPSO | PASS: validate, run qua worker/process, đọc result, export, accept/apply, execution; database test riêng |
| Legacy, GA và CP-SAT | PASS trong full backend; không sửa thuật toán |

Browser chạy production build trên `127.0.0.1:8002`, `SERVE_FRONTEND=1`, database `.tmp-qa/mlpso-app-20261110/browser-data-final`. Không dùng production backend 8000. Nguồn upload là frozen 20-job input của pilot; App vẫn áp dụng quy trình freeze/calendar policy hiện có, không sửa quy trình này. Vì vậy không dùng kết quả browser để so sánh trực tiếp với smoke solver package trên nguyên JSON gốc.

Full backend bao gồm 325 cases trước lượt này, 4 cases mới trong `test_mlpso_app.py` và 1 case MLPSO thêm vào lifecycle hiện có. Không cộng các lần chạy lặp vào số cases. Lần khởi chạy pytest đầu lỗi thư mục cha `basetemp` chưa tồn tại; sau tạo thư mục QA, 21/21 và 330/330 đều PASS. Browser harness ban đầu sai working directory/test directory và vướng cleanup server Windows; sau tách quản lý server QA, browser test chạy thành công. Không coi lỗi harness này là lỗi solver.

## Sử dụng và giới hạn

Chọn **MLPSO → Validate Data → Advanced settings → Run MLPSO**. Seed/runs và các xác suất dùng giá trị đang cấu hình; `mlpso_max_decodes` giới hạn search evaluations mỗi attempt. Các bước kiểm tra/tái dựng lịch cuối được solver ghi riêng. Iteration/decode cap không phải thời gian tối đa theo giây và chưa phải protocol equal-time benchmark.

Với 4 jobs, default population 60 vượt 4! = 24: cần tự chọn một population hợp lệ, ví dụ 6. App báo lỗi trước khi tạo run nếu cấu hình này không hợp lệ, không tự tune để tạo nghiệm. Một job không có đủ 3 permutations cho 3 subpopulations của V3; cần dùng solver khác, không thay cấu trúc MLPSO để che giới hạn.

Backend đang chạy từ trước cần được khởi động lại để nạp adapter/availability mới; giao diện production cần reload để lấy build mới. Không tự dừng production backend vì có thể đang có scheduling run. Server QA được dừng sau kiểm thử.

**Kết luận:** MLPSO V3 đã kết nối vào luồng App V5 và qua kiểm thử chức năng. Chưa tích hợp MLPSO vào `backend.benchmark_v5`, chưa thống nhất protocol benchmark GA–MLPSO và chưa giải quyết khác biệt frozen calendar/weekly shutdown trong pilot cũ. Không suy ra ưu thế chất lượng hoặc độ ổn định trên mọi quy mô từ các test này.

Evidence: `target.xml`/`target.log`, `backend-final.xml`/`backend-final.log`, `build.log`, `browser-final.log`, browser JSON report, `protected-source-verification.json` trong thư mục QA nêu trên. Dữ liệu run/browser và logs solver nằm trong `browser-data-final/runs/`.
