# Binance Prediction Markets Event-Driven Trading Bot + Jev AI Decision Engine

![Architecture](https://img.shields.io/badge/Architecture-Event--Driven-emerald?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=flat-square)
![Next.js](https://img.shields.io/badge/Next.js-14.2-black?style=flat-square)
![Jev AI](https://img.shields.io/badge/AI%20Engine-Typesafe%20Jev%20AI-purple?style=flat-square)
![Vercel Ready](https://img.shields.io/badge/Deploy-Vercel%20Ready-white?style=flat-square)

ระบบเทรดเชิงปริมาณอัตโนมัติ (Quantitative Prediction Trading Bot) ความเร็วสูง ทำงานบนสถาปัตยกรรม **Event-Driven Architecture** ที่เชื่อมต่อข้อมูลตลาดแบบเรียลไทม์จาก **Binance Prediction Markets WebSocket Stream** วิเคราะห์สัญญาณความได้เปรียบทางสถิติด้วย **Jev AI Decision Engine** (Structured Output) ผ่านตัวกรองความเสี่ยงแบบหลายชั้น (**Risk & Execution Guard**) และส่งคำสั่งเทรดความเร็วสูงผ่าน **Binance Prediction REST API (HMAC-SHA256)** พร้อมหน้าแดชบอร์ด **Next.js + Tailwind CSS** ที่พร้อม Deploy บน Vercel ทันที

---

## 🏛️ สถาปัตยกรรมระบบ (System Architecture)

```
┌────────────────────────────────────────────────────────────────────────┐
│                        MARKET DATA INGRESS                             │
│       Binance Prediction WS Stream (wss://fstream.binance.com/ws)      │
│  - Auto-reconnect with Exponential Backoff & Jitter                     │
│  - Heartbeat / Ping-Pong (ping_interval=20, ping_timeout=10)           │
│  - Non-blocking Event Ingress via asyncio.create_task()                │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Non-blocking Ticks
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                       DECISION ENGINE (JEV AI)                         │
│               https://api.typesafe.ai/v1/evaluate                      │
│  - Persistent aiohttp Keep-Alive Connection Pool                       │
│  - Structured Output: {"action": "BUY_YES"|"BUY_NO"|"PASS",           │
│                       "confidence": 0.0-1.0, "reasoning": "..."}       │
│  - High-precision Bayesian Heuristic Fallback Engine                   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ AI Signal + Conviction Score
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                      RISK & EXECUTION GUARD                            │
│  [Gate 1] Daily Loss Circuit Breaker (Max Drawdown Guard)              │
│  [Gate 2] Confidence Threshold Filter (Strictly >= 0.80)               │
│  [Gate 3] Cooldown Throttle (45s per market round to prevent churn)    │
│  [Gate 4] Max Concurrent Positions Cap                                 │
│  [Gate 5] Dynamic Position Sizing (Capped at Max USDT Exposure)        │
│  [Gate 6] Extreme Odds & Spread Sanity Filter                          │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Approved Orders Only
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                       EXECUTION EGRESS (REST)                          │
│               Binance Prediction SAPI (/sapi/v1/prediction/order)      │
│  - HMAC-SHA256 Signature with Server Clock Calibration                 │
│  - Persistent aiohttp.ClientSession (Sub-30ms Round-trip)             │
│  - Zero-Risk Paper Trading & Fill Simulation Engine                    │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Telemetry Broadcast (WS /api)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     WEB MONITORING DASHBOARD (/web)                    │
│                        Next.js 14 + Tailwind CSS                       │
│  - Real-time WebSocket Ingress & Bot Connection Status                 │
│  - Live Binary Odds (Yes/No) with Dynamic Probability Bars             │
│  - Circular Confidence Radar Gauge & Quantitative Reasoning Trace      │
│  - Order Execution History Log with Sub-millisecond Latency Tracking   │
│  - Live Parameter Tuning (Threshold, Sizing, Cooldown, Mode Switch)   │
│  - Deployable on Vercel out-of-the-box                                 │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 📁 โครงสร้างโปรเจกต์ (Project Directory Structure)

```text
binance-jev-prediction-bot/
├── .env.example              # ตัวอย่างไฟล์ตั้งค่าคอนฟิกูเรชันทั้งหมด
├── .gitignore                # Git ignore rules สำหรับ Python และ Next.js
├── README.md                 # คู่มือและเอกสารสถาปัตยกรรมระบบ
├── requirements.txt          # รายการ Library ของ Python
├── config.py                 # โมดูลโหลดและตรวจสอบค่า Config ด้วย Pydantic Settings
├── engine/                   # แกนประมวลผลการเทรดหลัก
│   ├── __init__.py           # Package initializer
│   ├── jev_client.py         # ไคลเอนต์เชื่อมต่อ Jev AI Decision Engine
│   ├── binance_client.py     # ไคลเอนต์ Binance REST API (HMAC-SHA256 + Paper Trading)
│   └── risk_guard.py         # ตัวควบคุมความเสี่ยง (Confidence, Cooldown, Sizing, Drawdown)
├── streams/                  # ระบบรับข้อมูลตลาดแบบ Real-time
│   ├── __init__.py           # Package initializer
│   └── ws_listener.py        # WebSocket Listener (Auto-reconnect, Ping=20, Non-blocking)
├── main.py                   # ตัวควบคุมระบบหลัก (Coordinator) + FastAPI Telemetry Server
└── web/                      # หน้าแดชบอร์ด Frontend (Next.js + Tailwind CSS)
    ├── package.json          # Node.js dependencies และ build scripts
    ├── next.config.js        # การตั้งค่า Next.js (Standalone output สำหรับ Vercel)
    ├── tsconfig.json         # TypeScript configuration
    ├── tailwind.config.js    # ธีมสี Dark Quant, Glassmorphism, Animations
    ├── postcss.config.js     # PostCSS configuration
    └── src/
        ├── types/trading.ts  # Type Definitions สำหรับตลาด, คำสั่งเทรด, สัญญาณ AI
        ├── components/
        │   ├── Header.tsx             # แถบสถานะระบบ, โหมดเทรด, และปุ่มควบคุม
        │   ├── MetricsBar.tsx         # การ์ดแสดงผลสถิติหลัก (Capital, Speed, Uptime, Winrate)
        │   ├── MarketBoard.tsx        # กระดานแสดงราคาและ Odds Yes/No พร้อมเวลานับถอยหลัง
        │   ├── JevAiRadar.tsx         # เกจ์เรดาร์แสดง Confidence % และคำอธิบายเชิงสถิติ
        │   ├── OrderExecutionTable.tsx# ตารางบันทึกประวัติการยิงออเดอร์พร้อม Latency
        │   ├── LiveTerminalLog.tsx    # หน้าต่างคอนโซลจำลองการทำงานของบอท
        │   └── RiskControlsModal.tsx  # หน้าต่างปรับจูนพารามิเตอร์ความเสี่ยงแบบ Real-time
        └── app/
            ├── layout.tsx             # Root layout
            ├── page.tsx               # Main Dashboard page
            └── globals.css            # สไตล์ Glassmorphism และ Dark Mode
```

---

## ⚡ เริ่มต้นใช้งานอย่างรวดเร็ว (Quickstart Guide)

### 1. การติดตั้งและรัน Trading Bot (Python Core)

ต้องการ **Python 3.11 ขึ้นไป**:

```bash
# 1. ติดตั้ง Dependencies
pip install -r requirements.txt

# 2. คัดลอกและตั้งค่า Environment Variables
cp .env.example .env

# 3. รัน Trading Core
python main.py
```

> **หมายเหตุ:** ระบบตั้งค่าเริ่มต้นเป็น `PAPER_TRADING=true` เพื่อความปลอดภัย คุณสามารถรันบอทเพื่อทดสอบได้ทันทีโดยไม่ต้องใส่ API Key ระบบจะจำลองข้อมูลตลาดและการเทรดให้โดยอัตโนมัติ

API & WebSocket Endpoints ของ Trading Core (พอร์ต **8899** ป้องกันการชนกับโปรเจกต์อื่น):
- **Status API:** `http://localhost:8899/api/status`
- **Active Markets:** `http://localhost:8899/api/markets`
- **AI Decisions:** `http://localhost:8899/api/decisions`
- **Order Logs:** `http://localhost:8899/api/orders`
- **Real-time Stream:** `ws://localhost:8899/ws/stream`

---

### 2. การรัน Web Dashboard บนเครื่อง Local (พอร์ต **3888**)

ต้องการ **Node.js 18+**:

```bash
cd web

# 1. ติดตั้ง dependencies
npm install

# 2. รันโหมด Development (กำหนดพอร์ต 3888 โดยเฉพาะ ไม่ชนพอร์ต 3000 ของโปรเจกต์อื่น)
npm run dev
```

เปิดเบราว์เซอร์ไปที่ `http://localhost:3888`

---

### 3. การ Deploy หน้า Dashboard บน Vercel

โฟลเดอร์ `/web` ถูกออกแบบมาให้พร้อม Deploy บน **Vercel** ผ่าน GitHub ได้ทันที:

1. Push โค้ดโปรเจกต์ขึ้น **GitHub**
2. ไปที่ [Vercel Dashboard](https://vercel.com/new) -> กด **Import Git Repository**
3. ในส่วน **Root Directory** ให้เลือกโฟลเดอร์: `web`
4. Framework Preset จะตรวจจับเป็น **Next.js** โดยอัตโนมัติ
5. กด **Deploy**

> **คุณสมบัติพิเศษสำหรับ Vercel Preview:** เมื่อ Deploy บน Vercel ในกรณีที่เครื่อง Server ของบอทไม่ได้เปิดอยู่ หน้าเว็บจะมี **Standalone Live Simulator Fallback** ในตัว ทำให้หน้าแดชบอร์ดมีข้อมูลตลาด การขยับของราคา อัตราต่อรอง และเกจ์วัดความมั่นใจของ AI ทำงานให้ผู้เข้าชมเห็นได้ตลอดเวลาอย่างสวยงาม

---

## 🛡️ กลไกการควบคุมความเสี่ยง (Risk & Execution Guard)

| ด่านตรวจความเสี่ยง (Risk Gate) | พารามิเตอร์เริ่มต้น | พฤติกรรมการทำงาน |
|---|---|---|
| **Confidence Threshold** | `>= 0.80` (80%) | บอทจะปฏิเสธคำสั่งเทรดทันทีหาก Jev AI ประเมินค่าความมั่นใจต่ำกว่า 80% |
| **Cooldown Throttle** | `45 วินาที` | ป้องกันการยิงออเดอร์ซ้ำซ้อนในสัญญาของรอบตลาดเดียวกันภายใน 45 วินาที |
| **Position Sizing** | สูงสุด `$50.00 USDT` | คำนวณจำนวนสัญญาตามขนาดทุนและปรับขยายตามระดับความมั่นใจของสัญญาณ |
| **Daily Drawdown Breaker** | `$200.00 USDT` | ระบบจะตัดการทำงานฉุกเฉิน (Circuit Breaker) ทันทีหากขาดทุนสะสมในวันถึงเกณฑ์ |
| **Max Concurrent Positions** | `5 สัญญา` | จำกัดจำนวนสัญญาที่เปิดค้างพร้อมกันในทุกตลาด |
| **Extreme Odds Protection** | `0.02 - 0.90` | ป้องกันการเข้าซื้อในราคาที่เสียเปรียบ (เช่น ซื้อ Yes ที่ 0.95+ ซึ่งมี Risk/Reward ต่ำมาก) |

---

## ⚙️ รายละเอียดตัวแปร Configuration (.env)

| ตัวแปร | ค่าเริ่มต้น | คำอธิบาย |
|---|---|---|
| `BINANCE_API_KEY` | `""` | API Key ของ Binance (เว้นว่างไว้เพื่อรันในโหมด Paper Trading) |
| `BINANCE_API_SECRET` | `""` | API Secret ของ Binance |
| `BINANCE_PREDICTION_BASE_URL` | `https://fapi.binance.com` | Base URL สำหรับ REST API |
| `BINANCE_PREDICTION_WS_URL` | `wss://fstream.binance.com/ws` | WebSocket Stream URL สำหรับรับข้อมูลราคาแบบสด |
| `JEV_AI_API_KEY` | `""` | API Key สำหรับ Jev AI (หากไม่ใส่จะใช้ Heuristic Bayesian Engine ภายใน) |
| `JEV_AI_ENDPOINT` | `https://api.typesafe.ai/v1/evaluate` | Endpoint API ของ Jev AI |
| `CONFIDENCE_THRESHOLD` | `0.80` | เกณฑ์ความมั่นใจขั้นต่ำที่ยอมให้เปิดออเดอร์ (0.0 ถึง 1.0) |
| `MAX_POSITION_SIZE_USDT` | `50.0` | วงเงินเปิดสถานะสูงสุดต่อออเดอร์ (USDT) |
| `COOLDOWN_SECONDS` | `45` | หน่วงเวลาป้องกันการยิงออเดอร์ซ้ำในตลาดเดิม (วินาที) |
| `MAX_DAILY_LOSS_USDT` | `200.0` | ขีดจำกัดขาดทุนรายวันก่อนตัดการทำงาน |
| `TELEMETRY_PORT` | `8899` | พอร์ตของ FastAPI & WebSocket สำหรับหน้า Dashboard (ป้องกันการชนกับพอร์ต 8000) |
| `NEXT_PUBLIC_BOT_PORT` | `8899` | พอร์ตที่ Next.js Dashboard ใช้เชื่อมต่อไปยัง Python Core |
| `DASHBOARD_USERNAME` | `admin` | ชื่อผู้ใช้สำหรับยืนยันตัวตนเข้าสู่หน้า Web Dashboard (Account Identify) |
| `DASHBOARD_PASSWORD` | `trader2026` | รหัสผ่าน Master Key / PIN สำหรับปลดล็อคหน้าแดชบอร์ด |

---

## 🔐 ระบบยืนยันตัวตนและการควบคุมบอทผ่านหน้าเว็บ (Web Operator Controls)

1. **ระบบป้องกัน Account Identification:**
   - เมื่อเข้าหน้าแดชบอร์ดครั้งแรก ระบบจะแสดงหน้าต่าง **Identity Access Gateway** บังคับให้ยืนยันตัวตนด้วย Username และ Master PIN ก่อนเข้าสู่ระบบควบคุม
   - ค่าเริ่มต้น:
     - **Username:** `admin`
     - **Password:** `trader2026`
   - เมื่อยืนยันตัวตนผ่าน ระบบจะจำกัด Session ไว้ในเบราว์เซอร์อย่างปลอดภัย พร้อมปุ่ม **Sign Out / Lock Desk** ที่แถบเมนูด้านบน

2. **ปุ่มสั่งการ START / STOP บอทสดจากหน้าเว็บ:**
   - **START BOT (สีเขียว):** สั่งเริ่มทำงานหรือปลดล็อคให้บอทเริ่มดักจับราคาและส่งคำสั่งเทรดอัตโนมัติ
   - **STOP BOT (สีแดง):** สั่งหยุดการทำงานฉุกเฉิน (Freeze/Halt) คำสั่งเทรดทั้งหมดจะถูกระงับทันที
   - แสดงสถานะแบบเรียลไทม์: `● BOT ACTIVE (RUNNING)` หรือ `■ BOT STOPPED (IDLE)`

---

## 📜 License
MIT License. พัฒนาขึ้นเพื่อการศึกษาและการทดสอบกลยุทธ์เชิงปริมาณ (Quantitative Research & Automated Prediction Trading).
