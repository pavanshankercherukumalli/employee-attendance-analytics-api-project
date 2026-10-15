project steps:
Employee Attendance & Analytics API

A REST API built with Python, FastAPI, and MongoDB for managing employees, attendance records, attendance corrections, and analytics.

Run locally

Install dependencies:

pip install -r requirements.txt

Start MongoDB.

Run the API from the project folder:

uvicorn app.main:app --port 8000

Open the API documentation at http://127.0.0.1:8000/docs.

Database setup

Run sample_seed.py only when you intentionally want to reset and reload the sample data.
The time stamps are need to bee fixed some better and also some data base fixing currently it is running I am on it and fixing remaining time stamp issues also 