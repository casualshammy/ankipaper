import io
from pathlib import Path

from PIL import Image


def image_to_size(
  image_bytes: io.BytesIO, 
  path_out: Path, 
  target_length_bytes: int) -> bool:
  """
  Compress an image to fit within a target file size.

  Args:
    image_bytes (io.BytesIO): Input image as a byte stream.
    path_out (Path): Output path for the compressed image.
    target_length_bytes (int): Target file size in bytes.

  Returns:
    bool: True if the resulting image meets the target size, False otherwise.

  Raises:
    ValueError: If the image format is unsupported.
  """

  if image_bytes.getbuffer().nbytes <= target_length_bytes:
    path_out.write_bytes(image_bytes.read())
    return True
  
  img: Image.Image = Image.open(image_bytes)

  path_last_part = path_out.suffix.lower()
  if (path_last_part.endswith(".png")):
      img = img.convert("P", palette=Image.Palette.ADAPTIVE, colors=256)
      img.save(path_out, "PNG")
      return path_out.stat().st_size <= target_length_bytes

  if (path_last_part.endswith(".jpg")) or (path_last_part.endswith(".jpeg")):
      if img.mode != "RGB":
        img = img.convert("RGB")
      lo, hi = 20, 95
      best_bytes: bytes | None = None
      while lo <= hi:
        q = (lo + hi) // 2
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
        size = buf.tell()
        if best_bytes is None or size <= target_length_bytes:
          best_bytes = buf.getvalue()
          lo = q + 2
        else:
          hi = q - 2

      # it's impossible, but for type checking purposes we handle the None case
      if best_bytes is None:
        raise ValueError("Cannot compress image to meet the target size: best value is not calculated")
      
      path_out.write_bytes(best_bytes)
      return path_out.stat().st_size <= target_length_bytes

  raise ValueError("Unsupported image format")