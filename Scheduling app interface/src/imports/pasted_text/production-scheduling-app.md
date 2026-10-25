Design a complete modern Production Scheduling web application for a manufacturing environment. Do not ask follow-up questions. If any information is missing, make sensible professional assumptions and generate the full UI directly.

The application should be easy to use for production planners, visually modern, clean, professional, and suitable for an industrial scheduling system.

The MAIN PURPOSE of the application is:

Create production schedules automatically, select the most appropriate optimization method based on problem size, and show the final result primarily as a Gantt Chart.

DESIGN STYLE

Use a modern blue industrial SaaS style.

Primary blue: #2563EB
Dark blue: #1E3A8A
Light blue: #EFF6FF
Background: #F8FAFC
White cards
Dark navy text: #0F172A
Secondary gray text: #64748B
Green: completed / successful
Orange: warning / delayed risk
Red: critical / delayed
Font similar to Inter
Border radius: 8–12px
Minimal modern icons
Subtle borders and shadows
Plenty of whitespace
Avoid excessive gradients
Use Auto Layout
Create reusable components

Desktop-first responsive design for approximately 1440px width.

The UI must feel like real production scheduling software, not an academic dashboard.

MAIN NAVIGATION

Use a left sidebar with:

Dashboard
Jobs
Scheduling
Workstations
Analytics
Settings

Put the user profile at the bottom.

Keep the navigation simple.

CORE BUSINESS FLOW

Create this complete prototype flow:

Login

↓

Dashboard

↓

Manage Production Data

↓

Create Schedule

↓

Select Jobs

↓

Validate Input Data

↓

Automatically Detect Problem Size

↓

If Small Scale:

Run MILP

If Large Scale:

Run GA and PSO

↓

Compare Scheduling Results

↓

Automatically Select Best Schedule

↓

Generate Final Schedule

↓

Display Gantt Chart

↓

User Reviews Schedule

↓

Save / Apply Schedule

↓

Production Monitoring

↓

Analytics

1. LOGIN PAGE

Create a clean login screen.

Include:

Application logo
Application name: Smart Production Scheduling
Email
Password
Sign In

Use a simple blue and white layout.

2. DASHBOARD

Dashboard should provide a quick production overview.

KPI cards:

Total Jobs
Unscheduled Jobs
Scheduled Jobs
In Progress
Completed
Delayed

Additional KPI:

Current Makespan
Workstation Utilization
On-time Completion Rate

Include:

Job Status chart
Workstation Utilization chart
Upcoming Jobs
Recent Scheduling Activity

Main CTA:

Create Schedule

This button should be visually prominent.

3. JOB MANAGEMENT

Create a production jobs table.

Columns:

Job ID
Product
Batch Quantity
Priority
Release Time
Material Ready Time
Due Date
Status
Actions

Include:

Search
Status Filter
Priority Filter
Date Filter
Add Job
Edit Job
View Job
Delete Job

Status:

Unscheduled
Scheduled
In Progress
Completed
Delayed

Use clean colored status chips.

4. CREATE / EDIT JOB

Create a simple form.

Fields:

Job ID
Product Code
Batch Quantity
Priority
Release Date & Time
Material Ready Time
Due Date & Time
Notes

Buttons:

Cancel
Save Job
Save & Schedule

Do not make the form overly complicated.

5. CREATE SCHEDULE

This page starts the scheduling process.

Show a table of available Unscheduled Jobs.

Allow:

Select individual jobs
Select All
Filter jobs
Search jobs

Show:

Selected Jobs: 50

Include:

Scheduling Start Date
Scheduling End Date
Scheduling Horizon

Main button:

Continue to Scheduling

6. SCHEDULING CONFIGURATION

Keep this screen simple.

Main scheduling mode:

Optimization Mode: AUTO

Explain briefly:

“The system automatically selects the appropriate optimization method according to problem size.”

Do NOT require normal users to manually select MILP, GA, or PSO.

Display:

Number of selected jobs
Scheduling horizon
Objective

Objective:

Minimize Makespan

Add:

Advanced Settings

collapsed by default.

Advanced Settings may contain:

Small-scale threshold
Maximum computation time
Makespan tolerance

Main CTA:

Run Smart Scheduling

7. INPUT VALIDATION

After the user clicks Run Smart Scheduling, validate:

Job information
Processing time
Eligible workstation
Operation sequence
Release time
Material availability
Working calendar
Resource availability

If validation passes:

Show:

All scheduling data validated successfully

Continue automatically.

If validation fails:

Show a clean validation panel.

Example:

3 issues found

Job J025 missing processing time
Job J031 missing Due Date
Workstation LASER_02 unavailable

Buttons:

Fix Data
Cancel
8. AUTOMATIC ALGORITHM SELECTION

This is system logic, not a complicated user interface.

Display a simple processing visualization:

Analyzing Scheduling Problem

Example:

Selected Jobs: 50

Problem Size: Large Scale

Optimization Strategy:

GA + PSO

For small-scale problems:

MILP

Use this logic:

Small Scale:

MILP

Large Scale:

GA + PSO

The threshold should be configurable internally.

9. SCHEDULING PROCESSING SCREEN

Create a modern loading / progress screen.

Example:

Optimizing Production Schedule

Status:

Running optimization algorithms...

For small scale:

MILP
Running...

For large scale:

show GA and PSO running in parallel:

GA
Running...
Best Makespan: 1,225 min

PSO
Running...
Best Makespan: 1,218 min

Also show:

Elapsed Time
Progress

Avoid showing too many technical parameters.

10. ALGORITHM DECISION LOGIC

The application should automatically select the final schedule.

Decision priority:

1. Feasible schedule

2. Lowest Makespan

3. If makespan results are within the acceptable tolerance, choose the method with shorter computation time

Do not create a manual ranking screen before the result.

The system should select automatically.

11. SCHEDULING RESULT

This is the MOST IMPORTANT SCREEN in the entire application.

Show at the top:

Scheduling Result

Summary cards:

Selected Method
Problem Size
Makespan
Computation Time
Number of Scheduled Jobs
Workstation Utilization

Example:

Selected Method
PSO

Problem Size
50 Jobs

Makespan
1,208 min

Computation Time
46.2 sec

Primary tabs:

Gantt View

Job Details

Algorithm Comparison

Gantt View must be selected by default.

12. GANTT CHART — PRIMARY OUTPUT

The Gantt Chart must occupy most of the result screen.

The final output of MILP, GA, and PSO must all use the SAME Gantt Chart interface.

Do not create separate result interfaces for each algorithm.

Vertical axis:

Workstations / Production Stages

Horizontal axis:

Time

Use production stages:

2B
PRE
FINAL
2A
LASER
SCALE
OIL
SEAL
PACK
2C

Each scheduled operation must appear as a horizontal task block.

Example:

Job J001 – PRE
Job J002 – PRE
Job J001 – FINAL
Job J004 – LASER

Each block represents:

Start Time → Finish Time

Different Jobs should be distinguishable using subtle task colors while maintaining the modern blue theme.

13. GANTT CONTROLS

Above the Gantt Chart include:

Previous
Today
Next
Date Range
Zoom In
Zoom Out
Day View
Week View
Filter by Job
Filter by Workstation

Actions on the right:

Run Again
Save Schedule
Export
14. GANTT TASK DETAILS

When the user clicks a task block, open a right-side details panel.

Display:

Job ID
Product
Operation
Workstation
Start Time
Finish Time
Processing Time
Priority
Status

Example:

Job J015

Operation
LASER

Workstation
LASER_01

Start
09:25

Finish
09:48

Processing Time
23 min

Clicking another task updates the side panel.

15. GANTT DESIGN REQUIREMENTS

The Gantt Chart should have:

Sticky workstation column
Sticky time header
Horizontal scrolling
Clear time grid
Clear row separation
Current time indicator
Compact rounded task blocks
Hover tooltip
Selected task highlight

Show Job ID inside the task block when enough space is available.

Do not overcrowd the Gantt Chart.

16. JOB DETAILS TAB

Create a detailed scheduling table.

Columns:

Job ID
Operation
Workstation
Start Time
Completion Time
Processing Time
Status

This table is secondary.

The Gantt Chart must remain the main output.

17. ALGORITHM COMPARISON TAB

For large-scale scheduling show:

Algorithm	Best Makespan	Mean Makespan	Runtime	Status
GA	1,219 min	1,230 min	57.8 s	Completed
PSO	1,208 min	1,217 min	46.2 s	Selected

Highlight the selected algorithm.

Display:

Selected Method: PSO

Reason: Lowest makespan and shorter computation time

If makespan results are within tolerance:

Example:

Selected Method: PSO

Reason: Makespan difference is within acceptable tolerance, while PSO has lower computation time.

For small scale:

Display:

Method: MILP
Makespan: 850 min
Runtime: 12.8 sec
Status: Proven Optimal
18. REVIEW SCHEDULE

User can:

Inspect the Gantt Chart
View Job Details
View Algorithm Comparison
Filter Jobs
Filter Workstations

Main actions:

Run Again

Save Schedule

If Run Again:

return to Scheduling Configuration.

19. SAVE SCHEDULE

When the user accepts the result:

Show a confirmation modal:

Save this production schedule?

Display:

Schedule ID
Algorithm Used
Makespan
Number of Jobs

Buttons:

Cancel
Save Schedule

After saving:

Schedule status:

Approved

20. APPLY SCHEDULE

After saving show:

Apply Schedule

When applied:

update Job status:

Unscheduled
↓
Scheduled
↓
In Progress
↓
Completed

If production becomes late:

Scheduled / In Progress
↓
Delayed
21. PRODUCTION MONITORING

Allow planners to compare planned schedule and actual progress.

Show the same Gantt Chart with task status.

Example:

Planned:

08:00 → 08:30

Actual:

08:05 → 08:38

Use status:

Scheduled
In Progress
Completed
Delayed
22. WORKSTATIONS

Create a workstation management page.

Show:

Workstation ID
Production Stage
Current Job
Availability
Utilization
Status

Status:

Available
Busy
Downtime

Clicking a workstation opens a detail drawer showing:

Current Job
Upcoming Jobs
Utilization
Planned schedule
23. ANALYTICS

Create a modern reporting page.

Metrics:

Makespan
Average Flow Time
Total Tardiness
On-time Completion
Workstation Utilization
Completed Jobs
Algorithm Runtime

Charts:

Makespan Trend
Workstation Utilization
Job Status
Production Completion Trend
GA vs PSO Performance

Include:

Date Filter
Export Report
24. SETTINGS

Keep the settings simple.

Sections:

General Settings
Scheduling Settings
Optimization Settings
Notification Settings

Optimization Settings may include:

Small-scale Threshold
Maximum Runtime
Makespan Tolerance

These settings should not appear in the normal scheduling workflow.

UX PRINCIPLES

Follow these rules:

Minimize user clicks
Keep advanced algorithm settings hidden
Do not require users to understand MILP, GA, or PSO
Use Auto Optimization by default
Make Gantt Chart the primary scheduling result
Use clear primary and secondary buttons
Use realistic manufacturing data
Do not use lorem ipsum
Use reusable components
Use consistent spacing
Use consistent typography
Provide empty states
Provide loading states
Provide success messages
Provide error messages
Provide confirmation modals
Use tooltips when necessary
Keep tables readable
Use sticky table headers where appropriate
MOST IMPORTANT USER FLOW

Prioritize this prototype flow:

Dashboard

→ Create Schedule

→ Select Jobs

→ Run Smart Scheduling

→ Validate Data

→ Automatically Select MILP or GA + PSO

→ Generate Best Schedule

→ Gantt Chart

→ Review

→ Save

→ Apply

→ Monitor Production

→ Analytics

FINAL INSTRUCTION

Generate the complete UI directly. Do not ask me additional questions about layout, colors, components, page structure, charts, or algorithm settings.

If something is unspecified, make a professional assumption.

Prioritize:

1. Ease of use

2. Scheduling workflow

3. Gantt Chart clarity

4. Modern blue visual design

5. Minimal user interaction

The application should look like a realistic industrial production scheduling product suitable for a university thesis demonstration.