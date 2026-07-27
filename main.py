import os # ★【新增】用來讀取環境變數
import json # ★【新增】用來解析 JSON 鑰匙
from google.oauth2 import service_account # ★【新增】Google 驗證套件
from googleapiclient.discovery import build # ★【新增】Google API 套件

import requests 
from typing import Optional 
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from datetime import datetime, timedelta
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base, Session

# ==========================================
# ★ Google Calendar API 設定 (請在 Render 後台設定變數)
# ==========================================
CALENDAR_ID = os.getenv("CALENDAR_ID")
google_creds_str = os.getenv("GOOGLE_CREDENTIALS_JSON")

calendar_service = None
if google_creds_str:
    try:
        creds_dict = json.loads(google_creds_str)
        SCOPES = ['https://www.googleapis.com/auth/calendar']
        creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        # 建立 Google 日曆連線服務
        calendar_service = build('calendar', 'v3', credentials=creds)
        print("Google Calendar 授權成功！")
    except Exception as e:
        print(f"Google Calendar 授權失敗: {e}")

def add_to_google_calendar(guest_name: str, start_time: datetime, end_time: datetime, service_name: str):
    """★【新增】專門負責把預約寫入 Google 日曆的小幫手"""
    if not calendar_service or not CALENDAR_ID:
        print("缺少 Google 日曆設定，跳過寫入日曆。")
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
            'colorId': '2' # 顏色代碼：2通常是淺綠色，可自行在日曆改
        }
        
        # 呼叫 Google API 插入行程
        created_event = calendar_service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        return created_event.get('htmlLink')
    except Exception as e:
        print(f"寫入 Google 日曆失敗: {e}")
        return None


# ==========================================
# ★ LINE Messaging API 設定 
# ==========================================
LINE_ACCESS_TOKEN = "VjmXl7a6yv5rnm4IWsDYW40iGTn5rlIYoTy+nMc5AYqXx4sOapBr9Uf2uID9LVV3xIa9RhDA4PqtZdW3AGQznl3DmFM3BjZvkhokgPWXMvt++bQrmNeOJ7xc6S56xhtsB6+1tU3MJn/e7R2+ILT2iQdB04t89/1O/w1cDnyilFU="

def send_line_push(line_user_id: str, text_message: str):
    """專門負責發送 LINE 訊息的機器人小幫手"""
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
    except Exception as e:
        print(f"LINE 發送失敗: {e}")

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
# 2. 營業設定 (完全預約制，保留菜單與緩衝)
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

# ==========================================
# 3. 前端傳來的資料格式 (Pydantic 模型)
# ==========================================
class BookingCreate(BaseModel):
    user_name: str
    user_phone: str
    service_name: str
    start_time: datetime
    line_user_id: Optional[str] = None 

app = FastAPI(title="單人美甲工作室 - 智慧 LINE 通知版")

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
    return {"message": "歡迎！美甲系統已切換為『完全預約制』並啟動 LINE 與 Google 日曆通知模式！"}

@app.get("/bookings")
def get_all_bookings(db: Session = Depends(get_db)):
    return db.query(BookingDB).all()

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
    if booking.service_name not in SERVICES_MENU:
        raise HTTPException(status_code=400, detail=f"找不到『{booking.service_name}』這項服務喔！")
    if booking.start_time < datetime.now():
         raise HTTPException(status_code=400, detail="時光機還沒發明喔！請選擇未來的時間進行預約。")

    duration = SERVICES_MENU[booking.service_name]
    calculated_end_time = booking.start_time + timedelta(minutes=(duration + BUFFER_TIME))

    for start_str, end_str in LEAVE_TIMES:
        leave_start = datetime.strptime(start_str, "%Y-%m-%d %H:%M")
        leave_end = datetime.strptime(end_str, "%Y-%m-%d %H:%M")
        if booking.start_time < leave_end and calculated_end_time > leave_start:
            raise HTTPException(status_code=400, detail="不好意思！這個時段老闆私事外出/休假中，不開放預約喔。")

    existing_bookings = db.query(BookingDB).all()
    for eb in existing_bookings:
        if booking.start_time < eb.end_time and calculated_end_time > eb.start_time:
            exist_start = eb.start_time.strftime('%Y-%m-%d %H:%M')
            exist_end = eb.end_time.strftime('%H:%M')
            raise HTTPException(status_code=400, detail=f"預約失敗！時段衝突。（衝突預約：{exist_start} ~ {exist_end}）")

    # 寫入資料庫
    new_booking = BookingDB(
        user_name=booking.user_name,
        user_phone=booking.user_phone,
        service_name=booking.service_name,
        start_time=booking.start_time,
        end_time=calculated_end_time,
        line_user_id=booking.line_user_id 
    )
    db.add(new_booking)
    db.commit()
    db.refresh(new_booking)
    
    # 發送 LINE 通知給客人
    if booking.line_user_id:
        start_time_str = booking.start_time.strftime('%Y-%m-%d %H:%M')
        msg = f"親愛的 {booking.user_name} 您好！✨\n您已成功預約美甲服務：\n💅 項目：{booking.service_name}\n⏰ 時間：{start_time_str}\n\n期待您的光臨！🥰"
        send_line_push(booking.line_user_id, msg)

    # ★【新增】同步寫入老闆的 Google 日曆
    add_to_google_calendar(
        guest_name=booking.user_name,
        start_time=booking.start_time,
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
        raise HTTPException(status_code=404, detail=f"找不到訂單編號為 {booking_id} 的預約紀錄喔！")
    
    target_line_id = booking_to_delete.line_user_id
    target_start_time = booking_to_delete.start_time.strftime('%Y-%m-%d %H:%M')
    
    db.delete(booking_to_delete)
    db.commit()
    
    # 刪除成功後，發送取消通知
    if target_line_id:
        msg = f"【預約取消通知】\n您原定於 {target_start_time} 的預約已取消成功。如有任何問題，歡迎隨時聯繫老闆！"
        send_line_push(target_line_id, msg)
        
    return {"message": f"成功取消預約！已刪除訂單編號: {booking_id}"}

@app.put("/bookings/{booking_id}")
def update_booking(booking_id: int, booking_update: BookingCreate, db: Session = Depends(get_db)):
    booking_to_update = db.query(BookingDB).filter(BookingDB.id == booking_id).first()
    if not booking_to_update:
        raise HTTPException(status_code=404, detail=f"找不到訂單編號為 {booking_id} 的預約紀錄喔！")

    if booking_update.start_time < datetime.now():
         raise HTTPException(status_code=400, detail="請選擇未來的時間。")

    if booking_update.service_name not in SERVICES_MENU:
        raise HTTPException(status_code=400, detail=f"找不到『{booking_update.service_name}』這項服務喔！")
    
    duration = SERVICES_MENU[booking_update.service_name]
    calculated_end_time = booking_update.start_time + timedelta(minutes=(duration + BUFFER_TIME))

    for start_str, end_str in LEAVE_TIMES:
        leave_start = datetime.strptime(start_str, "%Y-%m-%d %H:%M")
        leave_end = datetime.strptime(end_str, "%Y-%m-%d %H:%M")
        if booking_update.start_time < leave_end and calculated_end_time > leave_start:
            raise HTTPException(status_code=400, detail="不好意思！這個新時段老闆休假中，不開放預約喔。")

    existing_bookings = db.query(BookingDB).filter(BookingDB.id != booking_id).all()
    for eb in existing_bookings:
        if booking_update.start_time < eb.end_time and calculated_end_time > eb.start_time:
            exist_start = eb.start_time.strftime('%Y-%m-%d %H:%M')
            exist_end = eb.end_time.strftime('%H:%M')
            raise HTTPException(
                status_code=400, 
                detail=f"修改失敗！新時段跟別人衝突囉。（衝突預約：{exist_start} ~ {exist_end}）"
            )

    booking_to_update.user_name = booking_update.user_name
    booking_to_update.user_phone = booking_update.user_phone
    booking_to_update.service_name = booking_update.service_name
    booking_to_update.start_time = booking_update.start_time
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
            
            msg = f"【明日預約溫馨提醒】🔔\n親愛的 {b.user_name} 您好！\n提醒您明天 ({date_str}) {time_str} 有預約美甲服務：\n💅 項目：{b.service_name}\n\n期待您的光臨！若有任何變動請提早告知老闆喔🥰"
            
            send_line_push(b.line_user_id, msg)
            reminded_count += 1
            
    return {"message": f"執行完畢！共找到了 {len(tomorrow_bookings)} 筆明天的預約，並成功發送了 {reminded_count} 則 LINE 提醒。"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
