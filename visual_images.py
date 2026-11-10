"""Worker-side logical-window capture and bounded-memory template matching."""
import io
import math
import subprocess
import sys

MAX_PIXELS = 16_000_000


def normalize(image, ref_size):
    """Normalize centered reference-aspect content, matching login.map_point."""
    from PIL import Image
    if (not isinstance(ref_size, (tuple, list)) or len(ref_size) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in ref_size)
            or math.prod(ref_size) > MAX_PIXELS):
        raise ValueError('Invalid or excessively large reference geometry.')
    if image.width < 1 or image.height < 1 or image.width * image.height > MAX_PIXELS:
        raise ValueError('Image is too large or empty.')
    width, height = ref_size
    scale = min(image.width / width, image.height / height)
    content_width, content_height = width * scale, height * scale
    left, top = (image.width-content_width)/2, (image.height-content_height)/2
    # resize(box=...) preserves fractional centered crop boundaries, avoiding
    # a rounded crop shifting the inverse of the existing input mapping.
    return image.resize((width, height), Image.Resampling.BILINEAR,
                        box=(left, top, left+content_width, top+content_height))


def capture(window):
    from PIL import Image
    x, y, w, h = (int(getattr(window, k)) for k in ('x', 'y', 'width', 'height'))
    if w <= 0 or h <= 0 or w * h > MAX_PIXELS:
        raise ValueError('Invalid or excessively large game capture geometry.')
    if sys.platform == 'win32':
        from PIL import ImageGrab
        return ImageGrab.grab(bbox=(x, y, x+w, y+h), all_screens=True).convert('RGB')
    result = subprocess.run(['grim', '-g', f'{x},{y} {w}x{h}', '-s', '1', '-'],
                            capture_output=True, timeout=15, check=True)
    with Image.open(io.BytesIO(result.stdout)) as image:
        return image.convert('RGB')


def match(image, template, region=None, threshold=.9, ref_size=None):
    """Return best normalized RGB RMS similarity; FFT avoids sliding arrays."""
    from PIL import Image
    import numpy as np
    if template.width * template.height > MAX_PIXELS:
        raise ValueError('Template is too large; use a smaller template or search region.')
    if not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError('Threshold must be between zero and one.')
    if ref_size is not None:
        image = normalize(image, ref_size)
    if image.width * image.height > MAX_PIXELS:
        raise ValueError('Image is too large.')
    ox = oy = 0
    if region is not None:
        ox, oy, w, h = region
        if min(ox, oy) < 0 or min(w, h) < 1 or ox+w > image.width or oy+h > image.height:
            raise ValueError('Search region is outside reference geometry.')
        image = image.crop((ox, oy, ox+w, oy+h))
    H, W = image.height, image.width
    h, w = template.height, template.width
    if h > H or w > W:
        return None
    if H * W > 4_000_000:
        raise ValueError('Search area exceeds four million pixels; select a smaller region.')
    shape = (H+h-1, W+w-1)
    if math.prod(shape) > 5_000_000:
        raise ValueError('FFT search is too large; select a smaller search region or template.')
    a = np.asarray(image.convert('RGB'), dtype=np.float32) / 255
    b = np.asarray(template.convert('RGB'), dtype=np.float32) / 255
    cross = np.zeros((H-h+1, W-w+1), dtype=np.float64)
    for channel in range(3):
        conv = np.fft.irfft2(np.fft.rfft2(a[:, :, channel], s=shape) *
                             np.fft.rfft2(b[::-1, ::-1, channel], s=shape), s=shape)
        cross += conv[h-1:H, w-1:W]
    integral = np.pad(np.sum(a*a, axis=2, dtype=np.float64), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    squares = integral[h:, w:] - integral[:-h, w:] - integral[h:, :-w] + integral[:-h, :-w]
    errors = np.maximum(0, squares + np.sum(b*b, dtype=np.float64) - 2*cross)
    iy, ix = np.unravel_index(np.argmin(errors), errors.shape)
    score = 1 - math.sqrt(float(errors[iy, ix]) / (h*w*3))
    return dict(x=ox+int(ix)+w/2, y=oy+int(iy)+h/2, score=score) if score >= threshold else None


def recognize(window, step, store, workflow):
    from PIL import Image
    with Image.open(store.image_path(step['image'])) as template:
        if template.width * template.height > MAX_PIXELS:
            raise ValueError('Template is too large.')
        return match(capture(window), template, step.get('region'), step.get('threshold', .9),
                     (workflow['ref_width'], workflow['ref_height']))
