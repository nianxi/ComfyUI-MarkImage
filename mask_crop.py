import math

import torch
import torch.nn.functional as F

import comfy.utils
from comfy_api.latest import io


def _as_mask(mask):
    if mask.dim() == 2:
        mask = mask.unsqueeze(0)
    if mask.dim() == 4:
        mask = mask[:, 0] if mask.shape[1] == 1 else mask[..., 0]
    return mask


def _bbox(mask):
    ys, xs = torch.where(mask > 0.01)
    if xs.numel() == 0:
        return None
    x0 = int(xs.min())
    y0 = int(ys.min())
    x1 = int(xs.max()) + 1
    y1 = int(ys.max()) + 1
    return x0, y0, x1 - x0, y1 - y0


def _crop_rect(mask, width, height, expand_scale, expand_pixels):
    box = _bbox(mask)
    if box is None:
        return 0, 0, width, height
    x, y, w, h = box
    cx = x + w / 2.0
    cy = y + h / 2.0
    half_w = w * expand_scale / 2.0 + expand_pixels
    half_h = h * expand_scale / 2.0 + expand_pixels
    x0 = math.floor(cx - half_w)
    y0 = math.floor(cy - half_h)
    x1 = math.ceil(cx + half_w)
    y1 = math.ceil(cy + half_h)
    x0 = max(0, min(width - 1, x0))
    y0 = max(0, min(height - 1, y0))
    x1 = max(x0 + 1, min(width, x1))
    y1 = max(y0 + 1, min(height, y1))
    return x0, y0, x1 - x0, y1 - y0


def _dilate(mask, pixels):
    if pixels <= 0:
        return mask
    m = mask.unsqueeze(1)
    m = F.max_pool2d(m, kernel_size=pixels * 2 + 1, stride=1, padding=pixels)
    return m.squeeze(1)


def _blur(mask, sigma):
    if sigma <= 0:
        return mask
    radius = max(1, int(math.ceil(sigma * 3)))
    x = torch.arange(-radius, radius + 1, device=mask.device, dtype=torch.float32)
    kernel = torch.exp(-0.5 * (x / sigma) ** 2)
    kernel = kernel / kernel.sum()
    m = mask.float().unsqueeze(1)
    m = F.pad(m, (radius, radius, radius, radius), mode="reflect")
    m = F.conv2d(m, kernel.view(1, 1, 1, -1))
    m = F.conv2d(m, kernel.view(1, 1, -1, 1))
    return m.squeeze(1)


def _resize_image(image, width, height):
    if int(image.shape[2]) == width and int(image.shape[1]) == height:
        return image
    return comfy.utils.common_upscale(image.movedim(-1, 1), width, height, "lanczos", "disabled").movedim(1, -1)


def _resize_mask(mask, width, height):
    if int(mask.shape[2]) == width and int(mask.shape[1]) == height:
        return mask
    m = F.interpolate(mask.unsqueeze(1), size=(height, width), mode="bilinear", align_corners=False)
    return m.squeeze(1)


class MarkImageMaskCrop(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MarkImageMaskCrop",
            display_name="标记图像-遮罩裁切",
            category="image",
            description="按遮罩外接矩形裁切原图，只切片，不缩放。",
            inputs=[
                io.Image.Input("image", display_name="图像"),
                io.Mask.Input("mask", display_name="遮罩"),
                io.Float.Input(
                    "expand_scale",
                    default=1.0,
                    min=1.0,
                    max=2.0,
                    step=0.1,
                    display_name="遮罩扩展倍数",
                    tooltip="以遮罩中心为基准，宽高同时乘这个倍数。",
                ),
                io.Int.Input(
                    "expand_pixels",
                    default=32,
                    min=1,
                    max=8192,
                    step=1,
                    display_name="遮罩扩展像素",
                    tooltip="倍数扩展之后，四边再各加这么多像素。",
                ),
            ],
            outputs=[
                io.Image.Output("image", display_name="图像"),
                io.Mask.Output("mask", display_name="遮罩"),
                io.Int.Output("x"),
                io.Int.Output("y"),
                io.Int.Output("width"),
                io.Int.Output("height"),
            ],
        )

    @classmethod
    def execute(cls, image, mask, expand_scale, expand_pixels) -> io.NodeOutput:
        mask = _as_mask(mask)
        height, width = int(image.shape[1]), int(image.shape[2])
        sample = mask[0]
        x, y, w, h = _crop_rect(sample, width, height, float(expand_scale), int(expand_pixels))
        cropped = image[:, y:y + h, x:x + w, :]
        cropped_mask = mask[:, y:y + h, x:x + w]
        if cropped_mask.shape[0] == 1 and image.shape[0] > 1:
            cropped_mask = cropped_mask.expand(image.shape[0], -1, -1)
        return io.NodeOutput(cropped, cropped_mask, x, y, w, h)


class MarkImagePasteCrop(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MarkImagePasteCrop",
            display_name="标记图像-贴回",
            category="image",
            description="把编辑后的裁切贴回原图，遮罩边缘按像素混合。",
            inputs=[
                io.Image.Input("image", display_name="原图"),
                io.Image.Input("edited", display_name="编辑图"),
                io.Mask.Input("mask", display_name="遮罩"),
                io.Int.Input("x", default=0, min=0, max=16384, force_input=True),
                io.Int.Input("y", default=0, min=0, max=16384, force_input=True),
                io.Int.Input("width", default=64, min=1, max=16384, force_input=True),
                io.Int.Input("height", default=64, min=1, max=16384, force_input=True),
                io.Int.Input(
                    "blend_pixels",
                    default=32,
                    min=0,
                    max=256,
                    step=1,
                    display_name="遮罩混合像素",
                    tooltip="遮罩边缘向外过渡的像素数。0 是硬边。",
                ),
            ],
            outputs=[
                io.Image.Output(display_name="图像"),
            ],
        )

    @classmethod
    def execute(cls, image, edited, mask, x, y, width, height, blend_pixels) -> io.NodeOutput:
        x, y = int(x), int(y)
        width, height = max(1, int(width)), max(1, int(height))
        image_h, image_w = int(image.shape[1]), int(image.shape[2])
        x0 = max(0, min(image_w - 1, x))
        y0 = max(0, min(image_h - 1, y))
        x1 = max(x0 + 1, min(image_w, x + width))
        y1 = max(y0 + 1, min(image_h, y + height))
        crop_w = x1 - x0
        crop_h = y1 - y0

        edited = _resize_image(edited, crop_w, crop_h)
        mask = _resize_mask(_as_mask(mask), crop_w, crop_h)
        if int(blend_pixels) > 0:
            mask = _dilate(mask, int(blend_pixels))
            mask = _blur(mask, int(blend_pixels) * 0.5)
        mask = mask.clamp(0, 1)

        out = image.clone()
        base = out[:, y0:y1, x0:x1, :]
        if edited.shape[0] == 1 and base.shape[0] > 1:
            edited = edited.expand(base.shape[0], -1, -1, -1)
        if mask.shape[0] == 1 and base.shape[0] > 1:
            mask = mask.expand(base.shape[0], -1, -1)
        edited = edited.to(device=base.device, dtype=base.dtype)
        weight = mask.to(device=base.device, dtype=base.dtype).unsqueeze(-1)
        if edited.shape[-1] != base.shape[-1]:
            edited = edited[..., :base.shape[-1]]
        blended = weight * edited + (1.0 - weight) * base
        out[:, y0:y1, x0:x1, :] = blended
        return io.NodeOutput(out)
