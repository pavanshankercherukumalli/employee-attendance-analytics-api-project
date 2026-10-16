"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from pymongo.errors import DuplicateKeyError 
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

db.employees.create_index("emp_code", unique=True)

db.attendance_logs.create_index(
    [("emp_code", 1), ("date", 1)],
    unique=True
)

db.attendance_logs.create_index([("date", -1), ("emp_code", 1)])

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    h, m = map(int, shift_start.split(":"))
    start = punch_in.replace(hour=h, minute=m, second=0, microsecond=0)
    minutes = int((punch_in - start).total_seconds() / 60)
    return minutes if minutes > 10 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    return round((punch_out - punch_in).total_seconds() / 3600, 2)


def compute_overtime(punch_out: datetime, shift_end: str, date_str: str) -> int:
    h, m = map(int, shift_end.split(":"))
    end = datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST)
    return max(0, int((punch_out - end).total_seconds() / 60))


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"

class AttendanceCorrectionIn(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"status": "ok"}



@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if not re.fullmatch(r"EMP\d{4,6}", body.emp_code):
        raise HTTPException(
            status_code=422,
            detail="emp_code must start with EMP followed by 4 to 6 digits"
        )

    if body.shift_start == body.shift_end:
        raise HTTPException(
            status_code=422,
            detail="shift_start and shift_end must be different"
        )

    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(status_code=409, detail="emp_code already exists")

    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
    db.employees.insert_one(doc)
    doc.pop("_id", None)
    return doc


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = 1, page_size: int = 20):
    q = {}
    if department:
        q["department"] = department
    skip =(page - 1) * page_size
    total = db.employees.count_documents({})
    items = list(db.employees.find(q, {"_id": 0}).skip(skip).limit(page_size))
    return {"items": items, "total": total, "page": page, "page_size": page_size}



@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.punched_at is not None:
        if not isinstance(body.punched_at, int) or isinstance(body.punched_at, bool) or body.punched_at < 100000000000:
            raise HTTPException(status_code=422, detail="Timestamps must be epoch milliseconds")
        try:
            ts = datetime.fromtimestamp(body.punched_at / 1000, tz=timezone.utc).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            raise HTTPException(status_code=422, detail="Invalid timestamp")
    else:
        ts = datetime.now(timezone.utc).replace(tzinfo=None)

    ts = ts.replace(microsecond=0)
    d = (ts.replace(tzinfo=timezone.utc) + timedelta(hours=5, minutes=30)).date().isoformat()

    if db.attendance_logs.find_one({"emp_code": body.emp_code, "date": d}):
        raise HTTPException(status_code=409, detail="Already punched in for this date")

    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Already punched in for this date")

    return {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": int(ts.replace(tzinfo=timezone.utc).timestamp() * 1000),
        "punch_out": None,
        "work_hours": None,
        "late_minutes": doc["late_minutes"],
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }



@app.post("/attendance/punch-out")
def punch_out(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.punched_at is not None:
        if not isinstance(body.punched_at, int) or isinstance(body.punched_at, bool) or body.punched_at < 100000000000:
            raise HTTPException(status_code=422, detail="Timestamps must be epoch milliseconds")
        ts = datetime.fromtimestamp(body.punched_at / 1000, tz=timezone.utc).replace(tzinfo=None)
    else:
        ts = datetime.now(timezone.utc).replace(tzinfo=None)

    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$ne": None, "$lte": ts},
            "punch_out": None,
        },
        sort=[("punch_in", -1)],
    )

    if record is None:
        closed = db.attendance_logs.find_one({
            "emp_code": body.emp_code,
            "punch_in": {"$ne": None, "$lte": ts},
            "punch_out": {"$ne": None},
        })
        if closed:
            raise HTTPException(status_code=409, detail="Already punched out")
        raise HTTPException(status_code=404, detail="No open punch-in record found")

    punch_in_time = record["punch_in"]

    if ts <= punch_in_time:
        raise HTTPException(status_code=422, detail="Punch-out must be after punch-in")

    if (ts - punch_in_time).total_seconds() > 24 * 60 * 60:
        raise HTTPException(status_code=422, detail="Punch-out cannot be more than 24 hours after punch-in")

    work_hours = round((ts - punch_in_time).total_seconds() / 3600, 2)

    from datetime import timedelta

    shift_end = datetime.combine(
        punch_in_time.date(),
        datetime.strptime(emp["shift_end"], "%H:%M").time()
    )

    if shift_end <= punch_in_time:
        shift_end += timedelta(days=1)

    overtime_minutes = 0
    if (ts - shift_end).total_seconds() >= 30 * 60:
        overtime_minutes = int((ts - shift_end).total_seconds() // 60)

    half_day = work_hours < 4.50

    result = db.attendance_logs.update_one(
        {"_id": record["_id"], "punch_out": None},
        {"$set": {
            "punch_out": ts,
            "work_hours": work_hours,
            "overtime_minutes": overtime_minutes,
            "half_day": half_day,
        }}
    )

    if result.modified_count == 0:
        raise HTTPException(status_code=409, detail="Attendance record was already punched out")

    return {
        "emp_code": record["emp_code"],
        "date": record["date"],
        "status": record["status"],
        "punch_in": int(punch_in_time.replace(tzinfo=timezone.utc).timestamp() * 1000),
        "punch_out": int(ts.replace(tzinfo=timezone.utc).timestamp() * 1000),
        "work_hours": work_hours,
        "late_minutes": record.get("late_minutes", 0),
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
        "history": record.get("history", []),
    }


@app.patch("/attendance/{emp_code}/{date}")
def correct_attendance(
    emp_code: str,
    date: str,
    body: AttendanceCorrectionIn
):
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format")

    record = db.attendance_logs.find_one(
        {"emp_code": emp_code, "date": date}
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    changes = {}
    updates = body.model_dump(exclude_unset=True)

    if not updates:
        raise HTTPException(status_code=422, detail="No changes provided")

    status = updates.get("status", record.get("status", "PRESENT"))
    punch_in_value = updates.get("punch_in", record.get("punch_in"))
    punch_out_value = updates.get("punch_out", record.get("punch_out"))

    if status in ("ABSENT", "LEAVE"):
        if "punch_in" in updates and updates["punch_in"] is not None:
            raise HTTPException(status_code=422, detail="ABSENT and LEAVE cannot have punch times")
        if "punch_out" in updates and updates["punch_out"] is not None:
            raise HTTPException(status_code=422, detail="ABSENT and LEAVE cannot have punch times")
        punch_in_value = None
        punch_out_value = None
    elif status in ("PRESENT", "WFH", "ON_DUTY"):
        if punch_in_value is None:
            raise HTTPException(status_code=422, detail="Presence status requires punch-in")
    else:
        raise HTTPException(status_code=422, detail="Invalid attendance status")

    def parse_instant(value):
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool) or value < 100000000000:
            raise HTTPException(status_code=422, detail="Timestamps must be epoch milliseconds")
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).replace(tzinfo=None)

    if "punch_in" in updates:
        punch_in_value = parse_instant(updates["punch_in"])
    if "punch_out" in updates:
        punch_out_value = parse_instant(updates["punch_out"])

    if status in ("PRESENT", "WFH", "ON_DUTY") and punch_in_value is None:
        raise HTTPException(status_code=422, detail="Presence status requires punch-in")

    if punch_in_value is not None and punch_in_value.date().isoformat() != date:
        raise HTTPException(status_code=422, detail="Punch-in must be on the attendance date")

    if punch_out_value is not None:
        if punch_in_value is None or punch_out_value <= punch_in_value:
            raise HTTPException(status_code=422, detail="Punch-out must be after punch-in")
        if (punch_out_value - punch_in_value).total_seconds() > 24 * 60 * 60:
            raise HTTPException(status_code=422, detail="Punch-out cannot be more than 24 hours after punch-in")

    proposed = {
        "status": status,
        "punch_in": punch_in_value,
        "punch_out": punch_out_value,
    }

    for field, value in proposed.items():
        if value != record.get(field):
            changes[field] = {
                "from": record.get(field),
                "to": value,
            }

    if not changes:
        raise HTTPException(status_code=422, detail="No changes detected")

    if punch_in_value is not None:
        late_minutes = compute_late_minutes(punch_in_value, emp["shift_start"])
    else:
        late_minutes = 0

    work_hours = None
    overtime_minutes = 0
    half_day = False

    if punch_in_value is not None and punch_out_value is not None:
        work_hours = round(
            (punch_out_value - punch_in_value).total_seconds() / 3600,
            2
        )

        from datetime import timedelta

        shift_end = datetime.combine(
            punch_in_value.date(),
            datetime.strptime(emp["shift_end"], "%H:%M").time()
        )

        if shift_end <= punch_in_value:
            shift_end += timedelta(days=1)

        if (punch_out_value - shift_end).total_seconds() >= 30 * 60:
            overtime_minutes = int(
                (punch_out_value - shift_end).total_seconds() // 60
            )

        half_day = work_hours < 4.50

    history_entry = {
        "at": datetime.now(timezone.utc).replace(tzinfo=None),
        "changes": changes,
    }

    result = db.attendance_logs.update_one(
        {"_id": record["_id"]},
        {
            "$set": {
                **proposed,
                "late_minutes": late_minutes,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
            },
            "$push": {"history": history_entry},
        },
    )

    if result.modified_count == 0:
        raise HTTPException(status_code=409, detail="Attendance record changed concurrently")

    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    updated.pop("_id", None)
    updated.pop("history", None)
    updated["history"] = [
        {
            "at": int(entry["at"].replace(tzinfo=timezone.utc).timestamp() * 1000),
            "changes": entry["changes"],
        }
        for entry in record.get("history", []) + [history_entry]
    ]
    updated["punch_in"] = int(punch_in_value.replace(tzinfo=timezone.utc).timestamp() * 1000) if punch_in_value else None
    updated["punch_out"] = int(punch_out_value.replace(tzinfo=timezone.utc).timestamp() * 1000) if punch_out_value else None
    return updated


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    try:
        month_start = datetime.strptime(month, "%Y-%m").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="Month must be YYYY-MM")

    from calendar import monthrange
    from decimal import Decimal, ROUND_HALF_UP

    last_day = monthrange(month_start.year, month_start.month)[1]
    month_end = month_start.replace(day=last_day)

    joined_on = emp["joined_on"]
    if isinstance(joined_on, str):
        joined_on = datetime.strptime(joined_on, "%Y-%m-%d").date()
    elif isinstance(joined_on, datetime):
        joined_on = joined_on.date()

    first_workday = max(month_start, joined_on)
    working_days = 0
    if first_workday <= month_end:
        for day in range(first_workday.day, last_day + 1):
            current = month_start.replace(day=day)
            if current.weekday() < 5:
                working_days += 1

    records = list(
        db.attendance_logs.find({
            "emp_code": emp_code,
            "date": {
                "$gte": month_start.isoformat(),
                "$lte": month_end.isoformat()
            }
        })
    )

    present_days = 0.0
    leave_days = 0
    late_count = 0
    total_late_minutes = 0
    total_overtime_minutes = 0

    for record in records:
        status = record.get("status")
        record_date = datetime.strptime(record["date"], "%Y-%m-%d").date()

        if status in ("PRESENT", "WFH", "ON_DUTY") and record_date.weekday() < 5:
            present_days += 0.5 if record.get("half_day", False) else 1.0

        if status == "LEAVE":
            leave_days += 1

        late = record.get("late_minutes", 0) or 0
        if late > 0:
            late_count += 1
            total_late_minutes += late

        total_overtime_minutes += record.get("overtime_minutes", 0) or 0

    attendance_pct = None
    if working_days > 0:
        attendance_pct = float(
            (Decimal(str(present_days)) / Decimal(working_days) * 100)
            .quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late_minutes,
        "total_overtime_minutes": total_overtime_minutes,
        "attendance_pct": attendance_pct
    }



@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    import calendar
    from decimal import Decimal, ROUND_HALF_UP

    try:
        month_start = datetime.strptime(month, "%Y-%m").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="Month must be YYYY-MM")

    last_day = calendar.monthrange(month_start.year, month_start.month)[1]
    month_end = month_start.replace(day=last_day)

    
    employee_match = {
    "joined_on": {"$lte": month_end.isoformat()}
   }

    if department is not None:
        employee_match["department"] = department


    pipeline = [
        {"$match": employee_match},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$code"]},
                                    {"$gte": ["$date", month_start.isoformat()]},
                                    {"$lte": ["$date", month_end.isoformat()]}
                                ]
                            }
                        }
                    },
                    {
                        "$group": {
                            "_id": None,
                            
                "present_days": {
                    "$sum": {
                        "$cond": [
                    {
                        "$and": [
                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                    {
                        "$lt": [
                            {
                                "$dayOfWeek": {
                                    "$dateFromString": {
                                        "dateString": "$date",
                                        "format": "%Y-%m-%d"
                                    }
                                }
                            },
                            7
                        ]
                    },
                    {
                        "$gt": [
                            {
                                "$dayOfWeek": {
                                    "$dateFromString": {
                                        "dateString": "$date",
                                        "format": "%Y-%m-%d"
                                    }
                                }
                            },
                            1
                        ]
                    }
                ]
            },
            {
                "$cond": [
                    {"$eq": ["$half_day", True]},
                    0.5,
                    1
                ]
            },
            0
        ]
    }
},

                            "work_hours_sum": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                                {"$ne": ["$work_hours", None]}
                                            ]
                                        },
                                        "$work_hours",
                                        0
                                    ]
                                }
                            },
                            "work_hours_count": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                                {"$ne": ["$work_hours", None]}
                                            ]
                                        },
                                        1,
                                        0
                                    ]
                                }
                            },
                            "late_count": {
                                "$sum": {
                                    "$cond": [
                                        {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                                        1,
                                        0
                                    ]
                                }
                            },
                            "total_late_minutes": {
                                "$sum": {"$ifNull": ["$late_minutes", 0]}
                            },
                            "leave_count": {
                                "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                            },
                            "on_duty_count": {
                                "$sum": {"$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]}
                            }
                        }
                    }
                ],
                "as": "stats"
            }
        },
        {
            "$unwind": {
                "path": "$stats",
                "preserveNullAndEmptyArrays": True
            }
        },
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                "present_days": {"$sum": {"$ifNull": ["$stats.present_days", 0]}},
                "work_hours_sum": {"$sum": {"$ifNull": ["$stats.work_hours_sum", 0]}},
                "work_hours_count": {"$sum": {"$ifNull": ["$stats.work_hours_count", 0]}},
                "late_count": {"$sum": {"$ifNull": ["$stats.late_count", 0]}},
                "total_late_minutes": {"$sum": {"$ifNull": ["$stats.total_late_minutes", 0]}},
                "leave_count": {"$sum": {"$ifNull": ["$stats.leave_count", 0]}},
                "on_duty_count": {"$sum": {"$ifNull": ["$stats.on_duty_count", 0]}}
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": 1,
                "present_days": 1,
                "avg_work_hours": {
                    "$cond": [
                        {"$gt": ["$work_hours_count", 0]},
                        {"$round": [{"$divide": ["$work_hours_sum", "$work_hours_count"]}, 2]},
                        None
                    ]
                },
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1
            }
        },
        {"$sort": {"department": 1}}
    ]
    
    results = list(db.employees.aggregate(pipeline))

    for item in results:
        item["present_days"] = float(item["present_days"])
        if item["avg_work_hours"] is not None:
            item["avg_work_hours"] = float(
                Decimal(str(item["avg_work_hours"])).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            )

    return {"month": month, "items": results}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str,
    limit: int = 10,
    department: Optional[str] = None
):
    try:
        month_start = datetime.strptime(month, "%Y-%m").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="Month must be YYYY-MM")

    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="Limit must be between 1 and 100")

    from calendar import monthrange

    last_day = monthrange(month_start.year, month_start.month)[1]
    month_end = month_start.replace(day=last_day)

    employee_filter = {}
    if department is not None:
        employee_filter["department"] = department

    employees = list(db.employees.find(employee_filter))
    employee_map = {}
    for emp in employees:
        joined_on = emp["joined_on"]

        if isinstance(joined_on, str):
            try:
                joined_on = datetime.strptime(joined_on, "%Y-%m-%d").date()
            except ValueError:
                continue
        elif isinstance(joined_on, datetime):
            joined_on = joined_on.date()

        if joined_on <= month_end:
            employee_map[emp["emp_code"]] = emp

    if not employee_map:
        return {"month": month, "items": []}

    pipeline = [
        {
            "$match": {
                "emp_code": {"$in": list(employee_map.keys())},
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lte": month_end.isoformat()
                },
                "late_minutes": {"$gt": 0}
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"}
            }
        },
        {
            "$sort": {
                "total_late_minutes": -1,
                "_id": 1
            }
        }
    ]

    totals = list(db.attendance_logs.aggregate(pipeline))

    ranked = []
    previous_total = None
    current_rank = 0

    for index, item in enumerate(totals, start=1):
        total = item["total_late_minutes"]
        if total != previous_total:
            current_rank = index
            previous_total = total

        emp = employee_map[item["_id"]]
        ranked.append({
            "rank": current_rank,
            "emp_code": item["_id"],
            "name": emp["name"],
            "department": emp["department"],
            "total_late_minutes": total
        })

    if limit:
        if len(ranked) > limit:
            cutoff_rank = ranked[limit - 1]["rank"]
            ranked = [
                item for item in ranked
                if item["rank"] <= cutoff_rank
            ]

    return {"month": month, "items": ranked}


@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, from_date: str = None, to_date: str = None):
    from decimal import Decimal, ROUND_HALF_UP
    from datetime import date, timedelta

    if from_date is None or to_date is None:
        raise HTTPException(status_code=422, detail="from and to dates are required")

    try:
        start = datetime.strptime(from_date, "%Y-%m-%d").date()
        end = datetime.strptime(to_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="Dates must be YYYY-MM-DD")

    if end < start:
        raise HTTPException(status_code=422, detail="to must be on or after from")

    if (end - start).days > 91:
        raise HTTPException(status_code=422, detail="Date range cannot exceed 92 days")

    employees = list(
    db.employees.find({
        "department": {
            "$regex": f"^{re.escape(department)}$",
            "$options": "i"
        }
    })
    )
    if not employees:
        raise HTTPException(status_code=404, detail="Department not found")

    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time())

    pipeline = [
        {
            "$match": {
                "emp_code": {"$in": [emp["emp_code"] for emp in employees]},
                "date": {
                    "$gte": start.isoformat(),
                    "$lte": end.isoformat()
                }
            }
        },
        {
            "$addFields": {
                "day": {
                    "$dateFromString": {
                        "dateString": "$date",
                        "format": "%Y-%m-%d"
                    }
                },
                "presence_value": {
                    "$cond": [
                        {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                        {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                        0
                    ]
                }
            }
        },
        {
            "$group": {
                "_id": "$day",
                "present_count": {"$sum": "$presence_value"},
                "late_count": {
                    "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "day": "$_id",
                "present_count": 1,
                "late_count": 1
            }
        },
        {
            "$densify": {
                "field": "day",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [start_dt, end_dt]
                }
            }
        },
        {
            "$addFields": {
                "present_count": {"$ifNull": ["$present_count", 0]},
                "late_count": {"$ifNull": ["$late_count", 0]}
            }
        },
        {"$sort": {"day": 1}},
        {
            "$setWindowFields": {
                "sortBy": {"day": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]}
                    }
                }
            }
        }
    ]

    rows = list(db.attendance_logs.aggregate(pipeline))
    employee_count_by_day = []

    for row in rows:
        day = row["day"].date()
        headcount = 0
        for emp in employees:
            joined_on = emp["joined_on"]

            if isinstance(joined_on, str):
                try:
                    joined_on = datetime.strptime(joined_on, "%Y-%m-%d").date()
                except ValueError:
                    continue
            elif isinstance(joined_on, datetime):
                joined_on = joined_on.date()

            if joined_on <= day:
                headcount += 1
        is_working_day = day.weekday() < 5
        present_count = row.get("present_count", 0)
        attendance_rate = None

        if is_working_day and headcount > 0:
            attendance_rate = float(
                (Decimal(str(present_count)) / Decimal(headcount))
                .quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            )

        employee_count_by_day.append({
            "date": day.isoformat(),
            "is_working_day": is_working_day,
            "headcount": headcount,
            "present_count": present_count,
            "late_count": row.get("late_count", 0),
            "attendance_rate": attendance_rate
        })

    for index, row in enumerate(employee_count_by_day):
        rates = [
            item["attendance_rate"]
            for item in employee_count_by_day[max(0, index - 6):index + 1]
            if item["attendance_rate"] is not None
        ]
        row["moving_avg_7d"] = (
            float(
                (Decimal(str(sum(rates) / len(rates)))
                .quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
            )
            if rates else None
        )

    return {
        "department": department,
        "from": from_date,
        "to": to_date,
        "items": employee_count_by_day
    }


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    month: Optional[str] = None,
    department: Optional[str] = None,
    emp_code: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    limit: int = 10
):
    allowed = {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    }

    if endpoint not in allowed:
        raise HTTPException(status_code=422, detail="Invalid endpoint")

    if page < 1 or page_size < 1 or page_size > 100:
        raise HTTPException(status_code=422, detail="Invalid pagination")

    if endpoint in {
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
    } and month is None:
        raise HTTPException(status_code=422, detail="month is required")

    if endpoint == "employee_monthly":
        if emp_code is None:
            raise HTTPException(status_code=422, detail="emp_code is required")
        pipeline = [
            {"$match": {"emp_code": emp_code, "date": {"$regex": f"^{month}"}}},
            {"$sort": {"date": -1}},
        ]
        return db.command(
        "explain",
       {
        "aggregate": "attendance_logs",
        "pipeline": pipeline,
        "cursor": {}
    },
    verbosity="executionStats"
   )
    if endpoint == "attendance_list":
        match = {}
        if emp_code is not None:
            match["emp_code"] = emp_code
        if department is not None:
            employee_codes = [
                emp["emp_code"]
                for emp in db.employees.find(
                    {"department": department},
                    {"emp_code": 1}
                )
            ]
            match["emp_code"] = {"$in": employee_codes}

        pipeline = [
            {"$match": match},
            {"$sort": {"date": -1, "emp_code": 1}},
            {"$skip": (page - 1) * page_size},
            {"$limit": page_size},
        ]
        
        return db.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {}
            },
            verbosity="executionStats"
        )

    if endpoint == "department_summary":
        pipeline = [
            {
                "$match": {
                    "date": {
                        "$gte": f"{month}-01",
                        "$lte": f"{month}-31"
                    }
                }
            },
            {
                "$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {
                        "$sum": "$late_minutes"
                    }
                }
            }
        ]
        return db.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {}
            },
            verbosity="executionStats"
        )


    if endpoint == "late_leaderboard":
        pipeline = [
            {
                "$match": {
                    "date": {"$regex": f"^{month}"},
                    "late_minutes": {"$gt": 0}
                }
            },
            {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": "$late_minutes"}}},
            {"$sort": {"total_late_minutes": -1, "_id": 1}},
            {"$limit": limit},
        ]
        return db.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {}
            },
            verbosity="executionStats"
        )

        return db.attendance_logs.database.command(
            "aggregate",
            "attendance_logs",
            pipeline=pipeline,
            cursor={},
            explain=True,
            verbosity="executionStats",
        )

    if endpoint == "department_trend":
        if department is None or from_date is None or to_date is None:
            raise HTTPException(
                status_code=422,
                detail="department, from_date and to_date are required"
            )
        pipeline = [
            {
                "$match": {
                    "date": {
                        "$gte": from_date,
                        "$lte": to_date
                    }
                }
            },
            {"$sort": {"date": 1}},
        ]
        return db.command(
         "explain",
        {
        "aggregate": "attendance_logs",
        "pipeline": pipeline,
        "cursor": {}
    },
    verbosity="executionStats"
)

    raise HTTPException(status_code=422, detail="Unsupported endpoint")



@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if page < 1 or page_size < 1 or page_size > 100:
        raise HTTPException(status_code=422, detail="Invalid pagination")

    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must not be after date_to")

    q = {}

    if emp_code:
        q["emp_code"] = emp_code

    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to

    if status:
        q["status"] = status

    total = db.attendance_logs.count_documents(q)

    cursor = db.attendance_logs.find(q, {"_id": 0}).sort(
        [("date", -1), ("emp_code", 1)]
    ).skip((page - 1) * page_size).limit(page_size)

    def to_ms(value):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp() * 1000)

    items = []

    for d in cursor:
        for field in ("punch_in", "punch_out"):
            d[field] = to_ms(d.get(field))

        for entry in d.get("history", []):
            if isinstance(entry.get("at"), datetime):
                entry["at"] = to_ms(entry["at"])

            for change in entry.get("changes", {}).values():
                for key in ("from", "to"):
                    if isinstance(change.get(key), datetime):
                        change[key] = to_ms(change[key])

        items.append(d)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


