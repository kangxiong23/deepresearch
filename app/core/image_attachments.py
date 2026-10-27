# Layer: Core
# File: app/core/image_attachments.py
# Responsibility: 图片附件的唯一规则——按内容识别格式、按服务端上限缩放、
#                 估算 token 占用、拼 data URL。
# Input:  原始字节（bytes）
# Output: 归一化的 {"data", "mime", "width", "height"} / token 估算 / data URL
# 禁止: 文件 IO、网络请求、UI 导入（本模块是纯函数；Pillow 惰性导入）

from __future__ import annotations

import base64

# 服务端只认这四种，且按**文件内容**判断而不是扩展名或 MIME 声明，
# 所以下面一律用魔数嗅探（docs：图像理解 - 支持的图片格式）。
SUPPORTED_MIMES = ("image/jpeg", "image/png", "image/gif", "image/webp")

# 单次请求最多带几张图。服务端上限是 600，这里按预算收紧：
# 每张最多 1024 token，4 张 ≈ 4k，已经接近一次正常问答的体量。
MAX_IMAGES_PER_REQUEST = 4

# 单图字节上限。服务端允许 32 MiB，但 base64 会膨胀 4/3，
# 且请求体总共只有 48 MiB；4 MiB × 4 张 ≈ 21 MiB，留足余量。
MAX_IMAGE_BYTES = 4 * 1024 * 1024

# 服务端会把图片缩到"总像素约 1300×1300"再计费，更大的图不会多看到任何细节，
# 只会多占带宽和请求体。所以客户端先缩到这个量级。
MAX_PIXELS = 1300 * 1300

# token 估算区间（实测：1×1 图标 ≈ 194，1600×1200 ≈ 1017，上限 1024）
MIN_TOKENS = 200
MAX_TOKENS = 1024


# ──────────────────────────────────────────────
# 识别
# ──────────────────────────────────────────────

def sniff_mime(data: bytes) -> str | None:
    """按魔数判断图片格式；不是支持的图片返回 None。"""
    if not data:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def is_image(attachment: dict | None) -> bool:
    """消息附件里的这一项是不是图片。"""
    return str((attachment or {}).get("kind") or "") == "image"


def of(attachments: list[dict] | None) -> list[dict]:
    """筛出图片附件（保持原顺序）。"""
    return [
        a for a in (attachments or [])
        if is_image(a) and str(a.get("blob_id") or "")
    ]


def supports_images(model: str) -> bool:
    """
    当前模型能不能真的看见图片。

    实测（2026-10）：deepseek-v4-flash 认得图（prompt_tokens 含图片，答对了颜色），
    deepseek-v4-pro **静默丢弃**图片——返回 200，但 prompt_tokens 里没有图片那部分，
    回答与图毫无关系。这种"不报错但不生效"最坑人，所以按模型名硬性拦下来。
    """
    return "flash" in (model or "").lower()


# ──────────────────────────────────────────────
# 归一化
# ──────────────────────────────────────────────

def prepare(data: bytes, mime: str | None = None) -> dict | None:
    """
    把原始字节变成可以直接发出去的一张图。

    快路径：本来就够小 → 原样发送（重编码只会掉质量）。
    慢路径：超出像素或字节上限 → Pillow 等比缩小后重编码。
    不是支持的图片 / 缩完仍然过大 → None（调用方给可见提示，不静默丢）。
    """
    mime = mime or sniff_mime(data)
    if mime is None or mime not in SUPPORTED_MIMES:
        return None

    width, height = _dimensions(data)
    small_enough = len(data) <= MAX_IMAGE_BYTES
    modest_pixels = width * height <= MAX_PIXELS if width and height else True

    if small_enough and modest_pixels:
        return {"data": data, "mime": mime, "width": width, "height": height}

    resized = _downscale(data, mime)
    if resized is not None:
        return resized

    # 没有 Pillow 且图片本身不超标：仍然发原图（只是可能浪费点带宽）
    if small_enough:
        return {"data": data, "mime": mime, "width": width, "height": height}
    return None


def _dimensions(data: bytes) -> tuple[int, int]:
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            return int(image.width), int(image.height)
    except Exception:
        return 0, 0


def _downscale(data: bytes, mime: str) -> dict | None:
    """等比缩到 MAX_PIXELS 以内；GIF 取首帧。缩不动返回 None。"""
    try:
        import io

        from PIL import Image
    except ImportError:
        return None

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            width, height = image.size
            pixels = max(1, width * height)
            if pixels > MAX_PIXELS:
                scale = (MAX_PIXELS / pixels) ** 0.5
                size = (max(1, int(width * scale)), max(1, int(height * scale)))
                frame = image.resize(size, Image.LANCZOS)
            else:
                frame = image.copy()

            # JPEG 保持 JPEG（体积小），其余统一 PNG（截图/透明背景不糊）
            buffer = io.BytesIO()
            if mime == "image/jpeg":
                frame.convert("RGB").save(buffer, format="JPEG", quality=85, optimize=True)
                out_mime = "image/jpeg"
            else:
                if frame.mode not in ("RGB", "RGBA"):
                    frame = frame.convert("RGBA" if _has_alpha(frame) else "RGB")
                frame.save(buffer, format="PNG", optimize=True)
                out_mime = "image/png"

            payload = buffer.getvalue()
            if len(payload) > MAX_IMAGE_BYTES:
                # PNG 压不下来（照片存成 PNG 很常见）→ 退到 JPEG
                buffer = io.BytesIO()
                frame.convert("RGB").save(buffer, format="JPEG", quality=82, optimize=True)
                payload = buffer.getvalue()
                out_mime = "image/jpeg"

            if len(payload) > MAX_IMAGE_BYTES:
                return None
            return {
                "data": payload,
                "mime": out_mime,
                "width": int(frame.width),
                "height": int(frame.height),
            }
    except Exception as e:
        print(f"[IMAGE] 缩放失败: {e}")
        return None


def _has_alpha(frame) -> bool:
    try:
        return "A" in frame.getbands()
    except Exception:
        return False


# ──────────────────────────────────────────────
# 打包 / 计费
# ──────────────────────────────────────────────

def data_url(data: bytes, mime: str) -> str:
    """内联图片的 data URL。"""
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime or 'image/png'};base64,{encoded}"


def estimate_tokens(width: int, height: int) -> int:
    """
    估算一张图吃掉的 token。

    刻意**偏高**估：预算算少了会让 _truncate_history 放行超长历史，
    最后在服务端撞 400；算多了只是保守一点。
    实测 400×300 → 210、800×600 → 333、1600×1200 → 1017。
    """
    if width <= 0 or height <= 0:
        return MAX_TOKENS
    pixel_estimate = round(width * height / 1000)
    return max(MIN_TOKENS, min(MAX_TOKENS, pixel_estimate))
