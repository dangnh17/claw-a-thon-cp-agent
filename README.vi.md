# CP Agent

🇬🇧 [View in English](README.md)

> Công cụ AI tự động phân tích Event Log, tái dựng User Journey, xác định điểm gãy trong Funnel và phân biệt lỗi UI (thiết bị người dùng), lỗi Network/API (hệ thống).

---

## Demo

[![Xem video](https://img.shields.io/badge/▶%20Watch%20Demo-blue?style=for-the-badge)](https://vngms-my.sharepoint.com/:v:/g/personal/duyhv3_vng_com_vn/IQDNBH7_WhR6TYVlG7uP4veMAZXS6WLoglyFmU0ZKTPMyO8)

## Vấn đề

Dữ liệu event lớn, thiếu cấu trúc và phân tán — khiến mọi team đều khó rút ra insight mà không tốn nhiều công thủ công:

1. **Không có bức tranh rõ ràng về những gì đã xảy ra.** Event Log lộn xộn, nhiễu; để tái dựng hành trình người dùng hay xác định điểm lỗi phải lọc hàng trăm sự kiện bằng tay.
2. **Khó phân biệt nguyên nhân lỗi.** Cùng một triệu chứng có thể do app crash phía client hoặc API timeout phía server — hai nguyên nhân khác nhau hoàn toàn nhưng trông giống hệt nhau từ bề mặt.
3. **Không có visibility về funnel.** Team không có cách nhanh để thấy user rời bỏ ở bước nào, hay so sánh conversion theo thời gian và phân khúc.
4. **Insight bị khóa trong dữ liệu thô.** Pattern hành vi và chuỗi lỗi phổ biến vẫn tồn tại trong data nhưng không bao giờ được khai phá — vì làm thủ công không scale được.
5. **Nhân sự phi kỹ thuật bị phụ thuộc.** CS/Ops không thể tự phân tích mà phải chờ Dev — làm chậm phản hồi khách hàng và tăng MTTR.

---

## Đối tượng sử dụng

| Ai | Cách dùng |
|----|-----------|
| **CS / Ops** | Nhập mô tả lỗi + file JSON → nhận ngay chẩn đoán và bước gãy cụ thể để phản hồi khách hàng. |
| **Dev / QC** | Khoanh vùng lỗi tức thì (UI hay Network/API) mà không cần đọc toàn bộ log thủ công. |
| **Product Owner** | Xem Success Rate và điểm chạm lỗi theo từng tính năng để ra quyết định tối ưu hóa kịp thời. |

---

## Giải pháp

Agent cung cấp ba tính năng:

**Debug Investigator** — Dán Jira ticket vào; LLM tự trích xuất dữ liệu, truy vấn sự kiện, và phân loại lỗi. Kết quả gồm timeline có timestamp, trích dẫn evidence và khuyến nghị xử lý.

**Funnel Analysis** — Định nghĩa các bước funnel theo event ID hoặc prefix; agent tính số lượng user, tỉ lệ drop-off và conversion tại từng bước, sau đó dùng LLM phân tích điểm yếu nhất.

**Journey Insight** — Chạy pipeline 5 bước trên dữ liệu tracking thô để khai phá chuỗi event tự nhiên, phát hiện insight hành vi theo phân khúc user và khung giờ, xuất báo cáo Markdown kèm visual summary.

---

## Kiến trúc

```
Browser (frontend/)
  │  ← Dán Jira ticket, định nghĩa funnel steps, chọn khung giờ
  │  ← Xem timeline, phân loại lỗi, drop-off funnel, báo cáo journey
  │
  └── HTTP (port 8080) ──→ FastAPI (agent/app.py)
                                ├── /api/debug/*            → Debug Investigator
                                ├── /api/funnel-analysis/*  → Funnel Analysis
                                └── /api/journey-insight/*  → Journey Insight

Data store: output/**/*.parquet   (PyArrow)
LLM:        agent/llm_client.py   (GreenNode MaaS — OpenAI-compatible)
```

---

## Cách chạy

### Yêu cầu

- Python 3.10+
- GreenNode MaaS API key (`AI_PLATFORM_API_KEY`)

### 1. Cài đặt

```bash
git clone <repo-url>
cd claw-a-thon-cp-agent
pip install -r requirements.txt
```

### 2. Cấu hình môi trường

```bash
cp .env.example .env
```

Điền API key vào `.env`:

```env
AI_PLATFORM_API_KEY=your-api-key-here
LLM_MODEL=google/gemma-4-31b-it
LLM_BASE_URL=https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1
```

### 3. Khởi động

```bash
python run_agent.py
```

Mở `http://localhost:8080` — các tab tính năng tự động xuất hiện.

### Docker

```bash
cp .env.example .env
docker compose up --build
```

```bash
docker compose up --build -d   # chạy nền
docker compose logs -f          # xem logs
docker compose down             # dừng
```

---

## LLM Client

```python
from agent.llm_client import call_llm

text = call_llm("Phân tích Event Log sau đây...", max_tokens=2000)
```

Cấu hình qua `.env`: `LLM_MODEL`, `LLM_BASE_URL`, `AI_PLATFORM_API_KEY`.

---

## Cấu trúc thư mục

```
├── Dockerfile
├── docker-compose.yml
├── run_agent.py
├── .env.example
├── requirements.txt
│
├── agent/
│   ├── app.py
│   ├── llm_client.py
│   ├── modules/
│   │   ├── __init__.py
│   │   ├── data_ingest.py
│   │   ├── debug_investigator.py
│   │   ├── feature_a.py
│   │   ├── funnel_analysis.py
│   │   └── journey_insight.py
│   ├── pipeline/
│   │   └── journey/
│   │       ├── __init__.py
│   │       ├── step1_event_meaning.py
│   │       ├── step2b_natural_chain_mining.py
│   │       ├── step3_insight_candidates.py
│   │       ├── step4_report.py
│   │       └── step5_visual_summary.py
│   └── data/
│       └── store.py
│
├── frontend/
│   ├── index.html
│   ├── loader.js
│   ├── style.css
│   └── modules/
│       ├── index.js
│       ├── data-ingest.js
│       ├── debug-investigator.js
│       ├── feature-a.js
│       ├── funnel-analysis.js
│       └── journey-insight.js
│
└── output/
```
