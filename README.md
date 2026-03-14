# 📈 Analysis Bot - 全方位投資助理

**Analysis Bot** 是一個整合了 Telegram Bot 與 Web 儀表板的現代化投資輔助系統。它能自動化分析個股估值、聚合財經新聞、摘要 Podcast 重點，並透過 Telegram 即時推播。

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.10+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-green.svg)

---

## ✨ 核心功能 (Features)

### 1. 🤖 智慧投資助理 (Telegram Bot)
*   **/start**: 啟動機器人與互動選單。
*   **/chat**: 與 AI 進行對話，回答投資相關問題。
*   **/info [ticker]**: 查詢個股基本面、營收與 AI 總結報告 (整合 Wikipedia & Google Search)。
*   **/esti [ticker]**: 執行「樂活五線譜」與「均值回歸」估值分析，產生詳細 Markdown 報告。
*   **/research**: 支援上傳 PDF/DOCX 文件，自動生成投資研究摘要。

### 2. 📰 全財經新聞聚合 (News Aggregator)
*   **多來源整合**: 自動抓取 Fugle, UDN, MoneyDJ, MacroMicro 等 8+ 個財經來源。
*   **智慧去重**: 避免重複標題轟炸，提供乾淨的閱讀體驗。
*   **網頁牆**: 提供 `/news` 網頁介面，支援關鍵字搜尋與來源過濾。

### 3. 📊 每日自動分析 (Daily Analysis)
*   **全市場掃描**: 每日收盤後自動掃描觀察清單，計算 TL (趨勢線) 與 SD (標準差)。
*   **低估偵測**: 自動標記股價低於 `TL - 2SD` 的潛在低估股。
*   **ETF 同步**: 自動追蹤 0050/0056 等熱門 ETF 成分股。

### 4. 🎧 Podcast 自動摘要
*   **自動監聽**: 追蹤指定投資 Podcast (如股癌、財報狗)。
*   **AI 轉錄**: 自動下載新單集 -> 轉文字 -> 生成重點摘要 -> 推播至 Telegram。

### 5. 🌐 現代化 Web 儀表板
*   **Glassmorphism UI**: 精美的深色玻璃擬態介面。
*   **個股管理**: 視覺化管理觀察清單、Tag 標籤與 Token 設定。
*   **TradingView 整合**: 在詳細頁面查看即時 K 線圖。

---

## 🛠️ 安裝與執行 (Installation)

### 前置需求
*   Python 3.10+
*   Telegram Bot Token
*   Gemini API Key (用於 AI 分析)
*   [Ollama](https://ollama.com/) (選用，支援本機與雲端大語言模型)
*   Fugle/FinMind Token (選填，用於抓取股價)

### 1. 安裝依賴
```bash
# 建立虛擬環境
python -m venv venv
source venv/bin/activate  # Mac/Linux
# venv\Scripts\activate   # Windows

# 安裝套件
pip install -r requirements.txt
```

### 2. 設定環境變數
請複製 `.env.example` 為 `.env` 並填入您的 Key：
```ini
# Telegram
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_admin_chat_id

# AI Models (Gemini)
GEMINI_API_KEY=your_gemini_key

# AI Models (Ollama)
USE_OLLAMA=true
OLLAMA_BASE_URL="https://ollama.com"  # 雲端模式填 https://ollama.com，本機填 http://localhost:11434
OLLAMA_MODEL="glm-5:cloud"            # 想要使用的模型名稱
OLLAMA_API_KEY="your_ollama_api_key"  # 雲端模式必填，本機模式留空

# Stock Data APIs
FUGLE_API_KEY=your_fugle_key
FINMIND_API_KEY=your_finmind_key
```

### 3. 啟動服務
使用內建的啟動腳本 (同時啟動 Web Server 與 Scheduler)：
```bash
# 開發模式 (Auto Reload)
uvicorn analysis_bot.main:app --reload

# 或使用 Docker (未來支援)
# docker-compose up -d
```

啟動後：
*   **Web Dashboard**: [http://127.0.0.1:8000](http://127.0.0.1:8000)
*   **API Docs**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
*   **Telegram Bot**: 開始對話即可使用。

---

## 📂 專案結構 (Structure)

```text
analysis_bot/
├── api/             # FastAPI 路由 (Web Endpoints)
├── bot/             # Telegram Bot 邏輯 (Handlers, Jobs)
├── models/          # SQLModel 資料庫模型 (Stock, News, User)
├── services/        # 核心商業邏輯 (Analyzer, Parser, Crawler)
├── static/          # 前端資源 (CSS, JS)
├── templates/       # HTML 模板 (Jinja2)
├── config.py        # 全域設定與環境變數
├── main.py          # 程式進入點 (App Factory)
└── scheduler.py     # 排程器設定 (Daily Jobs)
```

---

## 📦 遷移指南 (Migration)
如果您是從舊版 `My_Stock_Page` (Django) 遷移過來，請參閱詳細的遷移報告：
👉 [MIGRATION_PROGRESS.md](analysis_bot/MIGRATION_PROGRESS.md)

---
*Developed with ❤️ by Analysis Bot Team*
