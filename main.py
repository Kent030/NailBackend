import os 
import json 
import re
from google.oauth2 import service_account 
from googleapiclient.discovery import build 

from typing import Optional 
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from datetime import datetime, timedelta
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base, Session

# ==========================================
# ★ Google Calendar API 設定
# ==========================================
CALENDAR_ID = os.getenv("CALENDAR_ID")
google_creds_str = os.getenv("GOOGLE_CREDENTIALS_JSON")

calendar_service = None
if google_creds_str:
    try:
        creds_dict = json.loads(google_creds_str)
        SCOPES = ['https://www.googleapis.com/auth/calendar']
        creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        calendar_service = build('calendar', 'v3', credentials=creds)
        print("Google Calendar 授權成功！")
    except Exception as e:
        print(f"Google Calendar 授權失敗: {e}")

def get_google_calendar_events():
    if not calendar_service or not CALENDAR_ID:
        return []
    try:
        today = datetime.now()
        first_day = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        time_min_str = first_day.isoformat() + 'Z' 
        
        events_result = calendar_service.events().list(
            calendarId=CALENDAR_ID, 
            timeMin=time_min_str,
            maxResults=500, 
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        parsed_events = []
        
        for event in events:
            summary = event.get('summary', '').strip()
            start_info = event['start']
            end_info = event['end']
            
            if 'dateTime' in start_info:
                start_dt = datetime.strptime(start_info['dateTime'][:19], "%Y-%m-%dT%H:%M:%S")
                end_dt = datetime.strptime(end_info['dateTime'][:19], "%Y-%m-%dT%H:%M:%S")
                is_full_day = False
            else:
                start_dt = datetime.strptime(start_info['date'], "%Y-%m-%d")
                end_dt = datetime.strptime(end_info['date'], "%Y-%m-%d")
                is_full_day = True
            
            parsed_events.append({
                "id": event['id'],
                "summary": summary,
                "start_time": start_dt,
                "end_time": end_dt,
                "is_full_day": is_full_day
            })
        return parsed_events
    except Exception as e:
        print(f"讀取 Google 日曆失敗: {e}")
        return []

def update_google_calendar_event(event_id: str, new_summary: str, end_time: datetime):
    if not calendar_service or not CALENDAR_ID:
        return
    try:
        event_obj = calendar_service.events().get(calendarId=CALENDAR_ID, eventId=event_id).execute()
        event_obj['summary'] = new_summary 
        if 'dateTime' in event_obj['end']:
            event_obj['end']['dateTime'] = end_time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
            event_obj['end']['timeZone'] = 'Asia/Taipei'
        calendar_service.events().update(calendarId=CALENDAR_ID, eventId=event_id, body=event_obj).execute()
    except Exception as e:
        print(f"更新 Google 日曆失敗: {e}")

def revert_google_calendar_event(start_time: datetime):
    if not calendar_service or not CALENDAR_ID:
        return
    try:
        events = get_google_calendar_events()
        for ge in events:
            if ge['start_time'] == start_time and not ge['is_full_day']:
                event_id = ge['id']
                event_obj = calendar_service.events().get(calendarId=CALENDAR_ID, eventId=event_id).execute()
                
                time_str = start_time.strftime("%H:%M")
                event_obj['summary'] = time_str 
                
                default_end = start_time + timedelta(hours=1)
                if 'dateTime' in event_obj['end']:
                    event_obj['end']['dateTime'] = default_end.strftime("%Y-%m-%dT%H:%M:%S+08:00")
                calendar_service.events().update(calendarId=CALENDAR_ID, eventId=event_id, body=event_obj).execute()
                break
    except Exception as e:
        print(f"恢復 Google 日曆失敗: {e}")

# ==========================================
# 1. 資料庫設定
# ==========================================
SQLALCHEMY_DATABASE_URL = "postgresql://postgres.sugdvdzopuvoronneugd:Lun09260616!@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres"
engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class BookingDB(Base):
    __tablename__ = "bookings"
    id = Column(Integer, primary_key=True, index=True)
    user_name = Column(String, index=True)
    user_phone = Column(String)
    service_name = Column(String, default="美甲預約") 
    start_time = Column(DateTime)
    end_time = Column(DateTime)

Base.metadata.create_all(bind=engine)

DEFAULT_DURATION = 120 
BUFFER_TIME = 15 

class BookingCreate(BaseModel):
    user_name: str
    user_phone: str
    start_time: datetime

app = FastAPI(title="單人美甲工作室 - 純網頁極簡版")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_event_status(summary, start_time):
    summary = summary.strip()
    if not summary:
        return "PRIVATE"
        
    has_keyword = any(k in summary for k in ["休息", "休假", "外出", "私人", "店休", "吃飯", "保留"])
    if has_keyword:
        return "PRIVATE"
        
    if re.fullmatch(r'^[0-9:：.\s]+$', summary):
        return "OPEN"
        
    has_text = bool(re.search(r'[a-zA-Z0-9\u4e00-\u9fa5]', summary))
    if not has_text:
        return "PRIVATE"
        
    return "BOOKED"

# ==========================================
# 4. API 路由 (Endpoints)
# ==========================================
@app.get("/")
@app.head("/") 
def read_root():
    return {"message": "系統運行中：無 LINE 純網頁預約版本。"}

@app.get("/daily-schedule")
def get_daily_schedule(date_str: str, db: Session = Depends(get_db)):
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式錯誤")

    google_events = get_google_calendar_events()
    
    is_day_off = any(ge for ge in google_events if ge['start_time'].date() == target_date and ge['is_full_day'])
    if is_day_off:
        return {"date": date_str, "slots": []}
    
    schedule_result = []
    for ge in google_events:
        if ge['start_time'].date() == target_date and not ge['is_full_day']:
            time_str = ge['start_time'].strftime("%H:%M") 
            status = get_event_status(ge['summary'], ge['start_time'])
            
            if status == "OPEN":
                if ge['start_time'] < datetime.now():
                    pass 
                else:
                    schedule_result.append({"time": time_str, "status": "可預約", "reason": ""})
            
            elif status == "BOOKED":
                schedule_result.append({"time": time_str, "status": "不可預約", "reason": "已被預約"})
                
    schedule_result.sort(key=lambda x: x["time"])
    return {"date": date_str, "slots": schedule_result}

@app.get("/bookings")
def get_all_bookings(db: Session = Depends(get_db)):
    db_bookings = db.query(BookingDB).all()
    google_events = get_google_calendar_events()
    result = []
    
    gcal_status_map = {}
    for ge in google_events:
        gcal_status_map[ge['start_time']] = {
            "status": "FULL_DAY" if ge['is_full_day'] else get_event_status(ge['summary'], ge['start_time']),
            "summary": ge['summary'],
            "end_time": ge['end_time']
        }
    
    db_start_times = []
    for b in db_bookings:
        gcal_info = gcal_status_map.get(b.start_time)
        if gcal_info and gcal_info["status"] == "BOOKED":
            db_start_times.append(b.start_time)
            result.append({
                "id": b.id,
                "user_name": b.user_name,
                "user_phone": b.user_phone,
                "service_name": "美甲預約",
                "start_time": b.start_time,
                "end_time": b.end_time
            })
            
    fake_id = -1 
    for ge in google_events:
        if ge['start_time'] in db_start_times:
            continue
            
        status = gcal_status_map[ge['start_time']]["status"]
        
        if status == "FULL_DAY":
            user_name_display = "🏖️ 店休日"
        elif status == "OPEN":
            user_name_display = "✨ 可預約"
        elif status == "BOOKED":
            user_name_display = "已被預約"
        else:
            user_name_display = "🔒 休息"
            
        result.append({
            "id": fake_id,
            "user_name": user_name_display,
            "user_phone": "0000000000",
            "service_name": "美甲預約" if status == "BOOKED" else ge['summary'],
            "start_time": ge['start_time'],
            "end_time": ge['end_time']
        })
        fake_id -= 1
        
    return result

@app.post("/bookings")
def create_booking(booking: BookingCreate, db: Session = Depends(get_db)):
    booking_start_time = booking.start_time.replace(tzinfo=None)
    time_str = booking_start_time.strftime("%H:%M")
    
    google_events = get_google_calendar_events()
    target_event_id = None
    for ge in google_events:
        if ge['start_time'] == booking_start_time and not ge['is_full_day']:
            if get_event_status(ge['summary'], ge['start_time']) == "OPEN":
                target_event_id = ge['id']
                break
                
    if not target_event_id:
        raise HTTPException(status_code=400, detail="這個時段尚未開放，或剛剛被預約走囉！")
    
    calculated_end_time = booking_start_time + timedelta(minutes=(DEFAULT_DURATION + BUFFER_TIME))

    new_booking = BookingDB(
        user_name=booking.user_name,
        user_phone=booking.user_phone,
        service_name="美甲預約",
        start_time=booking_start_time,
        end_time=calculated_end_time
    )
    db.add(new_booking)
    db.commit()
    db.refresh(new_booking)

    new_summary = f"{time_str} {booking.user_name}"
    update_google_calendar_event(target_event_id, new_summary, calculated_end_time)
    
    return {
        "message": "預約成功！", 
        "booking_id": new_booking.id
    }

@app.delete("/bookings/{booking_id}")
def delete_booking(booking_id: int, db: Session = Depends(get_db)):
    booking_to_delete = db.query(BookingDB).filter(BookingDB.id == booking_id).first()
    if not booking_to_delete:
        raise HTTPException(status_code=404, detail="找不到紀錄！")
    
    target_start_time = booking_to_delete.start_time
    db.delete(booking_to_delete)
    db.commit()
    
    revert_google_calendar_event(target_start_time)
        
    return {"message": "成功取消預約！"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
