import requests # ★【新增】用來呼叫 LINE API 的套件
from typing import Optional # ★【新增】用來設定選填欄位
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from datetime import datetime, timedelta
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base, Session

# ==========================================
# ★ LINE Messaging API 設定 (請換成你自己的金鑰)
# ==========================================
# 🛑 請務必把下面這串引號裡的文字，換成你在 LINE 後台拿到的超長 Channel Access Token
LINE_ACCESS_TOKEN = "VjmXl7a6yv5rnm4IWsDYW40iGTn5rlIYoTy+nMc5AYqXx4sOapBr9Uf2uID9LVV3xIa9RhDA4PqtZdW3AGQznl3DmFM3BjZvkhokgPWXMvt++bQrmNeOJ7xc6S56xhtsB6+1tU3MJn/e7R2+ILT2iQdB04t89/1O/w1cDnyilFU="

def send_line_push(line_user_id: str, text_message: str):
    """★【新增】專門負責發送 LINE 訊息的機器人小幫手"""
    if not line_user_id or line_user_id == "undefined":
        return # 如果客人沒有綁定 LINE，就不發送
    
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

# 建立資料庫引擎
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
    line_user_id = Column(String, nullable=True) # ★【新增】資料庫多一個口袋裝客人的 LINE ID

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
    line_user_id: Optional[str] = None # ★【新增】允許前端網頁把 LINE ID 傳過來

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
def read_root():
    return {"message": "歡迎！美甲系統已切換為『完全預約制』並啟動 LINE 通知模式！"}

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

    # 寫入資料庫，★【新增】把 line_user_id 一起存起來
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
    
    # ★【新增】預約成功後，發送 LINE 通知給客人！
    if booking.line_user_id:
        start_time_str = booking.start_time.strftime('%Y-%m-%d %H:%M')
        msg = f"親愛的 {booking.user_name} 您好！✨\n您已成功預約美甲服務：\n💅 項目：{booking.service_name}\n⏰ 時間：{start_time_str}\n\n期待您的光臨！🥰"
        send_line_push(booking.line_user_id, msg)
    
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
    
    # ★【新增】刪除前，先把客人的 LINE ID 和預約時間記錄下來
    target_line_id = booking_to_delete.line_user_id
    target_start_time = booking_to_delete.start_time.strftime('%Y-%m-%d %H:%M')
    
    db.delete(booking_to_delete)
    db.commit()
    
    # ★【新增】刪除成功後，發送取消通知！
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
    # ★【新增】如果修改時有傳新的 LINE ID，也一併更新
    if booking_update.line_user_id:
        booking_to_update.line_user_id = booking_update.line_user_id

    db.commit()
    db.refresh(booking_to_update)
    
    return {
        "message": f"成功修改訂單 {booking_id}！", 
        "new_end_time": calculated_end_time.strftime('%Y-%m-%d %H:%M')
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
