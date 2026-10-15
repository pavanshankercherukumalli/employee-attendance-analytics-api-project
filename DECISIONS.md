# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?
ans:I created a unique index on employees.emp_code to prevent duplicate employee codes. I created a compound unique index on attendance_logs.emp_code and date to prevent duplicate attendance records for an employee on the same date. I also created a compound index on date and emp_code to support attendance listing and sorting. I considered adding more indexes but avoided unnecessary ones because they increase storage and write costs.

2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.
ans:If two identical punch-in requests arrive together, both could initially find no attendance record. The unique compound index on emp_code and date ensures that only one record can be inserted. The other request should fail with a duplicate-key error. This prevents duplicate records, although the API should handle that error cleanly.

3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?
 The late leaderboard sorts employees by total late minutes in descending order and uses employee code as a secondary sort. If employees are tied at the limit, it includes employees sharing the cutoff rank, so the response can contain more entries than the requested limit.

4. **Headcount.** How does your department summary make sure employees with zero logs are counted?
ans:Headcount: The department summary starts from employees and looks up their attendance records. This means employees with no attendance logs are still included in their department's headcount.

5. **One thing you would change** if this had to serve 100x the data.
If the data grew 100 times, I would improve pagination and query performance, review indexes using execution statistics, and consider caching frequently requested analytics.by scaling.