# Bước 1 — Thử nghiệm thứ tự jobs và cặp 5+5

Ngày 10/11/2026. Chỉ tạo experiment, không chỉnh production, input, lịch đã lưu, objective hoặc calendar. Không tích hợp kết quả thử vào App.

## Kết luận

**PASS về tính hợp lệ và bảo toàn; CHƯA ĐẠT mục tiêu giảm số jobs trễ.** Local search rất ngắn quanh nghiệm hiện có chỉ cải thiện Cmax tối đa 49,775 phút (0,074648%); không lịch cuối nào giảm số jobs trễ dưới 130. Nghiệm có Cmax nhỏ nhất có 131 jobs trễ. Chưa đủ bằng chứng đưa các moves này vào thuật toán production hoặc tuyên bố phương án thất bại trên mọi ngân sách/instance.

## Protocol thực tế

- Run nguồn: `236e9e6fbb1044fab37acb6064f8b4b2`, 166 jobs, GA đã lưu.
- Đọc kết quả từ bản snapshot immutable đã tạo trong experiment Due Date Sensitivity. Đọc đúng frozen input theo `instance_path` và kiểm tra SHA-256 khớp run. Có bản copy input tại `run-20261110-01/frozen.json`, không regenerate.
- Giữ nguyên release, due date, processing, capacity, resources, setup, OIL, exact-10/5+5, calendar và 60-day horizon.
- Decoder: `backend.solver_v5.benchmark_common_fast.V5ScheduleDecoder`, nguyên bản production. Repair: `repair_product_change_remainders` hiện có của base V5 GA.
- Mỗi arm bắt đầu từ **cùng chromosome tốt nhất đã lưu**, không cho phương án thử bắt đầu từ lịch tốt hơn control.
- Seeds: 42, 1051, 2060; 40 unique full search decodes/arm, gồm 1 decode incumbent đầu. Tổng **480 search decodes**. Candidate trùng không decode và không tiêu budget; candidate decode infeasible vẫn tiêu budget. Có giới hạn generation attempts để tránh lặp cache.
- Chấp nhận thay incumbent **chỉ khi Cmax giảm hơn 1e-9**. Không ưu tiên tardiness hoặc chấp nhận đi ngang trong experiment này. Điều này giữ duy nhất mục tiêu Cmax, nhưng cũng hạn chế khả năng đi qua plateau.
- Ngoài search: 1 lần re-decode baseline dùng chung, 12 lần tái dựng lịch cuối, 13 lần kiểm tra bằng validator V5 hiện có. Các bước kiểm tra không tính vào budget search. Runtime từng arm trong CSV tách search và end-to-end.
- Chạy serial, không cap theo wall-clock. Đây là so sánh cùng số evaluations của **local neighborhoods**, không phải full SS Micro-GA benchmark, không phải so sánh GA–MLPSO, và không thay thế experiment full-run nhiều seeds.

| Arm | Thay đổi riêng trong experiment |
|---|---|
| control | Swap/insert ngẫu nhiên; vẫn qua repair và decoder giống các arm khác. |
| release | Đổi hai jobs cùng sản phẩm khi readiness input bị đảo thứ tự; giữ nguyên chuỗi product slots. Readiness = max(release, material, loopset), không thay cách decoder xác định thời điểm khả thi. |
| pair | Di chuyển hai remainder jobs cùng Batch Key đến cạnh nhau. Không sửa qty/key hay bắt decoder dùng một pairing mới. Decoder vẫn quyết định batch vật lý. |
| combined | Chọn ngẫu nhiên release/pair với xác suất 50/50 mỗi lần sinh candidate. |

Nếu không còn targeted move, script có fallback random và ghi rõ số lần. Trong lượt chạy này không arm nào dùng fallback. Moves đều giữ đủ permutation trước và sau repair. Không tuyên bố adjacency tự bảo đảm feasibility hoặc đúng hạn.

## Baseline được tái lập trước thử nghiệm

- Cmax: **66.679,265 phút**.
- Tardy jobs: **130/166**.
- Total tardiness: **2.288.702,982 phút**.
- Total flow: **3.194.177,601 phút**.
- 115 jobs đã quá hạn trước lúc bắt đầu PRE.
- Release → PRE trung bình: 280,821 giờ.
- VISUAL → completion 2C dài nhất: 610,300 giờ.

Re-decode cùng chromosome và đối chiếu Cmax/tardiness/flow/count với App đều khớp trong tolerance 1e-5 phút. Lịch baseline được validator V5 chấp nhận.

## Kết quả thực tế

| Seed | Arm | Cmax phút | Giảm Cmax so baseline | Jobs trễ | Total tardiness phút |
|---:|---|---:|---:|---:|---:|
| 42 | control | 66.679,265 | 0% | 130 | 2.288.702,982 |
| 42 | release | 66.629,490 | 0,074648% | **131** | 2.326.537,354 |
| 42 | pair | 66.679,265 | 0% | 130 | 2.288.702,982 |
| 42 | combined | 66.668,925 | 0,015507% | 130 | 2.294.034,652 |
| 1051 | control | 66.679,265 | 0% | 130 | 2.288.702,982 |
| 1051 | release | 66.674,095 | 0,007754% | 130 | 2.289.256,707 |
| 1051 | pair | 66.674,095 | 0,007754% | 130 | 2.287.047,966 |
| 1051 | combined | 66.679,265 | 0% | 130 | 2.288.702,982 |
| 2060 | control | 66.679,265 | 0% | 130 | 2.288.702,982 |
| 2060 | release | 66.659,896 | 0,029048% | 130 | 2.292.381,029 |
| 2060 | pair | 66.674,095 | 0,007754% | 130 | 2.287.453,478 |
| 2060 | combined | 66.674,095 | 0,007754% | 130 | 2.289.492,424 |

Tất cả 12 lịch cuối đủ jobs, re-decode khớp Cmax và validator V5 chấp nhận. Candidate trung gian infeasible: control 7/120 search decodes, các arm mục tiêu 0/360; không đưa candidate infeasible vào incumbent. Đây không phải bằng chứng targeted moves luôn feasible trên mọi instance.

115 jobs trễ trước PRE không giảm ở bất kỳ lịch cuối nào. Thời gian chờ từ release đến PRE vẫn xấp xỉ 280,8 giờ. Một lịch release/seed42 còn tăng max VISUAL→2C lên 731,617 giờ. Vì vậy không thể suy từ Cmax giảm sang lịch giao hàng tốt hơn.

`lowest_tardy_evaluated` trong CSV chỉ là diagnostic từ metrics của candidate trung gian, không phải kết quả được chọn. Chúng không được tái dựng/validator riêng trong lượt này, không dùng để tuyên bố đã có lịch cuối giảm jobs trễ. Experiment cố ý không nhận move ngang Cmax chỉ để giảm tardiness.

## Diễn giải và giải pháp tiếp theo

1. **Chưa đưa release/pair moves này vào production.** Tác động Cmax quá nhỏ và không đạt mục tiêu tardy jobs. Không thay deadline để che kết quả không thuận lợi.
2. Giả thuyết “release và cặp 5+5 đáng kiểm tra” chưa bị bác bỏ hoàn toàn: đây chỉ là 39 proposals mới sau incumbent/arm, bắt đầu từ nghiệm GA đã chạy 600 giây. Một swap/insert nhỏ và strict Cmax descent có thể không vượt được plateau hay thay đổi các product blocks lớn.
3. Nếu tiếp tục mà vẫn giữ Cmax, bước hợp lý là một experiment khác: khởi tạo release-aware/pair-aware ngay từ đầu, hoặc neighborhood nhiều bước/plateau exploration có ngân sách rõ ràng. Phải giữ cùng input/seeds/tổng compute và so với control đầy đủ; chưa thực hiện hay sửa thuật toán trong lượt này.
4. Nếu mục tiêu chính là giảm số jobs trễ, cần thống nhất rõ có cho phép secondary tardiness criterion hay objective khác không. Không tự thêm tie-break tardiness vào Cmax-only rồi gọi là “không đổi logic”. Không có cam kết rằng chỉ tăng runtime sẽ giảm tardy jobs.
5. Tiếp tục xác minh due-date provenance và giới hạn công suất đã phát hiện. Chỉ các thử nghiệm lịch mới tốt hơn và được validate mới là bằng chứng cải thiện scheduling; Due Date Extension chỉ là sensitivity của KPI.

## Kiểm thử và bảo toàn

- 5/5 unit tests PASS: permutation, product slots, same-key pairing, no-target behavior, deterministic moves và không mutate input.
- 12/12 lịch cuối PASS independent V5 validation; baseline PASS.
- Cùng budget 40/arm, đủ 480 search calls; trace incumbent Cmax không tăng.
- Hash production source giữ nguyên, input nguồn và snapshot DB giữ nguyên. Live database không được mở hoặc ghi trong lượt này; không kết nối API App.
- Không chạy full backend/UI suites vì không sửa production. Không có full benchmark mới và chưa kiểm tra statistical significance hoặc reproducibility của full GA.

Artifacts: [script](run.py), [tests](test_moves.py), [unit test log](unit-test-results.txt), [CSV](run-20261110-01/results.csv), [manifest](run-20261110-01/manifest.json), [trace](run-20261110-01/traces.json), [baseline](run-20261110-01/baseline.json). Mỗi arm/seed có JSON chromosome và raw schedule riêng.
