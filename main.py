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

def get_google_calendar_events(max_results=500):
    if not calendar_service or not CALENDAR_ID:
        return []
    try:
        today = datetime.now()
        first_day = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        time_min_str = first_day.isoformat() + 'Z' 
        
        events_result = calendar_service.events().list(
            calendarId=CALENDAR_ID, 
            timeMin=time_min_str,
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

# ★ 稍微調整資料庫：允許 user_phone 為空，以便先建立沒有電話的 VIP
class VipDB(Base):
    __tablename__ = "vips"
    id = Column(Integer, primary_key=True, index=True)
    user_name = Column(String)
    user_phone = Column(String, nullable=True) 

Base.metadata.create_all(bind=engine)

def upgrade_db_schema():
    db = SessionLocal()
    try:
        db.execute(text("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS service_type VARCHAR;"))
        db.execute(text("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS remittance_last_5 VARCHAR;"))
        # 若之前設定為 unique，這裡為了容錯不再強制檢查 (有需要可手動在 DB 端移除 unique index)
        db.commit()
    except Exception as e:
        db.rollback()
        pass
    finally:
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
    user_phone: Optional[str] = None # ★ 修改：老闆手動新增時，電話變成選填

app = FastAPI(title="單人美甲工作室 - 自動補完電話版")

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

# ★ 名字清理魔法：去掉括號與後續文字
def clean_vip_name(raw_name: str) -> str:
    # 例如把 "王小明 (手部) 末5" 切割成 "王小明"
    return raw_name.split("(")[0].strip()

# ★ 升級版的 VIP 檢查大腦
def check_is_vip(user_phone: str, user_name: str, db: Session) -> bool:
    cleaned_input_name = clean_vip_name(user_name)
    
    # 1. 優先用「電話」查資料庫
    if user_phone:
        existing_vip_by_phone = db.query(VipDB).filter(VipDB.user_phone == user_phone).first()
        if existing_vip_by_phone:
            # 如果發現名字有括號，順便清理一下
            clean_db_name = clean_vip_name(existing_vip_by_phone.user_name)
            if existing_vip_by_phone.user_name != clean_db_name:
                existing_vip_by_phone.user_name = clean_db_name
                db.commit()
                
            if existing_vip_by_phone.user_name == "VIP客戶" and cleaned_input_name != "VIP客戶":
                existing_vip_by_phone.user_name = cleaned_input_name
                db.commit()
            return True
            
    # 2. 如果電話查不到，試著用「乾淨的名字」查 (為了那些當初只有建名字沒有電話的 VIP)
    if cleaned_input_name != "VIP客戶":
        all_vips = db.query(VipDB).all()
        for vip in all_vips:
            if clean_vip_name(vip.user_name) == cleaned_input_name:
                # 找到了！且他沒有留電話？太好了，趁現在幫他補上！
                if not vip.user_phone and user_phone:
                    vip.user_phone = user_phone
                    db.commit()
                    print(f"已自動幫 VIP {cleaned_input_name} 補上電話：{user_phone}")
                return True
                
    # 3. 如果資料庫真的沒有，再去翻 Google 日曆找 V
    if not calendar_service or not CALENDAR_ID: return False
    try:
        # 先用電話搜
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
                
        # 電話搜不到？再用名字去日曆搜搜看
        if cleaned_input_name != "VIP客戶":
            events_result_name = calendar_service.events().list(
                calendarId=CALENDAR_ID, q=cleaned_input_name, maxResults=30, singleEvents=True
            ).execute()
            events_name = events_result_name.get('items', [])
            for event in events_name:
                summary = event.get('summary', '').strip()
                if re.search(r'(^|\s|\d)[vV]([\s\d\u4e00-\u9fa5]|$)', summary):
                    # 找到了！存入乾淨的名字和剛拿到的電話
                    new_vip = VipDB(user_name=cleaned_input_name, user_phone=user_phone)
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
    return {"message": "系統運行中：名字過濾與無電話補完機制上線！"}

# ★ 前端解鎖檢查時，現在會傳名字進來比對了
@app.get("/check-vip/{phone}")
def api_check_vip(phone: str, name: str = "VIP客戶", db: Session = Depends(get_db)):
    is_vip = check_is_vip(phone.strip(), name.strip(), db)
    return {"is_vip": is_vip}

@app.get("/api/vips")
def get_vips(pwd: str, db: Session = Depends(get_db)):
    if pwd != BOSS_PWD:
        raise HTTPException(status_code=401, detail="密碼錯誤")
    
    # 回傳給老闆前，再一次確保名字乾淨
    vips = db.query(VipDB).all()
    result = []
    for v in vips:
        clean_name = clean_vip_name(v.user_name)
        result.append({
            "user_name": clean_name,
            "user_phone": v.user_phone if v.user_phone else "尚未提供電話"
        })
    return result

@app.post("/api/vips")
def add_vip(vip: VipCreate, pwd: str, db: Session = Depends(get_db)):
    if pwd != BOSS_PWD:
        raise HTTPException(status_code=401, detail="密碼錯誤")
    
    clean_name = clean_vip_name(vip.user_name)
    clean_phone = None
    
    if vip.user_phone:
        clean_phone = re.sub(r'\D', '', vip.user_phone)
        exist = db.query(VipDB).filter(VipDB.user_phone == clean_phone).first()
        if exist:
            raise HTTPException(status_code=400, detail="此電話已經在 VIP 名單囉！")
            
    # ★ 即使沒有電話，只要有名字一樣可以新增入庫
    new_vip = VipDB(user_name=clean_name, user_phone=clean_phone)
    db.add(new_vip)
    db.commit()
    return {"message": "新增 VIP 成功！若未填電話，系統將於客人下次預約時自動補上。"}

@app.delete("/api/vips/{name}")
def delete_vip(name: str, pwd: str, db: Session = Depends(get_db)):
    if pwd != BOSS_PWD:
        raise HTTPException(status_code=401, detail="密碼錯誤")
        
    # 因為電話可能為空，所以我們改用「乾淨名字」來刪除
    target_name = clean_vip_name(name)
    all_vips = db.query(VipDB).all()
    deleted = False
    
    for v in all_vips:
        if clean_vip_name(v.user_name) == target_name:
            db.delete(v)
            deleted = True
            
    if not deleted:
        raise HTTPException(status_code=404, detail="找不到此 VIP")
        
    db.commit()
    return {"message": "已移除 VIP 資格"}

@app.post("/api/sync-vips")
def sync_vips_from_calendar(pwd: str, db: Session = Depends(get_db)):
    if pwd != BOSS_PWD:
        raise HTTPException(status_code=401, detail="密碼錯誤")
    if not calendar_service or not CALENDAR_ID:
        raise HTTPException(status_code=500, detail="Google API 尚未設定")

    try:
        one_year_ago = (datetime.now() - timedelta(days=365)).isoformat() + 'Z'
        events_result = calendar_service.events().list(
            calendarId=CALENDAR_ID, 
            timeMin=one_year_ago,
            maxResults=2000, 
            singleEvents=True
        ).execute()
        
        events = events_result.get('items', [])
        added_count = 0
        missed_phone_count = 0
        
        for event in events:
            summary = event.get('summary', '').strip()
            desc = event.get('description', '')
            
            if re.search(r'(^|\s|\d)[vV]([\s\d\u4e00-\u9fa5]|$)', summary):
                
                # 抓名字並馬上清理乾淨
                name_match = re.search(r'[vV]\s*([^\d\(\)\-\s]+)', summary)
                raw_name = name_match.group(1).strip() if name_match else "VIP客戶"
                clean_name = clean_vip_name(raw_name)
                
                combined_text = summary + " " + desc
                phone_match = re.search(r'09\d{2}[-\s]?\d{3}[-\s]?\d{3}', combined_text)
                
                if phone_match:
                    raw_phone = phone_match.group(0)
                    clean_phone = re.sub(r'\D', '', raw_phone) 
                    
                    exist = db.query(VipDB).filter(VipDB.user_phone == clean_phone).first()
                    if not exist:
                        new_vip = VipDB(user_name=clean_name, user_phone=clean_phone)
                        db.add(new_vip)
                        added_count += 1
                else:
                    # ★ 就算沒電話，名字乾淨，也先幫他建檔！
                    all_vips = db.query(VipDB).all()
                    already_exist = any(clean_vip_name(v.user_name) == clean_name for v in all_vips)
                    if not already_exist and clean_name != "VIP客戶":
                         new_vip = VipDB(user_name=clean_name, user_phone=None)
                         db.add(new_vip)
                         added_count += 1
                    else:
                        missed_phone_count += 1
                        
        db.commit()
        msg = f"同步完成！自動清理並新增了 {added_count} 位 VIP。"
        return {"message": msg}
        
    except Exception as e:
        print(f"同步失敗: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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
