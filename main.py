from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from datetime import datetime, timedelta
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base, Session

# ==========================================
# 1. 資料庫設定
# ==========================================
#SQLALCHEMY_DATABASE_URL = "sqlite:///./bookings.db" 
#engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
# ★ 新的 Supabase 雲端資料庫連線
# ★ 改用 Supabase 的 IPv4 中繼站連線
# Connect to Postgres via the shared transaction-mode pooler (IPv4-only)
DATABASE_URL="postgresql://postgres.sugdvdzopuvoronneugd:Lun09260616!@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres?pgbouncer=true"

# Connect to Postgres via the shared session-mode pooler (used for migrations)
DIRECT_URL="postgresql://postgres.sugdvdzopuvoronneugd:Lun09260616!@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres"
# 建立 PostgreSQL 引擎 (不需要 check_same_thread 了)
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

# ★ 老闆的請假/不上班時間清單 (黑名單)
LEAVE_TIMES = [
    ("2026-06-12 13:00", "2026-06-12 18:00"),  # 舉例：6月12日下午出門進貨不上班
    ("2026-06-20 00:00", "2026-06-21 23:59")   # 舉例：6月20、21日週末兩天連假不上班
]

# ==========================================
# 3. 前端傳來的資料格式 (Pydantic 模型)
# ==========================================
class BookingCreate(BaseModel):
    user_name: str
    user_phone: str
    service_name: str
    start_time: datetime

app = FastAPI(title="單人美甲工作室 - 完全預約制版")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允許所有來源的網頁連線
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
    return {"message": "歡迎！美甲系統已切換為『完全預約制』模式！"}

# 讀取所有預約名單 (給老闆管理後台使用)
@app.get("/bookings")
def get_all_bookings(db: Session = Depends(get_db)):
    return db.query(BookingDB).all()

# 透過「姓名 + 電話」查詢特定預約 (給顧客查詢使用)
@app.get("/bookings/search")
def search_bookings(name: str, phone: str, db: Session = Depends(get_db)):
    user_bookings = db.query(BookingDB).filter(
        BookingDB.user_name == name,
        BookingDB.user_phone == phone
    ).all()
    
    if not user_bookings:
        raise HTTPException(status_code=404, detail="找不到符合此姓名與電話的預約紀錄喔！")
    
    return user_bookings

# 新增預約 (POST 路由)
@app.post("/bookings")
def create_booking(booking: BookingCreate, db: Session = Depends(get_db)):
    # 【防呆 1】檢查菜單
    if booking.service_name not in SERVICES_MENU:
        raise HTTPException(status_code=400, detail=f"找不到『{booking.service_name}』這項服務喔！")
    
    # 【防呆 2】不能預約「過去」的時間
    if booking.start_time < datetime.now():
         raise HTTPException(status_code=400, detail="時光機還沒發明喔！請選擇未來的時間進行預約。")

    # 計算預計結束時間
    duration = SERVICES_MENU[booking.service_name]
    calculated_end_time = booking.start_time + timedelta(minutes=(duration + BUFFER_TIME))

    # ★【新防呆：老闆請假檢查！】
    for start_str, end_str in LEAVE_TIMES:
        leave_start = datetime.strptime(start_str, "%Y-%m-%d %H:%M")
        leave_end = datetime.strptime(end_str, "%Y-%m-%d %H:%M")
        if booking.start_time < leave_end and calculated_end_time > leave_start:
            raise HTTPException(status_code=400, detail="不好意思！這個時段老闆私事外出/休假中，不開放預約喔。")

    # 【防呆 3】防客人撞期檢查
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
        end_time=calculated_end_time
    )
    db.add(new_booking)
    db.commit()
    db.refresh(new_booking)
    
    return {
        "message": "預約成功！", 
        "booking_id": new_booking.id,
        "auto_end_time": calculated_end_time.strftime('%Y-%m-%d %H:%M')
    }

# 取消預約 (DELETE 路由)
@app.delete("/bookings/{booking_id}")
def delete_booking(booking_id: int, db: Session = Depends(get_db)):
    booking_to_delete = db.query(BookingDB).filter(BookingDB.id == booking_id).first()
    if not booking_to_delete:
        raise HTTPException(status_code=404, detail=f"找不到訂單編號為 {booking_id} 的預約紀錄喔！")
    db.delete(booking_to_delete)
    db.commit()
    return {"message": f"成功取消預約！已刪除訂單編號: {booking_id}"}

# 修改訂單 (PUT 路由)
@app.put("/bookings/{booking_id}")
def update_booking(booking_id: int, booking_update: BookingCreate, db: Session = Depends(get_db)):
    # 第一步：先找出這筆訂單存不存在
    booking_to_update = db.query(BookingDB).filter(BookingDB.id == booking_id).first()
    if not booking_to_update:
        raise HTTPException(status_code=404, detail=f"找不到訂單編號為 {booking_id} 的預約紀錄喔！")

    # 第二步：檢查新選的時間是不是「過去」的時間
    if booking_update.start_time < datetime.now():
         raise HTTPException(status_code=400, detail="請選擇未來的時間。")

    # 第三步：檢查菜單並重新計算結束時間
    if booking_update.service_name not in SERVICES_MENU:
        raise HTTPException(status_code=400, detail=f"找不到『{booking_update.service_name}』這項服務喔！")
    
    duration = SERVICES_MENU[booking_update.service_name]
    calculated_end_time = booking_update.start_time + timedelta(minutes=(duration + BUFFER_TIME))

    # ★【新防呆：修改訂單時的老闆請假檢查！】
    for start_str, end_str in LEAVE_TIMES:
        leave_start = datetime.strptime(start_str, "%Y-%m-%d %H:%M")
        leave_end = datetime.strptime(end_str, "%Y-%m-%d %H:%M")
        if booking_update.start_time < leave_end and calculated_end_time > leave_start:
            raise HTTPException(status_code=400, detail="不好意思！這個新時段老闆休假中，不開放預約喔。")

    # 第四步：防撞期檢查！
    existing_bookings = db.query(BookingDB).filter(BookingDB.id != booking_id).all()
    for eb in existing_bookings:
        if booking_update.start_time < eb.end_time and calculated_end_time > eb.start_time:
            exist_start = eb.start_time.strftime('%Y-%m-%d %H:%M')
            exist_end = eb.end_time.strftime('%H:%M')
            raise HTTPException(
                status_code=400, 
                detail=f"修改失敗！新時段跟別人衝突囉。（衝突預約：{exist_start} ~ {exist_end}）"
            )

    # 第五步：所有檢查都通過了，把新資料覆寫上去
    booking_to_update.user_name = booking_update.user_name
    booking_to_update.user_phone = booking_update.user_phone
    booking_to_update.service_name = booking_update.service_name
    booking_to_update.start_time = booking_update.start_time
    booking_to_update.end_time = calculated_end_time

    db.commit() # 存檔生效
    db.refresh(booking_to_update)
    
    return {
        "message": f"成功修改訂單 {booking_id}！", 
        "new_end_time": calculated_end_time.strftime('%Y-%m-%d %H:%M')
    }

# ==========================================
# 伺服器自我啟動開關
# ==========================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
