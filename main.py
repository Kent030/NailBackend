import os 
import json 
import re
import calendar 
from google.oauth2 import service_account 
from googleapiclient.discovery import build 

from typing import Optional 
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from datetime import datetime, timedelta
from sqlalchemy import create_engine, Column, Integer, String, DateTime, text
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

def update_google_calendar_event(event_id: str, new_summary: str, end_time: datetime, description: str = ""):
    if not calendar_service or not CALENDAR_ID:
        return
    try:
        event_obj = calendar_service.events().get(calendarId=CALENDAR_ID, eventId=event_id).execute()
        event_obj['summary'] = new_summary 
        event_obj['description'] = description  
        
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
                event_obj['description'] = "" 
                
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
    service_type = Column(String, nullable=True)     
    remittance_last_5 = Column(String, nullable=True) 
    start_time = Column(DateTime)
    end_time = Column(DateTime)

# ★ 新增：VIP 專屬資料庫
class VipDB(Base):
    __tablename__ = "vips"
    id = Column(Integer, primary_key=True, index=True)
    user_name = Column(String)
    user_phone = Column(String, unique=True, index=True)

Base.metadata.create_all(bind=engine)

def upgrade_db_schema():
    db = SessionLocal()
    try:
        db.execute(text("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS service_type VARCHAR;"))
        db.execute(text("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS remittance_last_5 VARCHAR;"))
        db.commit()
    except Exception as e:
        db.rollback()
        pass
    finally:
        db.close()
upgrade_db_schema()

DEFAULT_DURATION = 120 
BUFFER_TIME = 15 
BOSS_PWD = "8888" # ★ 老闆的隱藏後台密碼，可以在這邊修改

class BookingCreate(BaseModel):
    user_name: str
    user_phone: str
    service_type: str
    remittance_last_5: str
    start_time: datetime

class VipCreate(BaseModel):
    user_name: str
    user_phone: str

app = FastAPI(title="單人美甲工作室 - VIP 專屬後台版")

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

# ==========================================
# ★ VIP 終極檢查大腦 (結合資料庫與日曆)
# ==========================================
def check_is_vip(user_phone: str, user_name: str, db: Session) -> bool:
    if not user_phone: return False
    
    # 1. 先查 VIP 資料庫有沒有他
    existing_vip = db.query(VipDB).filter(VipDB.user_phone == user_phone).first()
    if existing_vip:
        # 如果當初是自動建檔沒有名字，順便幫他補上名字
        if existing_vip.user_name == "VIP客戶" and user_name != "VIP客戶":
            existing_vip.user_name = user_name
            db.commit()
        return True
        
    # 2. 資料庫沒有？去翻 Google 日曆找 V
    if not calendar_service or not CALENDAR_ID: return False
    try:
        events_result = calendar_service.events().list(
            calendarId=CALENDAR_ID, q=user_phone, maxResults=30, singleEvents=True
        ).execute()
        
        events = events_result.get('items', [])
        for event in events:
            summary = event.get('summary', '').strip()
            if re.search(r'(^|\s|\d)[vV]([\s\d\u4e00-\u9fa5]|$)', summary):
                # 找到了！自動寫入資料庫，以後就不用再查日曆了！
                new_vip = VipDB(user_name=user_name, user_phone=user_phone)
                db.add(new_vip)
                db.commit()
                return True
        return False
    except Exception as e:
        print(f"VIP 查詢失敗: {e}")
        return False

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
    return {"message": "系統運行中：VIP 資料庫與老闆專屬後台已上線！"}

# ★ VIP 前端檢查接口
@app.get("/check-vip/{phone}")
def api_check_vip(phone: str, db: Session = Depends(get_db)):
    is_vip = check_is_vip(phone.strip(), "VIP客戶", db)
    return {"is_vip": is_vip}

# ==========================================
# ★ 老闆專屬 VIP 管理 API
# ==========================================
@app.get("/api/vips")
def get_vips(pwd: str, db: Session = Depends(get_db)):
    if pwd != BOSS_PWD:
        raise HTTPException(status_code=401, detail="密碼錯誤")
    return db.query(VipDB).all()

@app.post("/api/vips")
def add_vip(vip: VipCreate, pwd: str, db: Session = Depends(get_db)):
    if pwd != BOSS_PWD:
        raise HTTPException(status_code=401, detail="密碼錯誤")
    exist = db.query(VipDB).filter(VipDB.user_phone == vip.user_phone).first()
    if exist:
        raise HTTPException(status_code=400, detail="此電話已經在 VIP 名單囉！")
    new_vip = VipDB(user_name=vip.user_name, user_phone=vip.user_phone)
    db.add(new_vip)
    db.commit()
    return {"message": "新增 VIP 成功！"}

@app.delete("/api/vips/{phone}")
def delete_vip(phone: str, pwd: str, db: Session = Depends(get_db)):
    if pwd != BOSS_PWD:
        raise HTTPException(status_code=401, detail="密碼錯誤")
    vip = db.query(VipDB).filter(VipDB.user_phone == phone).first()
    if not vip:
        raise HTTPException(status_code=404, detail="找不到此 VIP")
    db.delete(vip)
    db.commit()
    return {"message": "已移除 VIP 資格"}


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
                "service_name": b.service_name,
                "service_type": b.service_type,             
                "remittance_last_5": b.remittance_last_5,   
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
    
    user_phone = booking.user_phone.strip()
    is_vip = check_is_vip(user_phone, booking.user_name, db)
    
    now = datetime.now()
    if now.month == 12:
        max_year = now.year + 1
        max_month = 1
    else:
        max_year = now.year
        max_month = now.month + 1
        
    last_day = calendar.monthrange(max_year, max_month)[1]
    max_allowed_date = datetime(max_year, max_month, last_day, 23, 59, 59)
    
    if booking_start_time > max_allowed_date and not is_vip:
        raise HTTPException(
            status_code=400, 
            detail=f"目前僅開放預約至 {max_month} 月底喔！後續月份將於日後陸續開放，敬請見諒。"
        )
    
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
        user_phone=user_phone,
        service_name="美甲預約",
        service_type=booking.service_type,           
        remittance_last_5=booking.remittance_last_5, 
        start_time=booking_start_time,
        end_time=calculated_end_time
    )
    db.add(new_booking)
    db.commit()
    db.refresh(new_booking)

    vip_prefix = "V " if is_vip else ""
    new_summary = f"{vip_prefix}{time_str} {booking.user_name} ({booking.service_type}) 末5:{booking.remittance_last_5}"
    
    new_description = (
        f"📱 聯絡電話：{user_phone}\n"
        f"💅 預約部位：{booking.service_type}\n"
        f"💰 匯款末五碼：{booking.remittance_last_5}\n"
        f"👑 VIP 客戶：{'是' if is_vip else '否'}\n"
        f"⏳ 系統自動保留時間：{DEFAULT_DURATION} 分鐘"
    )

    update_google_calendar_event(target_event_id, new_summary, calculated_end_time, new_description)
    
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
