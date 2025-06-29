import os
import zipfile
import threading
import logging
import queue
import rarfile
import requests
import time
from urllib.parse import urlparse, parse_qs
from config import BOT_TOKEN, CHAT_ID

# -------- Logging setup -------- #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s",
    handlers=[
        logging.FileHandler("log.txt"),
        logging.StreamHandler()
    ]
)

# -------- Constants -------- #
MAX_WORKERS = 3
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB

# -------- Util: Bersihkan nama file -------- #
def sanitize_filename(path):
    filename = os.path.basename(path)
    safe = filename.replace(" ", "_").replace("[", "").replace("]", "").replace("(", "").replace(")", "")
    new_path = os.path.join(os.path.dirname(path), safe)
    if new_path != path:
        os.rename(path, new_path)
        logging.info(f"✏️ Ubah nama file: {filename} → {safe}")
    return new_path

# -------- Google Drive -------- #
def get_gdrive_file_id(url):
    parsed = urlparse(url)
    if "id" in parse_qs(parsed.query):
        return parse_qs(parsed.query)["id"][0]
    elif "file" in parsed.path:
        return parsed.path.split("/")[3]
    else:
        raise ValueError("❌ Gagal mengurai URL Google Drive.")

def download_file_with_gdown(file_id):
    url = f"https://drive.google.com/uc?id={file_id}"
    response = requests.get(url, stream=True, allow_redirects=True)

    disposition = response.headers.get("Content-Disposition", "")
    filename = "downloaded_archive"
    if "filename=" in disposition:
        filename = disposition.split("filename=")[1].strip('"')

    # Kirim pesan progres awal ke Telegram
    message = f"📥 Mengunduh: {filename}\nProgres: 0MB"
    send_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(send_url, data={'chat_id': CHAT_ID, 'text': message})
    message_id = r.json().get("result", {}).get("message_id")

    downloaded = 0
    total = int(response.headers.get("Content-Length", 0))
    chunk_size = 10 * 1024 * 1024
    last_update_time = time.time()

    with open(filename, "wb") as f:
        for chunk in response.iter_content(chunk_size):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)

                current_time = time.time()
                if current_time - last_update_time >= 10:
                    percent = (downloaded / total) * 100 if total else 0
                    current_mb = downloaded / (1024 * 1024)
                    total_mb = total / (1024 * 1024)
                    progress_msg = f"📥 Mengunduh: {filename}\nProgres: {current_mb:.1f}MB / {total_mb:.1f}MB ({percent:.1f}%)"
                    edit_url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
                    requests.post(edit_url, data={
                        'chat_id': CHAT_ID,
                        'message_id': message_id,
                        'text': progress_msg
                    })
                    last_update_time = current_time

    # Final update
    percent = (downloaded / total) * 100 if total else 0
    current_mb = downloaded / (1024 * 1024)
    total_mb = total / (1024 * 1024)
    final_msg = f"✅ Selesai mengunduh: {filename}\nTotal: {current_mb:.1f}MB / {total_mb:.1f}MB ({percent:.1f}%)"
    edit_url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    requests.post(edit_url, data={
        'chat_id': CHAT_ID,
        'message_id': message_id,
        'text': final_msg
    })

    logging.info(f"✅ File berhasil diunduh: {filename}")
    return filename

# -------- Ekstraksi -------- #
def extract_archive_file(file_path, extract_to="extracted"):
    os.makedirs(extract_to, exist_ok=True)
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".zip":
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                zip_ref.extractall(extract_to)
            logging.info(f"📂 ZIP diekstrak ke {extract_to}")
        elif ext == ".rar":
            with rarfile.RarFile(file_path) as rar_ref:
                rar_ref.extractall(extract_to)
            logging.info(f"📂 RAR diekstrak ke {extract_to}")
        else:
            raise ValueError("Format file tidak didukung: hanya .zip dan .rar")
    except Exception as e:
        raise RuntimeError(f"Gagal mengekstrak file: {e}")
    return extract_to

# -------- Upload ke Telegram -------- #
def send_file_worker(q):
    while not q.empty():
        file_path = q.get()
        try:
            file_path = sanitize_filename(file_path)
            size = os.path.getsize(file_path)
            logging.info(f"📦 Ukuran {file_path}: {size / (1024*1024):.2f} MB")

            if size > MAX_FILE_SIZE:
                logging.warning(f"⛔ Lewati {file_path}: terlalu besar")
                q.task_done()
                continue

            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
            with open(file_path, 'rb') as f:
                response = requests.post(
                    url,
                    data={'chat_id': CHAT_ID},
                    files={'document': f},
                    timeout=300
                )
            if response.status_code == 200:
                logging.info(f"📤 Berhasil kirim: {file_path}")
            else:
                logging.error(f"❌ Gagal kirim: {file_path} => {response.text}")
        except Exception as e:
            logging.error(f"❌ Error kirim {file_path}: {str(e)}")
        q.task_done()

def send_folder_to_telegram(folder_path):
    q = queue.Queue()
    for root, _, files in os.walk(folder_path):
        for file in sorted(files):
            file_path = os.path.join(root, file)
            q.put(file_path)

    threads = []
    for _ in range(min(MAX_WORKERS, q.qsize())):
        t = threading.Thread(target=send_file_worker, args=(q,))
        t.start()
        threads.append(t)

    q.join()
    logging.info("✅ Semua file berhasil dikirim.")

# -------- Main -------- #
if __name__ == "__main__":
    try:
        gdrive_url = input("🔗 Masukkan URL Google Drive file ZIP atau RAR: ")
        file_id = get_gdrive_file_id(gdrive_url)

        logging.info("📥 Mengunduh file dari Google Drive...")
        archive_filename = download_file_with_gdown(file_id)

        logging.info("🗜️ Mengekstrak file arsip...")
        extracted_path = extract_archive_file(archive_filename)

        logging.info("🚀 Mengirim file ke Telegram...")
        send_folder_to_telegram(extracted_path)

        logging.info("🎉 Proses selesai tanpa error.")
    except Exception as e:
        logging.error(f"❌ Terjadi kesalahan fatal: {str(e)}")
