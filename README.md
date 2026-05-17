# 🎬 Video Auto-Cut ด้วย Claude CLI + DaVinci Resolve

ใช้ Claude Pro subscription (ไม่ต้องใช้ API key) วิเคราะห์ video แล้ว
generate EDL file สำหรับ import เข้า DaVinci Resolve โดยตรง

---

## 📦 สิ่งที่ต้องติดตั้งก่อน (ทำครั้งเดียว)

### 1. ffmpeg
```bash
# macOS
brew install ffmpeg

# Windows (PowerShell as Admin)
choco install ffmpeg
# หรือดาวน์โหลดจาก https://ffmpeg.org/download.html
```

### 2. Python 3.8+
ตรวจสอบด้วย: `python --version`
ถ้าไม่มี ดาวน์โหลดจาก https://python.org

### 3. Python dependencies
```bash
pip install -r requirements.txt
```

### 4. Claude Code CLI
```bash
# macOS / Linux
curl -fsSL https://claude.ai/install.sh | bash

# Windows — ดาวน์โหลด installer จาก
# https://claude.ai/download
```

### 5. Login ด้วย Claude Pro account
```bash
claude login
# เปิด browser → login ด้วย account เดียวกับ claude.ai
```

ตรวจสอบว่า login สำเร็จ:
```bash
claude --version
claude -p "hello"   # ควรได้รับ response กลับมา
```

---

## 🚀 วิธีใช้งาน

### รูปแบบพื้นฐาน
```bash
python analyze_video.py <path-to-video>
```

### ตัวอย่าง
```bash
# วิเคราะห์ video ธรรมดา
python analyze_video.py my_recording.mp4

# ดึง frame ถี่ขึ้น (ทุก 20 วินาที) เพื่อความแม่นยำ
python analyze_video.py my_recording.mp4 --interval 20

# เข้มงวดขึ้น — ตัดออกถ้า score < 6
python analyze_video.py my_recording.mp4 --min-score 6

# ใช้ทุก option รวมกัน
python analyze_video.py my_recording.mp4 --interval 20 --min-score 6
```

### ผลลัพธ์ที่ได้
```
video_autocut/
├── output_youtube.edl   ← import เข้า DaVinci สำหรับ YouTube
├── output_shorts.edl    ← import เข้า DaVinci สำหรับ Shorts
└── analysis.json        ← ผล analysis ฉบับเต็ม (ดูรายละเอียดได้)
```

---

## 🎬 ขั้นตอนใน DaVinci Resolve

1. **Import video ต้นฉบับ**
   File → Import → Media → เลือก video file

2. **Import YouTube timeline**
   File → Import → Timeline → เลือก `output_youtube.edl`
   → DaVinci จะถามว่าจะ link กับ clip ไหน → เลือก video ที่ import ไว้

3. **Import Shorts timeline** (แยก project หรือแยก timeline)
   File → Import → Timeline → เลือก `output_shorts.edl`

4. **ตรวจและปรับ** ใน timeline ตามต้องการ

5. **Export**
   - YouTube: Deliver → YouTube preset → 1080p/4K
   - Shorts: Deliver → เลือก Custom → ตั้ง resolution เป็น 1080x1920

---

## ⚠️ หมายเหตุ

- **Claude CLI ใช้ quota เดียวกันกับ claude.ai** — ถ้าใช้ Pro plan จะมี limit
  ถ้า quota หมด ให้รอ reset หรือ upgrade เป็น Max
- **ไฟล์ EDL ใช้ fps = 25** — ถ้า video เป็น 30fps ให้แก้ค่า `fps` ใน `sec_to_tc()` ใน script
- **ความแม่นยำขึ้นอยู่กับเนื้อหา** — video ที่มี visual ชัดเจนจะได้ผลดีกว่า talking head

---

## 🔧 Troubleshooting

| ปัญหา | วิธีแก้ |
|---|---|
| `claude: command not found` | เปิด terminal ใหม่ หรือ re-install CLI |
| `ffprobe: command not found` | ติดตั้ง ffmpeg ใหม่ และ restart terminal |
| Claude ตอบไม่เป็น JSON | รัน script อีกครั้ง (Claude อาจตอบผิด format) |
| EDL import ไม่เจอ clip | ตรวจให้แน่ใจว่า video อยู่ใน Media Pool ก่อน import EDL |
| quota หมด | รอ reset หรือรัน `claude login` ตรวจสอบ account |
