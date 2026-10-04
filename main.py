import os 
import json 
import re
import calendar 
import asyncio 
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

# ★ 升級：把抓取上限提高到 2000 筆，確保未來幾個月的行程都不會被漏掉
def get_google_calendar_events(max_results=2500):
    if not calendar_service or not CALENDAR_ID:
        return []
    try:
        today = datetime.now()
        # 起始時間：這個月 1 號
        first_day = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        time_min_str = first_day.isoformat() + 'Z' 
        
        # ★ 終極修復：加上「煞車時間 (timeMax)」！只往後抓 90 天 (3個月)
        # 防止「永遠重複的行程」把 2500 筆的扣打全部塞滿！
        end_day = first_day + timedelta(days=90)
        time_max_str = end_day.isoformat() + 'Z'

        events_result = calendar_service.events().list(
            calendarId=CALENDAR_ID, 
            timeMin=time_min_str,
            timeMax=time_max_str,      # ★ 告訴 Google 到這裡就停！
            maxResults=max_results, 
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
                "is_full_day": is_full_day,
                "description": event.get('description', '') 
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
SQLALCHEMY_DATABASE_URL = "postgresql+psycopg2://postgres.sugdvdzopuvoronneugd:Lun09260616!@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres"
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
    created_at = Column(DateTime, default=datetime.now) 
    payment_status = Column(String, default="待匯款")

class VipDB(Base):
    __tablename__ = "vips"
    id = Column(Integer, primary_key=True, index=True)
    user_name = Column(String)
    user_phone = Column(String) 

Base.metadata.create_all(bind=engine)

def upgrade_db_schema():
    db = SessionLocal()
    queries = [
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS service_type VARCHAR;",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS remittance_last_5 VARCHAR;",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;",
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS payment_status VARCHAR DEFAULT '待匯款';",
        "ALTER TABLE vips DROP CONSTRAINT IF EXISTS vips_user_phone_key;",
        "DROP INDEX IF EXISTS ix_vips_user_phone;"                          
    ]
    for q in queries:
        try:
            db.execute(text(q))
            db.commit()
        except Exception as e:
            db.rollback()
    db.close()

upgrade_db_schema()

DEFAULT_DURATION = 120 
BUFFER_TIME = 15 
BOSS_PWD = "8888" 

class BookingCreate(BaseModel):
    user_name: str
    user_phone: str
    service_type: str
    remittance_last_5: str
    start_time: datetime

class VipCreate(BaseModel):
    user_name: str
    user_phone: Optional[str] = None 

app = FastAPI(title="單人美甲工作室 - 超強辨識與防漏版")

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

def clean_vip_name(raw_name: str) -> str:
    return raw_name.split("(")[0].strip()

def check_is_vip(user_phone: str, user_name: str, db: Session) -> bool:
    cleaned_input_name = clean_vip_name(user_name)
    if user_phone:
        existing_vip_by_phone = db.query(VipDB).filter(VipDB.user_phone == user_phone).first()
        if existing_vip_by_phone:
            clean_db_name = clean_vip_name(existing_vip_by_phone.user_name)
            if existing_vip_by_phone.user_name != clean_db_name:
                existing_vip_by_phone.user_name = clean_db_name
                db.commit()
            if existing_vip_by_phone.user_name == "VIP客戶" and cleaned_input_name != "VIP客戶":
                existing_vip_by_phone.user_name = cleaned_input_name
                db.commit()
            return True
    if cleaned_input_name != "VIP客戶":
        all_vips = db.query(VipDB).all()
        for vip in all_vips:
            if clean_vip_name(vip.user_name) == cleaned_input_name:
                if not vip.user_phone and user_phone:
                    vip.user_phone = user_phone
                    db.commit()
                return True
    if not calendar_service or not CALENDAR_ID: return False
    try:
        events_result = calendar_service.events().list(
            calendarId=CALENDAR_ID, q=user_phone, maxResults=30, singleEvents=True
        ).execute()
        events = events_result.get('items', [])
        for event in events:
            summary = event.get('summary', '').strip()
            if re.search(r'(^|\s|\d)[vV]([\s\d\u4e00-\u9fa5]|$)', summary):
                new_vip = VipDB(user_name=cleaned_input_name, user_phone=user_phone)
                db.add(new_vip)
                db.commit()
                return True
        if cleaned_input_name != "VIP客戶":
            events_result_name = calendar_service.events().list(
                calendarId=CALENDAR_ID, q=cleaned_input_name, maxResults=30, singleEvents=True
            ).execute()
            events_name = events_result_name.get('items', [])
            for event in events_name:
                summary = event.get('summary', '').strip()
                if re.search(r'(^|\s|\d)[vV]([\s\d\u4e00-\u9fa5]|$)', summary):
                    new_vip = VipDB(user_name=cleaned_input_name, user_phone=user_phone)
                    db.add(new_vip)
                    db.commit()
                    return True
        return False
    except Exception as e:
        return False

# ★ 升級大腦：讓系統看懂老闆開放預約的字
def get_event_status(summary, start_time):
    summary = summary.strip()
    
    # 1. 沒打標題：一律視為私人休息
    if not summary: 
        return "PRIVATE"
        
    # 2. 明確寫了休息關鍵字：視為私人休息
    if any(k in summary for k in ["休息", "休假", "外出", "私人", "店休", "吃飯", "保留", "不開放"]): 
        return "PRIVATE"
        
    # 3. 開放預約判斷：純數字時間(14:00) 或是 標題有"可預約"、"開放"等字
    summary_lower = summary.lower()
    if re.fullmatch(r'^[0-9:：.\s]+$', summary) or any(k in summary_lower for k in ["可預約", "開放", "空檔", "open"]): 
        return "OPEN"
        
    # 4. 其他有打中文/英文字的情況：視為已被客人預約
    if not bool(re.search(r'[a-zA-Z0-9\u4e00-\u9fa5]', summary)): 
        return "PRIVATE"
        
    return "BOOKED"

async def auto_cancel_unpaid_bookings():
    while True:
        try:
            db = SessionLocal()
            three_days_ago = datetime.now() - timedelta(days=3)
            expired_bookings = db.query(BookingDB).filter(
                BookingDB.payment_status == "待匯款",
                BookingDB.created_at <= three_days_ago,
                BookingDB.start_time > datetime.now()
            ).all()
            for b in expired_bookings:
                target_start = b.start_time
                db.delete(b)
                db.commit()
                revert_google_calendar_event(target_start)
            db.close()
        except Exception as e:
            print(f"自動巡邏發生錯誤: {e}")
        await asyncio.sleep(3600)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(auto_cancel_unpaid_bookings())

@app.get("/")
@app.head("/") 
def read_root():
    return {"message": "系統運行中：超強辨識與自動取消機器人已上線！"}

@app.get("/check-vip/{phone}")
def api_check_vip(phone: str, name: str = "VIP客戶", db: Session = Depends(get_db)):
    is_vip = check_is_vip(phone.strip(), name.strip(), db)
    return {"is_vip": is_vip}

@app.get("/api/vips")
def get_vips(pwd: str, db: Session = Depends(get_db)):
    if pwd != BOSS_PWD: raise HTTPException(status_code=401)
    vips = db.query(VipDB).all()
    result = []
    seen_names = set() 
    for v in vips:
        clean_name = clean_vip_name(v.user_name)
        if clean_name not in seen_names:
            seen_names.add(clean_name)
            result.append({"user_name": clean_name, "user_phone": v.user_phone if v.user_phone else "尚未提供電話"})
    return result

@app.post("/api/vips")
def add_vip(vip: VipCreate, pwd: str, db: Session = Depends(get_db)):
    if pwd != BOSS_PWD: raise HTTPException(status_code=401)
    clean_name = clean_vip_name(vip.user_name)
    clean_phone = None
    try:
        existing_by_name = db.query(VipDB).all()
        for v in existing_by_name:
            if clean_vip_name(v.user_name) == clean_name:
                if vip.user_phone and not v.user_phone:
                    clean_phone = re.sub(r'\D', '', vip.user_phone)
                    v.user_phone = clean_phone
                    db.commit()
                    return {"message": f"此 VIP ({clean_name}) 已存在，已為其補上電話！"}
                raise HTTPException(status_code=400, detail="此 VIP 名字已經存在名單中囉！")
        if vip.user_phone:
            clean_phone = re.sub(r'\D', '', vip.user_phone)
            exist = db.query(VipDB).filter(VipDB.user_phone == clean_phone).first()
            if exist: raise HTTPException(status_code=400, detail="此電話已經在 VIP 名單囉！")
        new_vip = VipDB(user_name=clean_name, user_phone=clean_phone)
        db.add(new_vip)
        db.commit()
        return {"message": "新增 VIP 成功！"}
    except Exception as e:
        db.rollback() 
        raise HTTPException(status_code=500, detail=f"內部錯誤：{str(e)}")

@app.delete("/api/vips/{name}")
def delete_vip(name: str, pwd: str, db: Session = Depends(get_db)):
    if pwd != BOSS_PWD: raise HTTPException(status_code=401)
    target_name = clean_vip_name(name)
    all_vips = db.query(VipDB).all()
    deleted = False
    for v in all_vips:
        if clean_vip_name(v.user_name) == target_name:
            db.delete(v)
            deleted = True
    if not deleted: raise HTTPException(status_code=404)
    db.commit()
    return {"message": "已移除 VIP"}

@app.post("/api/sync-vips")
def sync_vips_from_calendar(pwd: str, db: Session = Depends(get_db)):
    if pwd != BOSS_PWD: raise HTTPException(status_code=401)
    if not calendar_service or not CALENDAR_ID: raise HTTPException(status_code=500)
    try:
        one_year_ago = (datetime.now() - timedelta(days=365)).isoformat() + 'Z'
        events = calendar_service.events().list(calendarId=CALENDAR_ID, timeMin=one_year_ago, maxResults=2000, singleEvents=True).execute().get('items', [])
        added_count = 0
        all_db_vips = db.query(VipDB).all()
        existing_names = {clean_vip_name(v.user_name) for v in all_db_vips}
        existing_phones = {v.user_phone for v in all_db_vips if v.user_phone}
        for event in events:
            summary = event.get('summary', '').strip()
            desc = event.get('description', '')
            if re.search(r'(^|\s|\d)[vV]([\s\d\u4e00-\u9fa5]|$)', summary):
                name_match = re.search(r'[vV]\s*([^\d\(\)\-\s]+)', summary)
                clean_name = clean_vip_name(name_match.group(1).strip() if name_match else "VIP客戶")
                if clean_name == "VIP客戶" or clean_name == "": continue 
                phone_match = re.search(r'09\d{2}[-\s]?\d{3}[-\s]?\d{3}', summary + " " + desc)
                clean_phone = re.sub(r'\D', '', phone_match.group(0)) if phone_match else None
                if clean_name in existing_names:
                    if clean_phone and clean_phone not in existing_phones:
                        target_vip = db.query(VipDB).filter(VipDB.user_name.like(f"{clean_name}%")).first()
                        if target_vip and not target_vip.user_phone:
                            target_vip.user_phone = clean_phone
                            existing_phones.add(clean_phone)
                    continue 
                if clean_phone and clean_phone in existing_phones: continue 
                db.add(VipDB(user_name=clean_name, user_phone=clean_phone))
                db.commit() 
                existing_names.add(clean_name)
                if clean_phone: existing_phones.add(clean_phone)
                added_count += 1
        return {"message": f"同步完成！自動清理並新增了 {added_count} 位全新 VIP。"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/bookings")
def get_admin_bookings(pwd: str, db: Session = Depends(get_db)):
    if pwd != BOSS_PWD: raise HTTPException(status_code=401)
    bookings = db.query(BookingDB).filter(BookingDB.start_time > datetime.now()).order_by(BookingDB.start_time).all()
    res = []
    for b in bookings:
        res.append({
            "id": b.id, "user_name": b.user_name, "user_phone": b.user_phone,
            "service_type": b.service_type, "remittance_last_5": b.remittance_last_5,
            "start_time_display": b.start_time.strftime("%m/%d %H:%M"),
            "created_at_display": b.created_at.strftime("%m/%d %H:%M"),
            "created_at": b.created_at.isoformat(), "payment_status": b.payment_status
        })
    return res

@app.put("/api/admin/bookings/{booking_id}/status")
def update_booking_status(booking_id: int, status: str, pwd: str, db: Session = Depends(get_db)):
    if pwd != BOSS_PWD: raise HTTPException(status_code=401)
    b = db.query(BookingDB).filter(BookingDB.id == booking_id).first()
    if not b: raise HTTPException(status_code=404)
    b.payment_status = status
    db.commit()
    return {"message": "狀態更新成功"}

@app.get("/daily-schedule")
def get_daily_schedule(date_str: str, db: Session = Depends(get_db)):
    try: target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError: raise HTTPException(status_code=400)
    google_events = get_google_calendar_events()
    if any(ge for ge in google_events if ge['start_time'].date() == target_date and ge['is_full_day']):
        return {"date": date_str, "slots": []}
    schedule_result = []
    for ge in google_events:
        if ge['start_time'].date() == target_date and not ge['is_full_day']:
            time_str = ge['start_time'].strftime("%H:%M") 
            status = get_event_status(ge['summary'], ge['start_time'])
            if status == "OPEN":
                if ge['start_time'] >= datetime.now():
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
    gcal_status_map = {ge['start_time']: {"status": "FULL_DAY" if ge['is_full_day'] else get_event_status(ge['summary'], ge['start_time']), "summary": ge['summary'], "end_time": ge['end_time']} for ge in google_events}
    db_start_times = []
    for b in db_bookings:
        gcal_info = gcal_status_map.get(b.start_time)
        if gcal_info and gcal_info["status"] == "BOOKED":
            db_start_times.append(b.start_time)
            result.append({"id": b.id, "user_name": b.user_name, "user_phone": b.user_phone, "service_name": b.service_name, "service_type": b.service_type, "remittance_last_5": b.remittance_last_5, "start_time": b.start_time, "end_time": b.end_time})
    fake_id = -1 
    for ge in google_events:
        if ge['start_time'] in db_start_times: continue
        status = gcal_status_map[ge['start_time']]["status"]
        if status == "FULL_DAY": user_name_display = "🏖️ 店休日"
        elif status == "OPEN": user_name_display = "✨ 可預約"
        elif status == "BOOKED": user_name_display = "已被預約"
        else: user_name_display = "🔒 休息"
        result.append({"id": fake_id, "user_name": user_name_display, "user_phone": "0000000000", "service_name": "美甲預約" if status == "BOOKED" else ge['summary'], "start_time": ge['start_time'], "end_time": ge['end_time']})
        fake_id -= 1
    return result

@app.post("/bookings")
def create_booking(booking: BookingCreate, db: Session = Depends(get_db)):
    booking_start_time = booking.start_time.replace(tzinfo=None)
    time_str = booking_start_time.strftime("%H:%M")
    user_phone = booking.user_phone.strip()
    is_vip = check_is_vip(user_phone, booking.user_name, db)
    now = datetime.now()
    if now.month == 12: max_year, max_month = now.year + 1, 1
    else: max_year, max_month = now.year, now.month + 1
    max_allowed_date = datetime(max_year, max_month, calendar.monthrange(max_year, max_month)[1], 23, 59, 59)
    if booking_start_time > max_allowed_date and not is_vip:
        raise HTTPException(status_code=400, detail=f"目前僅開放預約至 {max_month} 月底喔！後續月份將於日後陸續開放，敬請見諒。")
    google_events = get_google_calendar_events()
    target_event_id = next((ge['id'] for ge in google_events if ge['start_time'] == booking_start_time and not ge['is_full_day'] and get_event_status(ge['summary'], ge['start_time']) == "OPEN"), None)
    if not target_event_id:
        raise HTTPException(status_code=400, detail="這個時段尚未開放，或剛剛被預約走囉！")
    
    calculated_end_time = booking_start_time + timedelta(minutes=(DEFAULT_DURATION + BUFFER_TIME))
    initial_payment_status = "待對帳" if re.search(r'\d{3,}', booking.remittance_last_5) else "待匯款"

    new_booking = BookingDB(
        user_name=booking.user_name, user_phone=user_phone, service_name="美甲預約",
        service_type=booking.service_type, remittance_last_5=booking.remittance_last_5, 
        start_time=booking_start_time, end_time=calculated_end_time,
        created_at=datetime.now(), payment_status=initial_payment_status
    )
    db.add(new_booking)
    db.commit()
    db.refresh(new_booking)

    vip_prefix = "V " if is_vip else ""
    new_summary = f"{vip_prefix}{time_str} {booking.user_name} ({booking.service_type}) 末5:{booking.remittance_last_5}"
    new_description = f"📱 聯絡電話：{user_phone}\n💅 預約部位：{booking.service_type}\n💰 匯款末五碼：{booking.remittance_last_5}\n👑 VIP 客戶：{'是' if is_vip else '否'}\n⏳ 系統自動保留時間：{DEFAULT_DURATION} 分鐘"
    update_google_calendar_event(target_event_id, new_summary, calculated_end_time, new_description)
    return {"message": "預約成功！", "booking_id": new_booking.id}

@app.delete("/bookings/{booking_id}")
def delete_booking(booking_id: int, db: Session = Depends(get_db)):
    booking_to_delete = db.query(BookingDB).filter(BookingDB.id == booking_id).first()
    if not booking_to_delete: raise HTTPException(status_code=404)
    target_start_time = booking_to_delete.start_time
    db.delete(booking_to_delete)
    db.commit()
    revert_google_calendar_event(target_start_time)
    return {"message": "成功取消預約！"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
