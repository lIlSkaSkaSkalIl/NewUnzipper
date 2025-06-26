import os
import zipfile
import threading
import logging
import queue
import rarfile
import requests
import time
from tqdm import tqdm
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

# -------- Utility: Send Telegram Message -------- #
def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={'chat_id': CHAT_ID, 'text': text})
    except Exception as e:
        logging.error(f"❌ Gagal kirim pesan ke Telegram: {e}")

# -------- Google Drive File ID Extraction -------- #
def get_gdrive_file_id(url):
    parsed = urlparse(url)
    if "id" in parse_qs(parsed.query):
        return parse_qs(parsed.query)["id"][0]
    elif "file" in parsed.path:
        return parsed.path.split("/")[3]
    else:
        raise ValueError("❌ Gagal mengurai URL Google Drive.")

# -------- Download File with Progress & Telegram Updates -------- #
def download_file_with_progress(gdrive_url, output="downloaded_file"):
    file_id = get_gdrive_file_id(gdrive_url)
    download_url = f"https://drive.google.com/uc?export=download&id={file_id}"

    session = requests.Session()
    response = session.get(download_url, stream=True)

    # Tangani konfirmasi Google Drive jika file besar
    for key, value in response.cookies.items():
        if key.startswith("download_warning"):
            download_url = f"https://drive.google.com/uc?export=download&id={file_id}&confirm={value}"
            response = session.get(download_url, stream=True)
            break

    total_size = int(response.headers.get('Content-Length', 0))
    block_size = 1024 * 1024  # 1 MB
    filename = output

    progress_bar = tqdm(total=total_size, unit='iB', unit_scale=True)
    last_telegram_update = time.time()
    telegram_message_interval = 10  # detik

    with open(filename, 'wb') as f:
        downloaded = 0
        for data in response.iter_content(block_size):
            f.write(data)
            downloaded += len(data)
            progress_bar.update(len(data))

            current_time = time.time()
            if current_time - last_telegram_update >= telegram_message_interval:
                percent = int(downloaded * 100 / total_size)
                send_telegram_message(f"⬇️ Unduhan berjalan... ({percent}%)")
                last_telegram_update = current_time

    progress_bar.close()

    if total_size != 0 and downloaded != total_size:
        raise RuntimeError("❌ Unduhan gagal atau tidak lengkap.")

    logging.info(f"✅ File berhasil diunduh: {filename}")
    send_telegram_message("✅ Unduhan selesai 100%!")
    return filename

# -------- Archive Extractor (.zip & .rar) -------- #
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

# -------- Upload to Telegram -------- #
def send_file_worker(q):
    while not q.empty():
        file_path = q.get()
        try:
            if os.path.getsize(file_path) > MAX_FILE_SIZE:
                logging.warning(f"⛔ Lewati {file_path}: terlalu besar")
                q.task_done()
                continue

            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
            with open(file_path, 'rb') as f:
                response = requests.post(url, data={'chat_id': CHAT_ID}, files={'document': f})
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
            q.put(os.path.join(root, file))

    threads = []
    for _ in range(min(MAX_WORKERS, q.qsize())):
        t = threading.Thread(target=send_file_worker, args=(q,))
        t.start()
        threads.append(t)

    q.join()
    logging.info("✅ Semua file berhasil dikirim.")

# -------- Main Program -------- #
if __name__ == "__main__":
    try:
        gdrive_url = input("🔗 Masukkan URL Google Drive file ZIP atau RAR: ")
        logging.info("📥 Mengunduh file dari Google Drive...")
        archive_filename = download_file_with_progress(gdrive_url, output="archive_downloaded")

        logging.info("🗜️ Mengekstrak file arsip...")
        extracted_path = extract_archive_file(archive_filename)

        logging.info("🚀 Mengirim file ke Telegram...")
        send_folder_to_telegram(extracted_path)

        logging.info("🎉 Proses selesai tanpa error.")
    except Exception as e:
        logging.error(f"❌ Terjadi kesalahan fatal: {str(e)}")
        send_telegram_message(f"❌ Error: {e}")
