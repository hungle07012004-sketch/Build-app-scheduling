# Pilot benchmark V5 — 08/11/2026

**Kết luận: pilot chạy kỹ thuật PASS; điều kiện dữ liệu để benchmark theo lịch sản xuất hiện hành FAIL. Chưa nên chạy benchmark luận văn chính thức trên bộ frozen này.**

Không sửa solver, runner, decoder, constraints, calendar, dữ liệu gốc hoặc kết quả cũ. Chỉ tạo artifacts mới trong `.tmp-qa/2026-11-08-pilot-30s/` và báo cáo này.

## Protocol thực tế

- Runner: `backend.benchmark_v5`, dùng hai adapter và solver hiện tại của App.
- Hai frozen instances `AL13_n020_i01.json`, `AL13_n040_i01.json` từ `correct workstation/benchmark_instances_v5_h60/`.
- Seeds: **42, 1051**; một attempt mỗi thuật toán/seed, một worker, chạy tuần tự. Tổng **8 lần chạy**, không warm start CP-SAT từ GA.
- Nominal search-time budget: **30 giây mỗi thuật toán/seed**. Tổng thời gian end-to-end của 8 lời gọi và output validation: **240,975 giây**.
- Micro-GA dùng App defaults: population 24, generations 3000, max_decodes 12000, no_improve 600, min_epochs 1300, mutation 0,15, crossover 0,9. Settings đầy đủ nằm trong manifests.
- Cùng frozen bytes, calendar và objective `MIN_CMAX_FINAL_2C` cho hai thuật toán trong mỗi quy mô. Không so sánh trực tiếp Cmax giữa quy mô 20 và 40 như cùng một bài toán.
- Budget là search cap, không phải end-to-end time. Hạn bảo vệ tiến trình ngoài runner là 240 giây cho mỗi instance; không bị kích hoạt.

## Preflight và nguồn dữ liệu

| Kiểm tra | Kết quả |
|---|---|
| Đúng 20/40 jobs, schema V5, mandatory input validation | PASS — không có validation issues |
| Horizon | PASS — 2026-10-01 06:00 → 2026-11-30 06:00, 86.400 phút = 60 ngày |
| Input/calendars giống nhau giữa hai thuật toán trong từng cặp | PASS — 4/4 cặp |
| Code version/source hashes giống nhau giữa hai quy mô | PASS |
| Original/frozen input hash trước và sau chạy | PASS |
| Weekly shutdown hiện hành | **FAIL — 1.215 cửa sổ tài nguyên xung đột trong mỗi dataset** |

Input SHA-256:

- 20 jobs: `62d8d830242ae6948fee677c9706a77feb24a1d00db5bd70c62b6fc572c2b8f8`
- 40 jobs: `78ff8981bea147558dba7ee6cd3bf5e24d997100b9c712e41592f76b4b4e38b4`

Hai file cùng tên trong `correct workstation/fast decode/benchmark_instances_v5_h60/` có hash trùng các file trên; chuyển sang thư mục đó không giải quyết được vấn đề calendar.

Đã phát hiện vấn đề trước chạy và giữ nguyên dataset để thực hiện **pilot chẩn đoán theo historical saved calendar**. Không coi đây là bộ dữ liệu đã đạt lịch sản xuất hiện hành.

## Kết quả từng lần chạy

Runtime dưới đây là end-to-end, đơn vị giây; Cmax tính bằng phút. `PASS saved` nghĩa là qua validator theo đúng frozen input, không đồng nghĩa PASS weekly shutdown hiện hành.

| Jobs | Thuật toán | Seed | Feasibility theo frozen input | Cmax | Runtime | Stop/status | Gap đến optimum |
|---:|---|---:|---|---:|---:|---|---|
| 20 | CP-SAT | 42 | Chưa tìm được nghiệm | — | 30,095 | UNKNOWN | — |
| 20 | CP-SAT | 1051 | Chưa tìm được nghiệm | — | 30,112 | UNKNOWN | — |
| 20 | SS Micro-GA Fast | 42 | PASS saved | 8.340,446 | 30,083 | time_limit | — |
| 20 | SS Micro-GA Fast | 1051 | PASS saved | 8.340,446 | 30,079 | time_limit | — |
| 40 | CP-SAT | 42 | Chưa tìm được nghiệm | — | 30,188 | UNKNOWN | — |
| 40 | CP-SAT | 1051 | Chưa tìm được nghiệm | — | 30,176 | UNKNOWN | — |
| 40 | SS Micro-GA Fast | 42 | PASS saved | 15.290,777 | 30,124 | time_limit | — |
| 40 | SS Micro-GA Fast | 1051 | PASS saved | 14.744,400 | 30,118 | time_limit | — |

CP-SAT không có exception; `UNKNOWN` không phải chứng minh infeasible. Search runtime báo cáo khoảng 30 giây, nhưng adapter không cung cấp nguyên nhân dừng chi tiết nên không gán một stop reason chắc chắn ngoài status. Không có CP incumbent hợp lệ nên **cả 4 cặp đều không tính gap hoặc phần trăm chênh lệch với CP-SAT**. Không thay các ô thiếu bằng 0.

## KPI và convergence Micro-GA

| Jobs | Seed | Tardiness (phút) | Flow time (phút) | Search evaluations | Seed-refinement decodes | Epoch cuối | Events chồng shutdown hiện hành |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 20 | 42 | 3.003,117 | 60.761,216 | 5.089 | 57 | 879 | 241 |
| 20 | 1051 | 3.003,117 | 59.643,685 | 4.703 | 57 | 1.235 | 241 |
| 40 | 42 | 55.822,128 | 231.754,312 | 2.254 | 60 | 280 | 610 |
| 40 | 1051 | 49.370,516 | 204.298,980 | 2.217 | 60 | 239 | 432 |

- **PASS 4/4**: tính lại độc lập completion từng job từ VISUAL/2C_BATCH events; Cmax, tardiness và flow time khớp summary trong tolerance 0,0025 phút; không thiếu Job ID.
- **PASS 4/4**: trace có điểm cuối, không trùng epoch và khớp run summary về epoch/evaluations/Cmax/stop reason.
- Evaluations không bao gồm seed refinement hoặc final reconstruction. CP-SAT không có đại lượng decoder evaluations tương đương, để null.
- 20 jobs cùng Cmax nhưng khác flow time giữa hai seed là có thể xảy ra: objective chỉ tối ưu Cmax; flow time là diagnostic.
- Không chạy lặp cùng seed trong pilot này; **tái lập trên 20/40 jobs chưa được kiểm chứng**. Không suy rộng bằng chứng fixture 4 jobs từ lượt trước.

## Vấn đề phát hiện

### 1. Calendar frozen không tuân thủ shutdown hiện hành — mức độ cao, chặn benchmark chính thức

Weekly rule cần kiểm tra: Sunday 00:00 → Monday 06:00 không làm việc. Trong mỗi dataset có 1.215 availability windows chồng khoảng này (đếm theo resource/window; không phải 1.215 lần dừng máy độc lập).

Ví dụ `WS_2B_INSPECTION` có các availability windows:

- 2026-10-03 22:00 → 2026-10-04 02:00, đi qua Saturday midnight vào Sunday.
- 2026-10-04 02:30 → 06:00, nằm trong Sunday shutdown.

Lịch GA thực sự dùng các khoảng này. Ví dụ 20 jobs, cả hai seed: J020 OIL_CYCLE chạy từ **2026-10-03 23:58:27,720 → 2026-10-04 00:03:40,080**, cắt qua mốc shutdown.

Nguyên nhân trực tiếp nằm trong **availability đã đóng băng**. Solver/validator đánh giá theo saved calendar nên chấp nhận; đây không phải bằng chứng solver bỏ qua các resource windows đã cung cấp. Input validator PASS không tự chứng minh lịch historical phù hợp weekly rule mới. Chỉ sửa Gantt hoặc tăng thời gian chạy sẽ không giải quyết dữ liệu calendar này.

Không tự sửa lịch hoặc ghi lại input trong pilot. Cần chuẩn bị **bộ frozen mới riêng biệt** từ calendar đã phê duyệt, kiểm tra Sunday/Monday boundary rồi đóng băng/hash lại; giữ bộ cũ để bảo toàn thực nghiệm lịch sử.

### 2. CP-SAT chưa có incumbent trong 30 giây — hạn chế budget, chưa kết luận lỗi solver

4/4 CP-SAT runs không có nghiệm để xác nhận KPI/gap. Chưa thể đánh giá chất lượng Micro-GA so với CP-SAT chỉ từ các lần chạy này. Sau khi đạt preflight calendar, nên thử lại paired pilot 120 giây, rồi 300 giây nếu cần, cùng seed và protocol. Không bổ sung warm start riêng cho CP-SAT mà vẫn gọi là cùng protocol hiện tại.

## Đánh giá trước benchmark luận văn

| Hạng mục | Đánh giá |
|---|---|
| Runner hoạt động ổn định trong pilot | PASS — 8/8 hoàn tất, 0 exception, cả hai CLI exit 0 |
| Điều kiện cùng input/config/code cho mỗi cặp | PASS |
| Micro-GA feasibility theo saved calendar/KPI/convergence | PASS 4/4 |
| CP-SAT feasible incumbent và paired quality/gap comparison | CHƯA ĐẠT — 0/4 incumbent, không tính gap |
| Tuân thủ weekly calendar hiện hành | FAIL |
| Dữ liệu gốc/source/database được giữ nguyên | PASS theo baseline và input hashes |
| Sẵn sàng benchmark chính thức trên bộ dữ liệu này | **CHƯA** |

Vì pilot chưa PASS đầy đủ, **chưa chốt cấu hình benchmark chính thức 20–180 jobs**. Trình tự tiếp theo: phê duyệt calendar → tạo bộ frozen mới mà không ghi đè bộ cũ → pilot 20/40 với budget lớn hơn và lặp seed → mới chốt danh sách quy mô, nhiều seeds và ngân sách. Bộ cũ chỉ phù hợp nghiên cứu với chính historical calendar của nó, phải ghi rõ khác quy tắc hiện hành.

## Evidence

- [Preflight, settings và toàn bộ calendar conflicts](.tmp-qa/2026-11-08-pilot-30s/preflight.json)
- [Verification: hash, KPI, convergence, paired conditions](.tmp-qa/2026-11-08-pilot-30s/verification.json)
- [20 jobs manifest](.tmp-qa/2026-11-08-pilot-30s/n020/manifest.json), [results JSON](.tmp-qa/2026-11-08-pilot-30s/n020/results.json), [CSV](.tmp-qa/2026-11-08-pilot-30s/n020/results.csv)
- [40 jobs manifest](.tmp-qa/2026-11-08-pilot-30s/n040/manifest.json), [results JSON](.tmp-qa/2026-11-08-pilot-30s/n040/results.json), [CSV](.tmp-qa/2026-11-08-pilot-30s/n040/results.csv)
- [Script đối chiếu KPI/calendar/trace](.tmp-qa/2026-11-08-pilot-30s/verify_pilot.py)

Không chạy lại unit/full backend suites vì không sửa production code. PASS/FAIL trong báo cáo này dựa trên pilot và kiểm tra artifacts thực tế, không lấy số tests của lượt trước làm bằng chứng cho pilot.
