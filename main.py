import os 
import json 
import re
from google.oauth2 import service_account 
from googleapiclient.discovery import build 

import requests 
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
# ★ LINE Messaging API 設定 
# ==========================================
LINE_ACCESS_TOKEN = "VjmXl7a6yv5rnm4IWsDYW40iGTn5rlIYoTy+nMc5AYqXx4sOapBr9Uf2uID9LVV3xIa9RhDA4PqtZdW3AGQznl3DmFM3BjZvkhokgPWXMvt++bQrmNeOJ7xc6S56xhtsB6+1tU3MJn/e7R2+ILT2iQdB04t89/1O/w1cDnyilFU="

def send_line_push(line_user_id: str, text_message: str):
    if not line_user_id or line_user_id == "undefined":
        return 
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"
    }
    payload = {
        "to": line_user_id,
        "messages": [{"type": "text", "text": text_message}]
    }
    try:
        requests.post(url, json=payload, headers=headers)
    except Exception:
        pass

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
    service_name = Column(String)
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    line_user_id = Column(String, nullable=True) 

Base.metadata.create_all(bind=engine)

SERVICES_MENU = {
    "單色凝膠": 90,
    "造型凝膠": 120,
    "卸甲續作": 150,
    "純卸甲": 40
}
BUFFER_TIME = 15 

class BookingCreate(BaseModel):
    user_name: str
    user_phone: str
    service_name: str
    start_time: datetime
    line_user_id: Optional[str] = None 

app = FastAPI(title="單人美甲工作室 - 絕對獨裁版")

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

# ★ 核心判斷邏輯：Google 日曆就是聖旨！
def get_event_status(summary, start_time):
    summary = summary.strip()
    if not summary:
        return "PRIVATE"
        
    # 1. 標題完全只有數字、冒號、點、空白 (例如 "10:00", "14：00") -> 開放預約
    if re.fullmatch(r'^[0-9:：.\s]+$', summary):
        return "OPEN"
        
    # 2. 開頭有時間字串，但後面有跟著字 (例如 "10:00 王小明", "10v小橘") -> 已被預約
    t1 = start_time.strftime("%H:%M") # "09:00"
    t2 = t1.lstrip("0")               # "9:00"
    t3 = start_time.strftime("%H")    # "09"
    t4 = t3.lstrip("0")               # "9"
    
    if summary.startswith(t1) or summary.startswith(t2) or summary.startswith(t3) or summary.startswith(t4):
        return "BOOKED"
        
    # 3. 其他全中文 -> 私人休息
    return "PRIVATE"


# ==========================================
# 4. API 路由 (Endpoints)
# ==========================================
@app.get("/")
@app.head("/") 
def read_root():
    return {"message": "系統已更新！採用 Google 日曆絕對獨裁防呆機制。"}

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
                    pass # 時間過了就不給約
                else:
                    # 無視資料庫舊資料，老闆說開放就是開放！
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
    
    # 建立行事曆當前狀態地圖
    gcal_status_map = {}
    for ge in google_events:
        gcal_status_map[ge['start_time']] = {
            "status": "FULL_DAY" if ge['is_full_day'] else get_event_status(ge['summary'], ge['start_time']),
            "summary": ge['summary'],
            "end_time": ge['end_time']
        }
    
    # 【資料庫的訂單】：只有當 Google 日曆確實是「已被預約」狀態時，才輸出資料庫裡的客人資料 (供查詢功能使用)
    db_start_times = []
    for b in db_bookings:
        gcal_info = gcal_status_map.get(b.start_time)
        if gcal_info and gcal_info["status"] == "BOOKED":
            db_start_times.append(b.start_time)
            result.append({
                "id": b.id,
                "user_name": b.user_name,
                "user_phone": b.user_phone,
                "service_name": b.service_name,
                "start_time": b.start_time,
                "end_time": b.end_time,
                "line_user_id": b.line_user_id
            })
            
    # 【Google 行事曆行程】：補上那些沒有在資料庫裡的空檔、私人休息或手動填寫的預約
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
            "service_name": ge['summary'],
            "start_time": ge['start_time'],
            "end_time": ge['end_time'],
            "line_user_id": None
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
    
    if booking.service_name not in SERVICES_MENU:
        raise HTTPException(status_code=400, detail="找不到這項服務喔！")

    duration = SERVICES_MENU[booking.service_name]
    calculated_end_time = booking_start_time + timedelta(minutes=(duration + BUFFER_TIME))

    new_booking = BookingDB(
        user_name=booking.user_name,
        user_phone=booking.user_phone,
        service_name=booking.service_name,
        start_time=booking_start_time,
        end_time=calculated_end_time,
        line_user_id=booking.line_user_id 
    )
    db.add(new_booking)
    db.commit()
    db.refresh(new_booking)
    
    if booking.line_user_id:
        start_time_str = booking_start_time.strftime('%Y-%m-%d %H:%M')
        msg = f"親愛的 {booking.user_name} 您好！✨\n您已成功預約美甲服務：\n💅 項目：{booking.service_name}\n⏰ 時間：{start_time_str}\n\n期待您的光臨！🥰"
        send_line_push(booking.line_user_id, msg)

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
    
    target_line_id = booking_to_delete.line_user_id
    target_start_time = booking_to_delete.start_time
    
    db.delete(booking_to_delete)
    db.commit()
    
    revert_google_calendar_event(target_start_time)
    
    if target_line_id:
        msg = f"【預約取消通知】\n您原定於 {target_start_time.strftime('%Y-%m-%d %H:%M')} 的預約已取消成功。"
        send_line_push(target_line_id, msg)
        
    return {"message": "成功取消預約！"}

@app.get("/send-reminders")
def send_daily_reminders(db: Session = Depends(get_db)):
    utc_now = datetime.utcnow()
    tw_now = utc_now + timedelta(hours=8)
    tw_tomorrow = tw_now + timedelta(days=1)
    start_of_tomorrow = tw_tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_tomorrow = start_of_tomorrow + timedelta(days=1)
    
    tomorrow_bookings = db.query(BookingDB).filter(
        BookingDB.start_time >= start_of_tomorrow,
        BookingDB.start_time < end_of_tomorrow
    ).all()
    
    reminded_count = 0
    for b in tomorrow_bookings:
        if b.line_user_id:
            time_str = b.start_time.strftime('%H:%M')
            date_str = tw_tomorrow.strftime('%m/%d')
            msg = f"【明日預約溫馨提醒】🔔\n親愛的 {b.user_name} 您好！\n提醒您明天 ({date_str}) {time_str} 有預約美甲服務：\n💅 項目：{b.service_name}\n\n期待您的光臨！"
            send_line_push(b.line_user_id, msg)
            reminded_count += 1
            
    return {"message": f"發送了 {reminded_count} 則 LINE 提醒。"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
