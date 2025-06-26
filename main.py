import os
import requests
import zipfile
import threading
import logging
import queue
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
MAX_WORKERS = 3  # Jumlah thread untuk upload
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB

# -------- Google Drive utilities -------- #
def get_gdrive_file_id(url):
    parsed = urlparse(url)
    if "id" in parse_qs(parsed.query):
        return parse_qs(parsed.query)["id"][0]
    elif "file" in parsed.path:
        return parsed.path.split("/")[3]
    else:
        raise ValueError("❌ Gagal mengurai URL Google Drive.")

def download_gdrive_file(file_id, destination):
    session = requests.Session()
    URL = "https://docs.google.com/uc?export=download"
    response = session.get(URL, params={"id": file_id}, stream=True)
    token = get_confirm_token(response)
    if token:
        params = {"id": file_id, "confirm": token}
        response = session.get(URL, params=params, stream=True)
    save_response_content(response, destination)
    logging.info(f"✅ File berhasil diunduh ke {destination}")

def get_confirm_token(response):
    for k, v in response.cookies.items():
        if k.startswith("download_warning"):
            return v
    return None

def save_response_content(response, destination, chunk_size=32768):
    with open(destination, "wb") as f:
        for chunk in response.iter_content(chunk_size):
            if chunk:
                f.write(chunk)

# -------- Unzip -------- #
def unzip_file(zip_path, extract_to="unzipped"):
    os.makedirs(extract_to, exist_ok=True)
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_to)
    logging.info(f"📂 File berhasil diekstrak ke {extract_to}")
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

    # Enqueue semua file hasil ekstrak
    for root, _, files in os.walk(folder_path):
        for file in sorted(files):  # Urutkan agar lebih terkontrol
            file_path = os.path.join(root, file)
            q.put(file_path)

    # Mulai worker thread
    threads = []
    for _ in range(min(MAX_WORKERS, q.qsize())):
        t = threading.Thread(target=send_file_worker, args=(q,))
        t.start()
        threads.append(t)

    q.join()  # Tunggu semua selesai
    logging.info("✅ Semua file berhasil dikirim.")

# -------- Main Program -------- #
if __name__ == "__main__":
    try:
        gdrive_url = input("🔗 Masukkan URL Google Drive file ZIP: ")
        file_id = get_gdrive_file_id(gdrive_url)

        logging.info("📥 Mengunduh file dari Google Drive...")
        zip_filename = "downloaded.zip"
        download_gdrive_file(file_id, zip_filename)

        logging.info("🗜️ Mengekstrak file ZIP...")
        extracted_path = unzip_file(zip_filename)

        logging.info("🚀 Mengirim file ke Telegram...")
        send_folder_to_telegram(extracted_path)

        logging.info("🎉 Proses selesai tanpa error.")
    except Exception as e:
        logging.error(f"❌ Terjadi kesalahan fatal: {str(e)}")
