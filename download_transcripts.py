#!/usr/bin/env python3
"""
股癌 Podcast 逐字稿批次下載工具
從 whatmkreallysaid.com 下載全部逐字稿，按每 50 集分資料夾，方便上傳 NotebookLM。
"""

import json
import os
import time
import urllib.parse
import urllib.request

BASE_URL = "https://whatmkreallysaid.com"
EPISODES_JSON_URL = f"{BASE_URL}/episodes.json"
EPISODES_CONTENT_URL = f"{BASE_URL}/episodes"
OUTPUT_DIR = "gooaye_transcripts"
BATCH_SIZE = 50
REQUEST_DELAY = 0.3  # 秒，避免對伺服器造成壓力


def fetch_episodes_index():
    """取得 episodes.json 集數索引"""
    print("正在取得集數索引 episodes.json ...")
    req = urllib.request.Request(
        EPISODES_JSON_URL,
        headers={"User-Agent": "Mozilla/5.0 (Gooaye Transcript Downloader)"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    print(f"  共取得 {len(data)} 集資訊")
    # 按集數排序
    data.sort(key=lambda ep: ep.get("number", 0))
    return data


def sanitize_filename(name):
    """清理檔名中不適合檔案系統的字元"""
    # 移除或替換不安全的檔名字元
    unsafe = '<>:"/\\|?*'
    for ch in unsafe:
        name = name.replace(ch, "_")
    # 限制長度（保留副檔名空間）
    if len(name) > 200:
        name = name[:200]
    return name.strip()


def get_batch_folder(episode_number, total_episodes):
    """根據集數計算所屬的批次資料夾名稱"""
    batch_index = (episode_number - 1) // BATCH_SIZE
    start = batch_index * BATCH_SIZE + 1
    end = min(start + BATCH_SIZE - 1, total_episodes)
    return f"EP{start:03d}-{end:03d}"


def download_episode(episode, output_path):
    """下載單集逐字稿"""
    filename = episode["filename"]
    encoded_filename = urllib.parse.quote(filename)
    url = f"{EPISODES_CONTENT_URL}/{encoded_filename}"

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Gooaye Transcript Downloader)"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        content = resp.read().decode("utf-8")

    # 在逐字稿前加上 metadata header
    header = f"# EP{episode['number']} {episode.get('title', '')}\n"
    header += f"**日期**: {episode.get('date_display', episode.get('date', ''))}\n\n"
    header += "---\n\n"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(header + content)

    return len(content)


def main():
    print("=" * 60)
    print("  股癌 Podcast 逐字稿批次下載工具")
    print("  來源: whatmkreallysaid.com")
    print("=" * 60)
    print()

    # 1. 取得集數索引
    episodes = fetch_episodes_index()
    total = len(episodes)

    # 2. 建立輸出資料夾
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 3. 儲存 metadata 備份
    index_path = os.path.join(OUTPUT_DIR, "_episodes_index.json")
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(episodes, f, ensure_ascii=False, indent=2)
    print(f"已儲存集數索引到 {index_path}")

    # 4. 建立批次資料夾
    max_ep_number = max(ep["number"] for ep in episodes)
    batch_folders = set()
    for ep in episodes:
        folder = get_batch_folder(ep["number"], max_ep_number)
        batch_folders.add(folder)
        folder_path = os.path.join(OUTPUT_DIR, folder)
        os.makedirs(folder_path, exist_ok=True)

    print(f"已建立 {len(batch_folders)} 個批次資料夾")
    print()

    # 5. 批次下載
    failed = []
    skipped = 0
    downloaded = 0
    total_bytes = 0

    print("開始下載逐字稿...")
    print("-" * 60)

    for i, ep in enumerate(episodes):
        ep_num = ep["number"]
        title = ep.get("title", f"EP{ep_num}")
        safe_filename = sanitize_filename(ep.get("filename", f"EP{ep_num}.md"))
        batch_folder = get_batch_folder(ep_num, max_ep_number)
        output_path = os.path.join(OUTPUT_DIR, batch_folder, safe_filename)

        # 斷點續傳：檢查是否已下載
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            skipped += 1
            print(f"  [{i+1:3d}/{total}] 跳過 (已存在) EP{ep_num} {title[:30]}")
            continue

        try:
            size = download_episode(ep, output_path)
            downloaded += 1
            total_bytes += size
            print(f"  [{i+1:3d}/{total}] ✓ EP{ep_num} {title[:30]}  ({size:,} bytes)")
        except Exception as e:
            failed.append({"number": ep_num, "title": title, "error": str(e)})
            print(f"  [{i+1:3d}/{total}] ✗ EP{ep_num} {title[:30]}  錯誤: {e}")

        # 限速
        if i < len(episodes) - 1:
            time.sleep(REQUEST_DELAY)

    # 6. 報告結果
    print()
    print("=" * 60)
    print("  下載完成！")
    print("=" * 60)
    print(f"  總集數:     {total}")
    print(f"  已下載:     {downloaded}")
    print(f"  已跳過:     {skipped}")
    print(f"  失敗:       {len(failed)}")
    print(f"  總大小:     {total_bytes / 1024 / 1024:.1f} MB")
    print(f"  輸出位置:   {os.path.abspath(OUTPUT_DIR)}/")
    print()

    # 列出資料夾結構
    print("資料夾結構:")
    for folder in sorted(batch_folders):
        folder_path = os.path.join(OUTPUT_DIR, folder)
        count = len([f for f in os.listdir(folder_path) if f.endswith(".md")])
        print(f"  {folder}/  ({count} 集)")

    # 7. 記錄失敗的集數
    if failed:
        failed_path = os.path.join(OUTPUT_DIR, "failed_episodes.txt")
        with open(failed_path, "w", encoding="utf-8") as f:
            for item in failed:
                f.write(f"EP{item['number']} - {item['title']} - {item['error']}\n")
        print(f"\n失敗集數已記錄到 {failed_path}")
        print("可重新執行此腳本，會自動跳過已下載的集數。")

    print()
    print("提示: 每個資料夾包含約 50 集，可直接上傳到 NotebookLM 的各個 Notebook。")


if __name__ == "__main__":
    main()
