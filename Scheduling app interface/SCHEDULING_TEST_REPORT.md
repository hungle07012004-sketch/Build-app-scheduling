# AL13 V5 — Stability & Correctness Test Report

Ngày audit: **24/10/2026, Asia/Saigon**. Vai trò: kiểm thử/đánh giá, không sửa production code.

## 1. Kết luận

**Chưa khuyến nghị vận hành production không giám sát.** Luồng hợp lệ và hai lịch 180 jobs gần nhất vượt qua các kiểm tra đã thực hiện, nhưng kiểm thử dữ liệu bất thường phát hiện **5 nhóm lỗi validation** (QA-01…QA-05). Đặc biệt, một precedence bổ sung tạo chu trình có thể bị bỏ qua và kết quả vẫn được đánh dấu `Feasible`; setup âm cũng có thể bị coi như không cần setup.

- Backend hiện có: **70/70 PASS**, không skip, 69,54 giây.
- Frontend hiện có: **32/32 PASS**, không skip/flaky, 116,79 giây; gồm **25 browser tests và 7 kiểm tra helper calendar**.
- TypeScript: **PASS**. Vite production build: **PASS**. Lint: **NOT TESTED** — không có script lint cấu hình.
- Negative-input bổ sung: **6 PASS / 4 FAIL**. Corruption tests cho assignment đầu ra: **2 FAIL**, cùng nhóm QA-05.
- Kiểm tra độc lập hai lịch 180 jobs: **PASS**, mỗi lịch 11.287 events; không phát hiện vi phạm trong phạm vi đã kiểm tra.
- Luồng Excel V5 thật → chọn 10 jobs → SS Micro-GA → accept/save → reopen → Excel: **PASS**, 621 events.
- Đồng thời gửi 2 run, đọc health, accept/apply và tính bất biến snapshot: **PASS** trên database tạm.
- Không tự động sửa lỗi. Không chạy lại lịch sản xuất. `Proven optimal = false` ở GA là trạng thái đúng, **không phải bug**.

Kết luận PASS chỉ áp dụng đúng ca/fixture nêu trong báo cáo, không phải chứng nhận mọi dữ liệu hay mọi cấu hình đều đúng.

## 2. Phạm vi và bảo toàn dữ liệu

- Workspace: `Scheduling app interface`, Windows 11, Python 3.13.12, Node 22.23.3, Vite 8.0.5, 8 logical processors.
- CPU nhận diện: `Intel64 Family 6 Model 140 Stepping 1, GenuineIntel`.
- Đã hash **208 file** thuộc `src`, `backend`, `code`, `tests` trước và sau: **0 file thay đổi**, gồm database sản xuất và các snapshot/results trong `backend/storage`.
- Các truy vấn vào database sản xuất dùng SQLite `mode=ro`. Không gọi API mutate trên backend sản xuất và không restart backend sản xuất.
- Backend pytest dùng temp fixtures. Browser suite dùng bản sao lịch test, server riêng **127.0.0.1:8013**, không tái sử dụng server 8000/8443.
- Benchmark, raw result, workbook thử, database tạm, checker và logs nằm trong `.tmp-qa/2026-10-24/`; một số screenshot/manifest do test cũ ghi vào `.tmp-verification/` theo thiết kế sẵn có.
- Build chạy thành công; bản build audit riêng nằm ở `.tmp-qa/2026-10-24/dist`. Lệnh wrapper build ban đầu bỏ qua tham số outDir nên cũng tái tạo `dist`; không sửa source. Browser audit dùng bản build riêng.
- CP-SAT stress giới hạn **2 workers**; các stress task chạy tuần tự, micro budget 8–15 giây, process timeout **75 giây/case**, `OPENBLAS_NUM_THREADS=1`, `OMP_NUM_THREADS=2`. Không cài dependency mới. Có đo peak RAM, không áp hard RAM cap.

Bằng chứng: [integrity.json](.tmp-qa/2026-10-24/integrity.json), [baseline.json](.tmp-qa/2026-10-24/baseline.json).

## 3. Ma trận kết quả

| Hạng mục | Trạng thái | Phạm vi thực chạy / giới hạn |
|---|---|---|
| Jobs, Resources, Processing Times, Calendar API | PASS | Import workbook V5 180 jobs, 9 workstation; các endpoint trả 200 |
| Excel/JSON upload hợp lệ | PASS | Workbook thật V5; JSON V5; legacy Excel 10 jobs và JSON trong backend tests |
| Invalid input | FAIL | 4/10 payload sai mới vẫn được chấp nhận; xem QA-01…04 |
| Workflow Input → Schedule → Gantt → Save → Export | PASS | Browser V5 CP-SAT + Micro-GA, API workbook V5 thật; không có một browser test duy nhất bao trọn workbook V5 thật |
| Precedence / PRE–FINAL / release trên nghiệm hợp lệ | PASS | Checker độc lập dùng toàn bộ cạnh input, lag SS_FIRST_BELT, FINAL finish ≥ PRE finish, release/material/loopset |
| Reject precedence thiếu/ngoài mô hình/chu trình | FAIL | QA-01, QA-02 |
| Resource capacity/conflicts | PASS | Sweep intervals trên từng resource unary, gồm operator dùng chung; OIL tách load/unload theo snapshot |
| OIL ≤6, phase duration và bảo toàn lượng | PASS | Raw solver events + snapshot; không chỉ dựa vào nhãn OIL trên Gantt |
| 2C full 10 / pooled 5+5, compatibility và quantity | PASS | Ca 4 jobs có 10/5/5/10; benchmark và lịch lớn; check số batch, key và VISUAL readiness |
| Setup / clean-pipeline trên input hợp lệ | PASS | Setup trước PRE, sau FINAL-2C của predecessor, duration/resources và cặp chuyển sản phẩm |
| Input setup/batch duration âm | FAIL | QA-03/04 |
| Non-preemption và breaks/downtime | PASS | Interval phải nằm trọn trong hợp các availability liền nhau; không vượt gap thực |
| Sunday 00:00 → Monday 06:00 | PASS | Calendar generation tests, boundary tests, lịch mới và checker độc lập |
| 3 shifts 06–14 / 14–22 / 22–06 | PASS | Shift labels/cross-shift tests; qua biên ca không tự động là vi phạm |
| 60-day horizon | PASS | Workbook thật 01/10 06:00 → 30/11 06:00; các saved/new checks không vượt horizon |
| Cmax | PASS | Tính lại từ job completion sau final 2C và đối chiếu objective |
| Physical assignment trong output bị sửa sai | FAIL | QA-05: Station_ID/Resource_Units không được đối chiếu đầy đủ |
| CP-SAT | PASS / NOT TESTED | Nghiệm nhỏ đã kiểm tra; benchmark thật 20 jobs/5s trả UNKNOWN, không có nghiệm để kiểm tra độc lập |
| Improved GA legacy + MLPSO legacy | PASS | Backend smoke 50 jobs chạy thật; khả năng giữ nghiệm solver kia khi một worker lỗi được kiểm tra với injected failure |
| V5 Improved GA standalone | PASS | Gọi trực tiếp solver có sẵn trên benchmark 20 jobs; không phải lựa chọn production V5 UI |
| V5 SS Micro-GA Fast | PASS | Backend/browser và các stress 20–180 + synthetic 300 |
| Fast vs Reference Decoder | PASS | 13 feasible comparisons có metric/event equality trong tolerance; 4 permutation cả hai đều từ chối vì không tìm slot khả thi |
| Reproducibility | PASS | Hai run seed 42, cùng input, 10 generations, đủ thời gian: chromosome + toàn bộ schedule trùng nhau |
| Reproducibility dưới wall-time cutoff / đa máy | NOT TESTED | Không suy từ fixed-generation test; số evaluations có thể thay đổi theo tải máy |
| 300/600/900s configuration | PASS | Existing adapter/payload tests + 3 lần solver thật cấu hình các budget này, dừng sau 2 generations (~0,3s) |
| Chạy liên tục đủ 300/600/900s và kiểm tra deadline tại từng mốc | NOT TESTED | Không chạy thêm 30 phút tối ưu; không dùng historical runtime thay cho test mới |
| Early stop / time limit / generation stop | PASS | Existing micro stopping tests + timed stress; bao gồm retain incumbent và clock-controlled deadline |
| Decoder budget stop với run dài mới | NOT TESTED | Có evidence lịch cũ nhưng chưa tái chạy tới 30.000 evaluations trong audit này |
| Gantt 1/3/7 days, midnight clipping, zoom, fit | PASS | Existing browser tests, data identity and sticky alignment assertions |
| Expand/Compact/Fit Height, filters, tooltips | PASS | Normal/expanded/responsive layout; 15 synthetic workstation rows ở 1920×1080 |
| Calendar Review / historical conflicts | PASS | Phân biệt saved availability và weekly rule; historical fixture báo 1.909 conflicts, không chỉnh sửa event |
| Gantt 10k–20k+ | PASS | Replay 11.290/22.580 events, synthetic 20.000 events; không chạy solver cho replay |
| Concurrent requests | PASS | 2 run submissions, 4 health reads/lượt, scheduler serializes; concurrent apply idempotent |
| Save/Reload, accepted-before-apply | PASS | Backend/browser/API checks, original snapshots không đổi khi sửa input tạm |
| JSON/Excel consistency | PASS | Exact string identities; datetime round-trip ≤1 ms, numeric float tolerance do Excel |
| XLSX 20.000 rows | PASS | Synthetic expanded result, 4.17s, không đổi data gốc |
| 300+ production-representative benchmark | BLOCKED | Folder benchmark hiện có lớn nhất 180; synthetic 300 đã chạy, không đại diện tải thật 300+ |
| Long soak / nhiều người dùng / browser memory profiling | NOT TESTED | Không khẳng định readiness dựa vào concurrency nhỏ và screenshot |

## 4. Kết quả performance

Wall time bên dưới đo solver + standardization + checker độc lập trong worker, không phải chỉ thời gian search. CPU seconds cộng thời gian các thread trong process; CP-SAT 2 workers có thể dùng CPU-seconds lớn hơn wall time. Peak working set là giá trị Windows của worker, bao gồm import, data, solver và kiểm tra; không phải RAM toàn máy hoặc aggregate browser/server.

| Case | Jobs | Budget s / generations | Wall s | CPU s | Peak MiB | Makespan min | Kết quả |
|---|---:|---:|---:|---:|---:|---:|---|
| cp-10 | 10 | 5 | 5.304 | 10.109 | 98.18 | 183.0 | PASS |
| cp-20 | 20 | 5 | 4.595 | 4.547 | 107.99 | Không tìm được nghiệm | PASS thực thi; NOT TESTED kiểm tra nghiệm (UNKNOWN) |
| ga-20 | 20 | 4 generations | 1.262 | 1.266 | 79.84 | 11373.0 | PASS |
| micro-20 | 20 | 8 | 8.400 | 8.219 | 79.86 | 10377.0 | PASS |
| micro-60 | 60 | 10 | 10.553 | 10.328 | 86.52 | 27882.343000000004 | PASS |
| micro-100 | 100 | 12 | 12.780 | 12.453 | 92.24 | 42236.590000000004 | PASS |
| micro-180 | 180 | 15 | 16.318 | 16.203 | 103.98 | 74412.69 | PASS |
| micro-300 | 300 | 8 | 8.591 | 8.594 | 83.78 | 6729.0 | PASS |

- `micro-20/60/100/180` và `ga-20`, `cp-20`: benchmark có sẵn `benchmark_instances_v5_h60/AL13_nNNN_i01.json`. Weekly policy áp vào **bản sao test**, không sửa file benchmark.
- `cp-10`, `micro-300`: dữ liệu tổng hợp từ fixture V5, processing time nhỏ, horizon 60 ngày; chỉ đánh giá cơ chế/cỡ cấu trúc, không dự báo hiệu năng nhà máy.
- Micro stress dùng population 8, seed 42, 1 run, max_decodes 30.000. Không so chất lượng các nghiệm này với production population 24 và ngân sách 900/1.200 giây.
- CP-SAT 20 jobs trả **UNKNOWN**, chưa có nghiệm trong budget 5s. Đây không phải chứng minh vô nghiệm và không tự động là bug.
- Workbook V5 thật upload ~7.74s; chọn 10 jobs có quantity chia hết cho 10, solver ~4.01s, 621 events, makespan 6801.494 phút, accept/reopen/export thành công.
- Excel 20.000 rows: 4.173s export, CPU 4.031s, peak 208.64 MiB (bao gồm đọc lại workbook), 1,236,692 bytes.
- Browser suite duration không phải FPS hoặc p95 interaction latency. Chưa thu trace CPU/RAM browser theo thời gian.

Nguồn: [stress-results.json](.tmp-qa/2026-10-24/stress-results.json), [workbook-results.json](.tmp-qa/2026-10-24/workbook-results.json), [export-stress-results.json](.tmp-qa/2026-10-24/export-stress-results.json).

## 5. Independent correctness audit

Checker riêng ở [independent.py](.tmp-qa/2026-10-24/independent.py) không gọi validator của app. Nó đọc input + raw schedule, dựng resource intervals và quantity accounting, kiểm toàn bộ precedence và tính lại Cmax. Tolerance thời gian 0,005 phút để phù hợp sai số số thực/scale của mô hình.

- Saved run `b13bc76503bb49f481ee9f2b6a278a8b`: **180 jobs / 11.287 events / 0 violations**.
- Saved run `3dc65aeb040d42d7b9a07f115a00b888`: **180 jobs / 11.287 events / 0 violations**.
- Saved standardized events không chứa đầy đủ OIL phase/batch composition. Audit tái decode **chromosome đã lưu**, bằng Reference Decoder trên frozen snapshot, rồi xác nhận toàn bộ job/operation/start/finish multiset khớp lịch đã lưu đến 5 chữ số thập phân phút trước khi dùng raw events cho phase checks. Đây là deterministic replay, **không chạy optimization**; không đánh giá được mọi field raw đã bị bỏ khỏi standardized output chỉ từ file Excel.
- CP-SAT synthetic 10 jobs và mọi nghiệm feasible của stress đều qua checker độc lập.
- 7 negative controls cho checker: OIL quantity, OIL phase, batch composition, precedence, resource overlap, Cmax, setup đều được phát hiện.
- Fast/Reference: 15 repaired permutations (seed 42/43/44 trên 20/60/100/180/300 jobs): 11 feasible comparisons và 4 cùng thất bại; thêm incumbent feasible 100 và 180 jobs cho 2 comparisons. Không tuyên bố chứng minh tương đương cho mọi chromosome.

Bằng chứng: [independent-results.json](.tmp-qa/2026-10-24/independent-results.json), [decoder-feasible-results.json](.tmp-qa/2026-10-24/decoder-feasible-results.json), [reproducibility.json](.tmp-qa/2026-10-24/reproducibility.json).

## 6. Bug register — findings only

### QA-01 — HIGH — Missing mandatory precedence accepted as Valid

- **Observed:** Xóa cạnh cuối `J004 / SEAL → VISUAL` trong fixture `backend.tests.test_v5.instance()`, upload JSON trả **200 / validation_status=Valid**. Gọi decoder trên input đó gây **KeyError ('J004', 'SEAL', 'VISUAL')**.
- **Cause:** `v5_service.validate` kiểm coverage operations/OIL nhưng không kiểm đầy đủ mandatory precedence; constructor decoder lưu dictionary mà không kiểm đủ cạnh trước khi schedule.
- **Reproduce:** Xóa `data['precedence'][-1]`, POST `/api/input/upload`, sau đó gọi decode hoặc lên lịch input tương ứng trong database test.
- **Impact:** Input sai vượt validation, lỗi chỉ xuất hiện ở giai đoạn scheduling.
- **Recommended fix:** Kiểm đủ/duy nhất các cạnh bắt buộc, job/op reference, relation type và lag finite trước persist/queue. Bổ sung regression ở upload và schedule validate. Không cần đổi solver algorithm.

### QA-02 — HIGH — Unsupported/cyclic precedence silently ignored

- **Observed:** Thêm `{job_id:'J001', pred:'VISUAL', succ:'2B', lag_min:1, relation_type:'FS'}` vào fixture hợp lệ. Upload trả **200**. Reference decoder tạo lịch và app standardizer vẫn trả **feasible=true**. Independent checker phát hiện vi phạm chính cạnh này.
- **Cause:** Decoder và output validator dùng chuỗi cạnh cố định; cạnh khác trong input không được xử lý hoặc bị từ chối.
- **Reproduce:** Payload trên + decode thứ tự J001,J002,J003,J004 + `standardize('SS Micro-GA Fast', raw, data)`; xem `cyclic_precedence_proof`.
- **Impact:** App có thể báo lịch hợp lệ dù vi phạm ràng buộc người dùng đưa vào. Chưa thấy lỗi này trong hai saved runs được kiểm tra; đó không loại trừ lỗi validator.
- **Recommended fix:** Với mô hình V5 cố định, **reject rõ ràng cạnh/relation không hỗ trợ và chu trình**; kiểm mọi cạnh được chấp nhận khi validate output. Không tự mở rộng toán học solver trong một bản vá validation.

### QA-03 — HIGH — Negative setup treated as zero setup

- **Observed:** Đặt `setup_matrix['A']['B']=-3`; upload **200**, validation errors rỗng. Decoder bỏ setup này và standardized result **feasible=true**, không có SETUP event.
- **Cause:** Thiếu kiểm `setup_matrix` finite/nonnegative; logic `setup_min > EPS` xem giá trị âm giống không cần setup.
- **Reproduce:** Sửa đúng một giá trị như trên trong fixture V5 4 jobs, upload/decode/standardize.
- **Impact:** Input hỏng có thể làm mất setup và giảm Cmax giả tạo.
- **Recommended fix:** Reject setup âm/nonfinite, kiểm matrix coverage theo product; vẫn cho phép 0 hợp lệ. Test case ở upload, validate và output boundary.

### QA-04 — MEDIUM — Negative 2C processing time accepted at input

- **Observed:** `batching.processing_min_per_batch=-1`; upload **200 / Valid**. Decoder tạo **3 batch intervals có completion < start**; output standardizer sau đó từ chối (`feasible=false`).
- **Cause:** Input validation kiểm batch size/exact-ten nhưng chưa kiểm processing duration dương, hữu hạn.
- **Reproduce:** Sửa field trên trong fixture, upload và decode. Xem `invalid-impact.json`.
- **Impact:** Dữ liệu sai được trình bày là Valid và lãng phí run; trong ca này output guard đã ngăn lịch sai được chấp nhận.
- **Recommended fix:** Check batch processing >0 và finite trước persist/start; báo lỗi field cụ thể.

### QA-05 — MEDIUM — Reported workstation/resource assignment corruption not rejected

- **Observed:** Lấy raw schedule hợp lệ, sửa PRE `Station_ID` thành `WS_FINAL`, hoặc sửa `Resource_Units` thành `OP_FINAL`; cả hai vẫn được standardizer đánh dấu **feasible=true**.
- **Cause:** Physical validator dựng resource use từ schema theo operation, nhưng phần standardization dựng workstation/resource text từ raw output, không đối chiếu hai bên.
- **Reproduce:** Chạy fixture V5 decoder, sửa một field của PRE row, gọi standardize. Xem `output_mutations` trong reliability evidence.
- **Impact:** Gantt/table/Excel có thể biểu diễn assignment không đúng trong trường hợp output sai. Đây là corruption test có chủ ý; chưa ghi nhận solver hiện tại tự sinh assignment lỗi trên các case hợp lệ đã chạy.
- **Recommended fix:** Validate assignment raw so với resource mapping/cycle phase trước khi xuất, hoặc chuẩn hóa assignment từ schema đã xác minh và reject mâu thuẫn. Bổ sung corruption regressions cho mọi event type.

### Negative-input results chi tiết

| Ca | HTTP | QA status |
|---|---:|---|
| duplicate_job | 400 | PASS |
| negative_processing | 400 | PASS |
| oil_over_capacity | 400 | PASS |
| missing_required_resource | 400 | PASS |
| overlapping_calendar | 400 | PASS |
| negative_batch_duration | 200 | FAIL |
| negative_setup_duration | 200 | FAIL |
| missing_precedence | 200 | FAIL |
| nonfinite_precedence | 400 | PASS |
| cyclic_extra_precedence | 200 | FAIL |

Bằng chứng: [reliability-results.json](.tmp-qa/2026-10-24/reliability-results.json), [invalid-impact.json](.tmp-qa/2026-10-24/invalid-impact.json), [independent-results.json](.tmp-qa/2026-10-24/independent-results.json).

## 7. Reliability và các giới hạn cần hiểu đúng

- Hai request chạy solver được chấp nhận, tiến trình run lần lượt `running/queued → completed/running → completed/completed`; đó là queue tuần tự, không phải hai solver chạy song song.
- Đồng thời 2 Apply requests cho cùng accepted run trả 200 và chỉ có 4 execution jobs, không nhân đôi.
- Sửa priority tạo input revision; bytes frozen snapshot và saved standardized result không đổi.
- Excel bảo toàn job/product/operation/workstation; datetime Excel round-trip có độ chính xác millisecond, không phải microsecond; float có giới hạn precision của Excel.
- Historical Gantt fixture có weekly-rule conflicts là trạng thái dữ liệu cũ đã biết. Calendar Review báo chúng và không sửa lịch cũ; không được suy rằng tất cả dữ liệu lịch sử tuân thủ policy mới.
- GA không cung cấp chứng minh optimality; chấp nhận một nghiệm `Feasible` không được đổi cờ `proven_optimal` thành true.
- Calendar generated/repeated từ source có thể thiếu metadata lý do break/downtime; UI phải tiếp tục ghi “Reason not recorded” khi thiếu.

## 8. Remaining risks / unexecuted coverage

| Hạng mục | Status | Lý do / bước tiếp theo |
|---|---|---|
| 300/600/900s full wall-clock endurance | NOT TESTED | Đã kiểm truyền cấu hình và short deadlines, chưa chạy mới đủ từng thời lượng |
| 300+ jobs từ dữ liệu nhà máy thật | BLOCKED | Benchmark hiện có chỉ tới 180; cần fixture có release/quantity/calendar đại diện |
| Full-search optimum 180 jobs | NOT TESTED | Không chạy exact proof dài; GA feasible không thể thay thế optimality certificate |
| 300+ many OIL cycles / 20k raw solver events | NOT TESTED | Synthetic 300 nhẹ; Gantt/Excel 20k dùng synthetic/replay riêng |
| Multi-hour soak, browser memory leak | NOT TESTED | Cần test dài có memory time series và interaction latency |
| High concurrency / multiple users / OS restart mid-save | NOT TESTED | Chỉ kiểm concurrency nhỏ, existing recovery tests; không fault-inject kill production |
| Windows EXE packaging, clean machine install | NOT TESTED | Không thuộc artifact đã build/test trong lượt này |
| Legacy monolithic `tests/e2e/workflow.spec.ts` | NOT TESTED | Config có `reuseExistingServer` ở 8000/8443, có nguy cơ chạm dữ liệu thật; thay bằng isolated backend/browser/API coverage đã nêu, không tự chạy config đó |

## 9. Lệnh và evidence để tái hiện

Chạy tại workspace, dùng **database test riêng**, không đổi `SCHEDULING_DATA_DIR` sang `backend/storage`:

```powershell
.\.venv\Scripts\python.exe -B -m pytest backend/tests -q --basetemp=.tmp-qa/2026-10-24/pytest-temp -o cache_dir=.tmp-qa/2026-10-24/pytest-cache --junitxml=.tmp-qa/2026-10-24/backend.xml
.\run-frontend.cmd check
& '.\.tools\node-v22.23.3-win-x64\node.exe' node_modules/vite/bin/vite.js build --outDir .tmp-qa/2026-10-24/dist
.\.venv\Scripts\python.exe -B -m backend.tests.prepare_reopen_fixture
& '.\.tools\node-v22.23.3-win-x64\node.exe' node_modules/@playwright/test/cli.js test --config=.tmp-qa/2026-10-24/browser.config.ts
.\.venv\Scripts\python.exe -B -u .tmp-qa/2026-10-24/stress.py
.\.venv\Scripts\python.exe -B -u .tmp-qa/2026-10-24/reliability.py
.\.venv\Scripts\python.exe -B -u .tmp-qa/2026-10-24/workbook_flow.py
.\.venv\Scripts\python.exe -B -u .tmp-qa/2026-10-24/audit_saved.py
```

- [Backend JUnit](.tmp-qa/2026-10-24/backend.xml), [backend log](.tmp-qa/2026-10-24/backend.log).
- [Browser JSON](.tmp-qa/2026-10-24/browser-results.json), [browser log](.tmp-qa/2026-10-24/browser.log); screenshots của test cũ ở `.tmp-verification/`.
- [Typecheck](.tmp-qa/2026-10-24/typecheck.log), [isolated build](.tmp-qa/2026-10-24/build-isolated.log).
- [Reliability confirmed log](.tmp-qa/2026-10-24/reliability-confirmed.log), [workbook confirmed log](.tmp-qa/2026-10-24/workbook_flow-confirmed.log).
- Raw solver outputs, copied inputs và per-case logs ở `.tmp-qa/2026-10-24/*-raw.json`, `*-input.json`, `*-metrics.json`.

Một số lần thử đầu lỗi ở **QA harness**, không phải ứng dụng: Win32 memory-counter thiếu ctypes argtypes; checker giả định CP-SAT raw có Event_ID; đường dẫn isolated build và snapshot QA. Chỉ QA harness được chỉnh, các case liên quan đã chạy/kiểm lại. CP-10 audit được chạy lại trên raw output đã lưu, không rerun solver. Kết quả cuối và log nguyên nhân được lưu; không tính harness error vào app bugs.

## 10. Production-readiness assessment và thứ tự xử lý đề xuất

1. **Ưu tiên HIGH:** chặn input precedence thiếu/ngoài mô hình/chu trình và setup âm (QA-01/02/03). Không để “Valid/Feasible” mang ý nghĩa sai.
2. Chặn 2C duration âm và kiểm raw assignment (QA-04/05), bổ sung regression dựa trên ca tái hiện.
3. Sau bản sửa được phê duyệt, chạy lại existing suites + negative cases + independent schedule checker; xác nhận các lỗi mới từ FAIL thành PASS.
4. Trước rollout không giám sát: benchmark đại diện 300+ jobs, wall-clock endurance 300/600/900s, soak/multi-user và thử restore từ backup.

**Thesis demo có giám sát:** các workflow hợp lệ được thử đã hoạt động; nên dùng dataset đã xác minh và công bố rõ giới hạn heuristic/validation còn tồn tại. **Production không giám sát:** chưa đủ điều kiện khuyến nghị vì các HIGH findings. Báo cáo này chỉ ghi nhận và đề xuất; không thực hiện bất kỳ bản vá production nào.
