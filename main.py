import os 
import json 
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

def add_to_google_calendar(guest_name: str, start_time: datetime, end_time: datetime, service_name: str):
    """把預約寫入 Google 日曆"""
    if not calendar_service or not CALENDAR_ID:
        return None
    try:
        event = {
            'summary': f'預約：{service_name} - {guest_name}',
            'start': {
                'dateTime': start_time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                'timeZone': 'Asia/Taipei',
            },
            'end': {
                'dateTime': end_time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                'timeZone': 'Asia/Taipei',
            },
            'colorId': '2' 
        }
        created_event = calendar_service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        return created_event.get('htmlLink')
    except Exception as e:
        print(f"寫入 Google 日曆失敗: {e}")
        return None

def get_google_calendar_events():
    """★【新增】讀取 Google 日曆上老闆手動新增的私人行程"""
    if not calendar_service or not CALENDAR_ID:
        return []
    try:
        # 從現在開始抓取未來的行程
        now = datetime.utcnow().isoformat() + 'Z' 
        events_result = calendar_service.events().list(
            calendarId=CALENDAR_ID, 
            timeMin=now,
            maxResults=100, 
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        busy_slots = []
        
        for event in events:
            summary = event.get('summary', '私人行程')
            
            # 如果標題是「預約：」開頭，代表是系統自己寫進去的，資料庫裡已經有了，不需要重複抓
            if summary.startswith('預約：'):
                continue
            
            start_info = event['start']
            end_info = event['end']
            
            # 處理 Google 回傳的時間格式 (包含全天行程和一般時段行程)
            if 'dateTime' in start_info:
                start_dt = datetime.strptime(start_info['dateTime'][:19], "%Y-%m-%dT%H:%M:%S")
                end_dt = datetime.strptime(end_info['dateTime'][:19], "%Y-%m-%dT%H:%M:%S")
            else:
                start_dt = datetime.strptime(start_info['date'], "%Y-%m-%d")
                end_dt = datetime.strptime(end_info['date'], "%Y-%m-%d")
            
            busy_slots.append({
                "summary": summary,
                "start_time": start_dt,
                "end_time": end_dt
            })
            
        return busy_slots
    except Exception as e:
        print(f"讀取 Google 日曆失敗: {e}")
        return []

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

# ==========================================
# 2. 營業設定
# ==========================================
SERVICES_MENU = {
    "單色凝膠": 90,
    "造型凝膠": 120,
    "卸甲續作": 150,
    "純卸甲": 40
}
BUFFER_TIME = 15 

LEAVE_TIMES = [
    ("2026-06-12 13:00", "2026-06-12 18:00"),  
    ("2026-06-20 00:00", "2026-06-21 23:59")   
]

class BookingCreate(BaseModel):
    user_name: str
    user_phone: str
    service_name: str
    start_time: datetime
    line_user_id: Optional[str] = None 

app = FastAPI(title="單人美甲工作室 - 雙向同步版")

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
# 4. API 路由 (Endpoints)
# ==========================================
@app.get("/")
@app.head("/") 
def read_root():
    return {"message": "歡迎！系統已啟動 LINE 與 Google 日曆『雙向』同步模式！"}

@app.get("/bookings")
def get_all_bookings(db: Session = Depends(get_db)):
    """★【修改】合併回傳『資料庫的預約』加上『Google 日曆的私人行程』"""
    # 1. 抓取資料庫原本的訂單
    db_bookings = db.query(BookingDB).all()
    result = []
    for b in db_bookings:
        result.append({
            "id": b.id,
            "user_name": b.user_name,
            "user_phone": b.user_phone,
            "service_name": b.service_name,
            "start_time": b.start_time,
            "end_time": b.end_time,
            "line_user_id": b.line_user_id
        })
        
    # 2. 抓取 Google 日曆上的老闆私人行程，並偽裝成訂單格式
    google_events = get_google_calendar_events()
    fake_id = -1 # 給私人行程一個虛擬的負數 ID，避免跟資料庫衝突
    for ge in google_events:
        result.append({
            "id": fake_id,
            "user_name": "老闆私人行程",
            "user_phone": "0000000000",
            "service_name": ge["summary"],
            "start_time": ge["start_time"],
            "end_time": ge["end_time"],
            "line_user_id": None
        })
        fake_id -= 1
        
    return result

@app.get("/bookings/search")
def search_bookings(name: str, phone: str, db: Session = Depends(get_db)):
    user_bookings = db.query(BookingDB).filter(
        BookingDB.user_name == name,
        BookingDB.user_phone == phone
    ).all()
    if not user_bookings:
        raise HTTPException(status_code=404, detail="找不到符合此姓名與電話的預約紀錄喔！")
    return user_bookings

@app.post("/bookings")
def create_booking(booking: BookingCreate, db: Session = Depends(get_db)):
    # 確保時間格式沒有時區衝突
    booking_start_time = booking.start_time.replace(tzinfo=None)
    
    if booking.service_name not in SERVICES_MENU:
        raise HTTPException(status_code=400, detail=f"找不到『{booking.service_name}』這項服務喔！")
    if booking_start_time < datetime.now():
         raise HTTPException(status_code=400, detail="時光機還沒發明喔！請選擇未來的時間進行預約。")

    duration = SERVICES_MENU[booking.service_name]
    calculated_end_time = booking_start_time + timedelta(minutes=(duration + BUFFER_TIME))

    # 檢查手動休假時間
    for start_str, end_str in LEAVE_TIMES:
        leave_start = datetime.strptime(start_str, "%Y-%m-%d %H:%M")
        leave_end = datetime.strptime(end_str, "%Y-%m-%d %H:%M")
        if booking_start_time < leave_end and calculated_end_time > leave_start:
            raise HTTPException(status_code=400, detail="不好意思！這個時段老闆私事外出/休假中，不開放預約喔。")

    # 檢查資料庫既有預約
    existing_bookings = db.query(BookingDB).all()
    for eb in existing_bookings:
        if booking_start_time < eb.end_time and calculated_end_time > eb.start_time:
            exist_start = eb.start_time.strftime('%Y-%m-%d %H:%M')
            exist_end = eb.end_time.strftime('%H:%M')
            raise HTTPException(status_code=400, detail=f"預約失敗！時段衝突。（衝突預約：{exist_start} ~ {exist_end}）")

    # ★【新增】檢查 Google 日曆私人行程衝突
    google_events = get_google_calendar_events()
    for ge in google_events:
        if booking_start_time < ge['end_time'] and calculated_end_time > ge['start_time']:
            raise HTTPException(status_code=400, detail=f"預約失敗！這個時段老闆有其他行程喔。")

    # 寫入資料庫
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
    
    # 發送 LINE 通知
    if booking.line_user_id:
        start_time_str = booking_start_time.strftime('%Y-%m-%d %H:%M')
        msg = f"親愛的 {booking.user_name} 您好！✨\n您已成功預約美甲服務：\n💅 項目：{booking.service_name}\n⏰ 時間：{start_time_str}\n\n期待您的光臨！🥰"
        send_line_push(booking.line_user_id, msg)

    # 寫入老闆的 Google 日曆
    add_to_google_calendar(
        guest_name=booking.user_name,
        start_time=booking_start_time,
        end_time=calculated_end_time,
        service_name=booking.service_name
    )
    
    return {
        "message": "預約成功！", 
        "booking_id": new_booking.id,
        "auto_end_time": calculated_end_time.strftime('%Y-%m-%d %H:%M')
    }

@app.delete("/bookings/{booking_id}")
def delete_booking(booking_id: int, db: Session = Depends(get_db)):
    booking_to_delete = db.query(BookingDB).filter(BookingDB.id == booking_id).first()
    if not booking_to_delete:
        raise HTTPException(status_code=404, detail=f"找不到訂單編號為 {booking_id} 的紀錄！")
    
    target_line_id = booking_to_delete.line_user_id
    target_start_time = booking_to_delete.start_time.strftime('%Y-%m-%d %H:%M')
    
    db.delete(booking_to_delete)
    db.commit()
    
    if target_line_id:
        msg = f"【預約取消通知】\n您原定於 {target_start_time} 的預約已取消成功。如有任何問題，歡迎隨時聯繫老闆！"
        send_line_push(target_line_id, msg)
        
    return {"message": f"成功取消預約！已刪除訂單編號: {booking_id}"}

@app.put("/bookings/{booking_id}")
def update_booking(booking_id: int, booking_update: BookingCreate, db: Session = Depends(get_db)):
    booking_start_time = booking_update.start_time.replace(tzinfo=None)
    booking_to_update = db.query(BookingDB).filter(BookingDB.id == booking_id).first()
    
    if not booking_to_update:
        raise HTTPException(status_code=404, detail=f"找不到紀錄！")
    if booking_start_time < datetime.now():
         raise HTTPException(status_code=400, detail="請選擇未來的時間。")
    if booking_update.service_name not in SERVICES_MENU:
        raise HTTPException(status_code=400, detail=f"找不到此服務！")
    
    duration = SERVICES_MENU[booking_update.service_name]
    calculated_end_time = booking_start_time + timedelta(minutes=(duration + BUFFER_TIME))

    for start_str, end_str in LEAVE_TIMES:
        leave_start = datetime.strptime(start_str, "%Y-%m-%d %H:%M")
        leave_end = datetime.strptime(end_str, "%Y-%m-%d %H:%M")
        if booking_start_time < leave_end and calculated_end_time > leave_start:
            raise HTTPException(status_code=400, detail="新時段老闆休假中！")

    existing_bookings = db.query(BookingDB).filter(BookingDB.id != booking_id).all()
    for eb in existing_bookings:
        if booking_start_time < eb.end_time and calculated_end_time > eb.start_time:
            raise HTTPException(status_code=400, detail="新時段跟別人衝突囉！")

    # ★【新增】檢查修改後的時間是否跟 Google 日曆私人行程衝突
    google_events = get_google_calendar_events()
    for ge in google_events:
        if booking_start_time < ge['end_time'] and calculated_end_time > ge['start_time']:
            raise HTTPException(status_code=400, detail=f"修改失敗！新時段老闆有其他行程喔。")

    booking_to_update.user_name = booking_update.user_name
    booking_to_update.user_phone = booking_update.user_phone
    booking_to_update.service_name = booking_update.service_name
    booking_to_update.start_time = booking_start_time
    booking_to_update.end_time = calculated_end_time
    
    if booking_update.line_user_id:
        booking_to_update.line_user_id = booking_update.line_user_id

    db.commit()
    db.refresh(booking_to_update)
    
    return {
        "message": f"成功修改訂單 {booking_id}！", 
        "new_end_time": calculated_end_time.strftime('%Y-%m-%d %H:%M')
    }

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
            
    return {"message": f"執行完畢！發送了 {reminded_count} 則 LINE 提醒。"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
