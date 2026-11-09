"""Core constants, paths, presets, and domain exceptions for VietSub Studio."""

import os
import re
import secrets
import sys

SOURCE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESOURCE_DIR = getattr(sys, "_MEIPASS", SOURCE_DIR)
APP_HOME = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else SOURCE_DIR
ICON_PATH = os.path.join(RESOURCE_DIR, "avatar.ico")

APPDATA = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
LOCALAPPDATA = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")


def get_windows_directory():
    if os.name != "nt":
        return ""
    import ctypes

    buffer = ctypes.create_unicode_buffer(32_768)
    length = ctypes.windll.kernel32.GetWindowsDirectoryW(buffer, len(buffer))
    return buffer.value if 0 < length < len(buffer) else r"C:\Windows"


WINDOWS_DIRECTORY = get_windows_directory()
POWERSHELL_EXE = os.path.join(
    WINDOWS_DIRECTORY,
    "System32",
    "WindowsPowerShell",
    "v1.0",
    "powershell.exe",
)

VIDEOCR_CLI = os.environ.get("VIETSUB_VIDEOCR_CLI") or r"C:\Program Files\VideOCR\videocr-cli.exe"
VIDEOCR_CONFIG = os.path.join(APPDATA, "VideOCR", "videocr_gui_config.ini")

USER_DATA_DIR_DEBUG = os.path.join(LOCALAPPDATA, "Microsoft", "Edge", "User Data Debug")
EDGE_DEBUG_PORT = 9222

CONFIG_DIR = (
    os.path.join(APPDATA, "VietSub Studio")
    if getattr(sys, "frozen", False) and APPDATA
    else APP_HOME
)
CONFIG_PATH = os.path.join(CONFIG_DIR, "app_config.json")
QUEUE_PATH = os.path.join(CONFIG_DIR, "workflow_queue.json")
LOCAL_VIDEO_DIR = os.path.join(CONFIG_DIR, "local_videos")
DOWNLOAD_CACHE_DIR = os.path.join(CONFIG_DIR, "downloads")
DOWNLOAD_HISTORY_PATH = os.path.join(CONFIG_DIR, "download_history.json")
INSTANCE_LOCK_PATH = os.path.join(CONFIG_DIR, "instance.lock")
LOG_PATH = os.path.join(CONFIG_DIR, "vietsub-studio.log")

SUPPORTED_OCR_LANGS = {"ch", "en", "ja", "ko", "vi"}
SUPPORTED_OCR_MODEL_MODES = {"auto", "fast", "accurate"}
MAX_OCR_FRAMES_TO_SKIP = 10
VIDEOCR_CHUNK_SECONDS = 10 * 60
VIDEOCR_CHUNK_THRESHOLD_SECONDS = 45 * 60
VIDEOCR_PROCESS_TIMEOUT_SECONDS = 30 * 60
VIDEOCR_TRANSCODE_TIMEOUT_SECONDS = 15 * 60
TRANSLATION_CHUNK_THRESHOLD_SECONDS = 45 * 60
TRANSLATION_CHUNK_TARGET_SECONDS = 30 * 60
TRANSLATION_CHUNK_MAX_BLOCKS = 400
TRANSLATION_CHUNK_MAX_CHARS = 24_000
TRANSLATION_API_TIMEOUT_SECONDS = 180
TRANSLATION_API_MAX_ATTEMPTS = 3
TRANSLATION_API_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_TRANSLATION_INSTRUCTIONS_CHARS = 16_000
TRANSLATION_PROMPT_VERSION = "zh-vi-v4-deepseek-nonthinking"
SUPPORTED_TRANSLATION_PROVIDERS = {"notebook", "deepseek", "openrouter", "custom"}
TRANSLATION_PROVIDER_PRESETS = {
    "deepseek": {
        "label": "DeepSeek API",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-pro",
        "requires_key": True,
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "deepseek/deepseek-chat",
        "requires_key": True,
    },
    "custom": {
        "label": "API OpenAI-compatible khác",
        "base_url": "",
        "model": "",
        "requires_key": False,
    },
}

URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")
MAX_LOG_LINES = 2_000
MAX_LOG_FILE_BYTES = 2 * 1024 * 1024
MAX_LOG_BACKUPS = 2
MAX_PROJECT_NAME_LENGTH = 100
PREVIEW_TTL_SECONDS = 6 * 60 * 60
QUEUE_SCHEMA_VERSION = 2
MAX_PERSISTED_JOBS = 500
MAX_IN_MEMORY_JOBS = MAX_PERSISTED_JOBS
MAX_DOWNLOAD_HISTORY_ITEMS = 100
ALLOWED_LOCAL_VIDEO_EXTENSIONS = {".mp4"}
ALLOWED_THUMBNAIL_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_DOUYIN_THUMBNAIL_DOMAINS = ("douyinpic.com", "douyincdn.com", "byteimg.com")
ALLOWED_DOUYIN_VIDEO_DOMAINS = (
    "douyin.com",
    "iesdouyin.com",
    "douyinvod.com",
    "douyincdn.com",
    "bytevcloudcdn.com",
    "ibytedtos.com",
    "amemv.com",
    "zjcdn.com",
    "pstatp.com",
    "snssdk.com",
)
MAX_THUMBNAIL_BYTES = 20 * 1024 * 1024
MAX_LOCAL_VIDEO_BYTES = 8 * 1024 * 1024 * 1024
DOUYIN_HTTP_TIMEOUT_SECONDS = 30
DOUYIN_STREAM_TIMEOUT_SECONDS = 300
DOUYIN_MAX_HTML_BYTES = 8 * 1024 * 1024

APP_VERSION = "1.5.3"
GITHUB_REPOSITORY = "qvinh8726/VietSub-Studio"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
LATEST_RELEASE_URL = f"https://github.com/{GITHUB_REPOSITORY}/releases/latest"
UPDATE_CACHE_TTL_SECONDS = 15 * 60
UPDATE_DOWNLOAD_MAX_BYTES = 512 * 1024 * 1024
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
API_TOKEN = secrets.token_urlsafe(32)
API_TOKEN_HEADER = "X-VietSub-Token"

SRT_TIMECODE_PATTERN = re.compile(
    r"(?m)^[ \t]*(\d{2}:\d{2}:\d{2}[,.]\d{3})[ \t]*-->[ \t]*"
    r"(\d{2}:\d{2}:\d{2}[,.]\d{3})(?:[ \t]+[^\r\n]*)?[ \t]*$"
)
SRT_TIMECODE_LINE_PATTERN = re.compile(
    r"^[ \t]*\d{2}:\d{2}:\d{2}[,.]\d{3}[ \t]*-->[ \t]*"
    r"\d{2}:\d{2}:\d{2}[,.]\d{3}(?:[ \t]+[^\r\n]*)?[ \t]*$"
)
CJK_CHAR_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

TRANSLATION_GLOSSARY_PROMPT = """\
Bạn là chuyên gia dịch phụ đề Trung sang Việt, đặc biệt am hiểu ngữ cảnh game,
phim hành động khoa học viễn tưởng và video cốt truyện.

QUY TẮC BẮT BUỘC:
- Dịch tự nhiên, mượt mà, bám sát ngữ cảnh; không dịch word-by-word.
- Chọn đại từ nhân xưng theo quan hệ và sắc thái nhân vật (tôi/cậu, tao/mày,
  ngài/tôi, sếp/tôi...). Giữ cách xưng hô nhất quán trong đoạn.
- Giữ nguyên tuyệt đối số thứ tự và timestamp của từng block.
- Chỉ được tạo đúng một block SRT ở phần trả lời; không chèn lời giải thích vào block.
- Sau block code, liệt kê ngắn gọn các tên riêng/địa danh, thuật ngữ và đại từ
  nhân xưng đã dùng để hậu kiểm. Phần liệt kê này nằm ngoài block SRT.

TỪ ĐIỂN THUẬT NGỮ VÀ TÊN RIÊNG:
- Nhân vật: Saeed (赛伊德), Kami (卡米), Tiến sĩ Prometheus (罗米修斯博士),
  Zoya (佐娅), Alexei (阿列克谢), Reyes (雷斯), Claire (克莱尔), Raven (渡鸦),
  Luna (露娜), Kai (凯), Landon (兰登), Carl (卡尔), Pete (比特),
  Desmoulin (德穆兰), Hudson (哈德森), Hamuk (哈姆克), Vô Danh (无名),
  T-Boy (T 仔), Echo (回响), Vương Vũ Hạo (王宇昊), Lina (莉娜).
- Thế lực/tổ chức: Haavk (哈夫克), Ahsahra (阿萨拉), G.T.I, Delta (三角洲),
  Vệ binh Ahsahra (阿萨拉卫队), Ổ Chuột Đỏ (红鼠窝), Đội Tăng Tốc (加速部队),
  Lực lượng phản ứng nhanh (快反部队), Phi đội Cánh Đỏ / Red Wing (红翼中队).
- Hệ thống/công nghệ: Thiết bị não bộ Relink (RE LINK 脑机), Alpha (A I pha),
  Skynet (天网), Khối Mandel (曼德尔砖 / 曼德尔单元), Hệ thống Thủy Triều
  (潮汐系统), Thủy Triều Phong Tỏa (潮汐封锁), Hệ thống Cicada (鸣蝉系统),
  Lăng Tiêu (凌霄), Uy Long (威龙), Khóa lượng tử (量子密钥).
- Kế hoạch/vật phẩm: Kế hoạch [Sao Đen] (【暗星】计划), Nhiên liệu Sao Đen
  (暗星燃料), Trái Tim Châu Phi (非洲之心).
- Địa danh: HA AVK CITY, Khách sạn Diamond (钻石酒店), Thung lũng Longbow
  (长弓溪谷), Nhà tù Thủy Triều (潮汐监狱), Đập Zero (零号大坝), Thành phố
  Không gian (航天城), Trạm không gian số 36 (36 号空间站), Kênh đào Urmus
  (鸟姆斯运河), Phòng Xanh (蓝室), Phòng Đen (黑室).
- Meta: Vũ Trụ Tốc Lăng (速凌宇宙), Đột Kích (穿越火线), Ngũ Hiệp Đau Trứng
  (蛋疼五侠), Tư Lệnh (司令).
"""

PERSISTED_JOB_KEYS = (
    "id",
    "source_url",
    "video_id",
    "video_title",
    "source_video",
    "project_name",
    "ocr_lang",
    "crop_coords",
    "video_resolution",
    "preview_id",
    "use_saved_crop",
    "source_type",
    "managed_source",
    "status",
    "step",
    "error",
    "result",
    "result_ready",
    "needs_edge_login",
    "resume_step",
    "created_at",
    "started_at",
    "finished_at",
)


class WorkflowCancelled(RuntimeError):
    pass


class EdgeLoginRequired(RuntimeError):
    pass


class QueuePersistenceError(OSError):
    pass


class VideoOCRProcessError(RuntimeError):
    def __init__(self, returncode):
        self.returncode = int(returncode)
        unsigned_code = self.returncode & 0xFFFFFFFF
        if unsigned_code == 0xC0000005:
            detail = " / 0xC0000005: tiến trình native bị access violation"
        else:
            detail = ""
        super().__init__(f"Lỗi tiến trình VideOCR (Mã thoát {self.returncode}{detail})")
