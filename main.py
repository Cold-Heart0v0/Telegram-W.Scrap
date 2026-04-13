import os
import asyncio
import re
import traceback
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

# ==========================================
# 1. FETCH VARIABLES FROM RAILWAY ENV
# ==========================================
# Make sure to set these in your Railway project variables
api_id = int(os.environ.get('API_ID', '0'))
api_hash = os.environ.get('API_HASH', '')
phone = os.environ.get('PHONE', '')
session_string = os.environ.get('SESSION_STRING', '') # Used for cloud login

source_chat = int(os.environ.get('SOURCE_CHAT', '0'))
target_chat = int(os.environ.get('TARGET_CHAT', '0'))
CHANNEL_USERNAME = os.environ.get('CHANNEL_USERNAME', '')

# ==========================================
# 2. CHOOSE YOUR SETTINGS
# ==========================================
FORWARD_MESSAGES = os.environ.get('FORWARD_MESSAGES', 'True').lower() == 'true'
SAVE_LINKS_TO_TXT = os.environ.get('SAVE_LINKS_TO_TXT', 'True').lower() == 'true'
DOWNLOAD_MEDIA = os.environ.get('DOWNLOAD_MEDIA', 'True').lower() == 'true'

# ==========================================
# 3. SETUP RAILWAY VOLUME STORAGE
# ==========================================
# In Railway, mount a volume to '/data' and set DATA_DIR='/data' in Env Vars.
# If testing locally, it will default to the current folder.
BASE_DIR = os.environ.get('DATA_DIR', os.path.dirname(os.path.abspath(__file__)))

TXT_FILENAME = os.path.join(BASE_DIR, 'extracted_links.txt')
ERROR_TXT_FILENAME = os.path.join(BASE_DIR, 'error_links.txt')
STATE_FILE = os.path.join(BASE_DIR, 'last_processed_id.txt')
media_folder = os.path.join(BASE_DIR, 'telegram_media')

if DOWNLOAD_MEDIA and not os.path.exists(media_folder):
    os.makedirs(media_folder, exist_ok=True)

# ==========================================
# HELPER FUNCTIONS
# ==========================================
def log_error(category, msg_id, error_details):
    link = f"https://t.me/{CHANNEL_USERNAME}/{msg_id}" if msg_id else "System/Network Level"
    with open(ERROR_TXT_FILENAME, 'a', encoding='utf-8') as f:
        f.write(f"[{category}] Link: {link} | Error: {error_details}\n")

def get_last_processed_id(default_start=1):
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return int(f.read().strip())
        except:
            pass
    return default_start - 1 

def save_last_processed_id(msg_id):
    with open(STATE_FILE, 'w') as f:
        f.write(str(msg_id))

# ==========================================
# MAIN LOGIC
# ==========================================
async def process_and_send_batch(client, message_buffer):
    if not message_buffer:
        return

    highest_msg_id = max(m.id for m in message_buffer)
    
    texts = [m.text for m in message_buffer if m.text]
    combined_text = "\n\n".join(texts)
    
    if len(combined_text) > 1000:
        combined_text = combined_text[:1000] + "..."

    media_files = []

    # 1. EXTRACT AND SAVE LINKS
    if SAVE_LINKS_TO_TXT and combined_text:
        urls = re.findall(r'(https?://[^\s]+)', combined_text)
        if urls:
            with open(TXT_FILENAME, 'a', encoding='utf-8') as file:
                for url in urls:
                    file.write(url + '\n')
            print(f"[Group {highest_msg_id}] Saved {len(urls)} links.")

    # 2. DOWNLOAD MEDIA
    if DOWNLOAD_MEDIA:
        for m in message_buffer:
            if m.media:
                try:
                    path = await client.download_media(m, file=media_folder)
                    if path: 
                        media_files.append(path)
                except Exception as e:
                    print(f"[Msg {m.id}] Error downloading media: {e}")
                    log_error("MEDIA_DOWNLOAD_ERROR", m.id, str(e))

    # 3. FORWARD MESSAGES
    if FORWARD_MESSAGES:
        try:
            if media_files:
                print(f"[Group {highest_msg_id}] Cloning album ({len(media_files)} items) to target...")
                await client.send_message(target_chat, combined_text, file=media_files)
                await asyncio.sleep(5) 
            elif combined_text:
                print(f"[Msg {highest_msg_id}] Cloning text to target...")
                await client.send_message(target_chat, combined_text)
                await asyncio.sleep(2) 
        except Exception as e:
            print(f"[Msg {highest_msg_id}] Error sending message: {e}")
            log_error("MESSAGE_SEND_ERROR", highest_msg_id, str(e))

    # 4. SAVE STATE 
    save_last_processed_id(highest_msg_id)

async def main():
    print("Connecting to Telegram...")
    # Use StringSession for cloud environments so you don't need interactive CLI logins
    async with TelegramClient(StringSession(session_string), api_id, api_hash) as client:
        
        last_id = get_last_processed_id()
        print(f"Resuming script. Reading messages strictly after ID {last_id}...")
        
        message_iterator = client.iter_messages(source_chat, reverse=True, min_id=last_id)
        
        album_buffer = []
        current_grouped_id = None
        
        try:
            async for message in message_iterator:
                if message.grouped_id:
                    if current_grouped_id is None:
                        current_grouped_id = message.grouped_id
                        album_buffer.append(message)
                    elif current_grouped_id == message.grouped_id:
                        album_buffer.append(message)
                    else:
                        await process_and_send_batch(client, album_buffer)
                        current_grouped_id = message.grouped_id
                        album_buffer = [message]
                else:
                    if album_buffer:
                        await process_and_send_batch(client, album_buffer)
                        album_buffer = []
                        current_grouped_id = None
                    
                    await process_and_send_batch(client, [message])

            if album_buffer:
                await process_and_send_batch(client, album_buffer)

        except Exception as e:
            print(f"CRITICAL SYSTEM ERROR: {e}")
            log_error("SYSTEM_SCRIPT_ERROR", getattr(message, 'id', None) if 'message' in locals() else None, traceback.format_exc())

        print("Operation Complete!")
        
        # 5. CLEANUP VIDEO FILES AFTER COMPLETION
        print("Cleaning up downloaded video files...")
        video_extensions = ('.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv')
        
        if os.path.exists(media_folder):
            for filename in os.listdir(media_folder):
                if filename.lower().endswith(video_extensions):
                    file_path = os.path.join(media_folder, filename)
                    try:
                        os.remove(file_path)
                        print(f"Deleted: {filename}")
                    except Exception as e:
                        print(f"Could not delete {filename}: {e}")
            print("Cleanup finished!")

if __name__ == "__main__":
    asyncio.run(main())
