from backend.services.instance_service import OPS

def instance(n=2):
    jobs = [dict(job_id=f"J{i+1:03}",product_code="AL13-Test",batch_qty=10,priority=1,
                 release_min=0.0,element_ready_min=0.0,loopset_release_min=0.0,due_min=600.0) for i in range(n)]
    return {
        "metadata":{"schema_version":"AL13_BENCHMARK_V1","instance_name":f"TEST_n{n}",
                    "size":n,"origin_datetime":"2026-09-01 08:00:00",
                    "horizon_end_datetime":"2026-09-05 12:00:00","horizon_end_min":6000.0,
                    "resource_constraints_redundant_under_station_capacity":True},
        "jobs":jobs,
        "station_master":[dict(station_id=op+"_01",station_group=op,capacity=1,time_multiplier=1) for op in OPS],
        "operation_options":[dict(job_id=j["job_id"],operation_code=op,station_id=op+"_01",processing_min=5.0+i%3,eligible=1) for i,j in enumerate(jobs) for op in OPS],
        "precedence":[dict(job_id=j["job_id"],pred=a,succ=b,lag_min=1.0) for j in jobs for a,b in zip(OPS,OPS[1:])],
        "station_windows":{op+"_01":[dict(window_id=op+"_W1",start_min=0.0,end_min=6000.0,calendar_date="2026-09-01",shift_id="A")] for op in OPS},
        "resource_requirements":{},"resource_units_by_type":{},"base_resource_capacity":{},"resource_capacity_by_shift":[]}

def make_workbook():
    import io
    from openpyxl import Workbook
    wb=Workbook()
    wb.remove(wb.active)
    data=instance(10)
    def sheet(name,columns,rows):
        ws=wb.create_sheet(name);ws.append(columns)
        for row in rows: ws.append(row)
    sheet("02_Station_Master",["Station_ID","Station_Group","Capacity","Status","Time_Multiplier"],
          [[s["station_id"],s["station_group"],1,"ACTIVE",1] for s in data["station_master"]])
    sheet("05_Shift_Calendar",["Calendar_Date","Shift_ID","Shift_Start","Break_Start","Break_End","Shift_End","Working_Day"],
          [["2026-09-01","A","2026-09-01 08:00","2026-09-01 12:00","2026-09-01 13:00","2026-09-01 18:00",1]])
    sheet("06_Station_Downtime",["Station_ID","Downtime_Start","Downtime_End"],[])
    sheet("07_Resource_Master",["Resource_Type","Base_Capacity"],[])
    sheet("07B_Resource_Units",["Resource_Type","Resource_Unit_ID","Status"],[])
    sheet("08_Resource_Req",["Operation_Code","Resource_Type","Required_Qty"],[])
    sheet("08B_Base_Resource_Shift_Cap",["Calendar_Date","Shift_ID","Resource_Type","Available_Qty"],[])
    sheet("09_Jobs_30D",["Job_ID","Product_Code","Batch_Qty","Priority","Release_DateTime","Element_Ready_Time","Loopset_Release_Time","Base_Due_DateTime"],
          [[j["job_id"],"AL13-Test",10,1,"2026-09-01 08:00","2026-09-01 08:00","2026-09-01 08:00","2026-09-01 18:00"] for j in data["jobs"]])
    sheet("09B_Due_Date_Scenarios",["Job_ID","Due_Scenario_ID","Due_DateTime"],
          [[j["job_id"],"BASE","2026-09-01 18:00"] for j in data["jobs"]])
    sheet("11B_Job_Precedence",["Job_ID","Predecessor_Operation","Successor_Operation","Minimum_Lag_Min"],
          [[r["job_id"],r["pred"],r["succ"],r["lag_min"]] for r in data["precedence"]])
    sheet("12_Job_Op_Options",["Job_ID","Operation_Code","Station_ID","Station_Group","Eligible","Deterministic_Processing_Time_Min"],
          [[r["job_id"],r["operation_code"],r["station_id"],r["operation_code"],1,r["processing_min"]] for r in data["operation_options"]])
    output=io.BytesIO();wb.save(output)
    return output.getvalue()
