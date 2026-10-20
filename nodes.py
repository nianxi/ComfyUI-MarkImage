import numpy as np
import torch
from PIL import Image, ImageDraw
from typing_extensions import override

from comfy_api.latest import ComfyExtension, io

STROKE_WIDTH = 6

BOX_COLORS = [
    ("红色", "#FF0000"),
    ("蓝色", "#0000FF"),
    ("绿色", "#00C853"),
    ("紫色", "#9C27B0"),
    ("橙色", "#FF9800"),
    ("粉色", "#FF69B4"),
    ("青色", "#00BCD4"),
    ("黄色", "#FFEB3B"),
    ("棕色", "#8D6E63"),
    ("黄绿色", "#C6FF00"),
    ("品红", "#FF00FF"),
    ("深蓝", "#1565C0"),
    ("栗色", "#B71C1C"),
    ("橄榄", "#827717"),
    ("珊瑚色", "#FF7043"),
    ("金色", "#FFD54F"),
    ("靛蓝", "#3F51B5"),
    ("银灰", "#90A4AE"),
]


def box_style(index):
    name, hex_color = BOX_COLORS[index % len(BOX_COLORS)]
    hex_color = hex_color.lstrip("#")
    rgb = tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    return name, rgb


def _px_box(box, width, height):
    if not isinstance(box, dict):
        return None
    x = int(round(box.get("x", 0)))
    y = int(round(box.get("y", 0)))
    w = int(round(box.get("width", 0)))
    h = int(round(box.get("height", 0)))
    if w < 0:
        x, w = x + w, -w
    if h < 0:
        y, h = y + h, -h
    if w <= 0 or h <= 0:
        return None
    x1 = max(0, min(width - 1, x))
    y1 = max(0, min(height - 1, y))
    x2 = max(0, min(width - 1, x + w - 1))
    y2 = max(0, min(height - 1, y + h - 1))
    if x2 <= x1 or y2 <= y1:
        return None
    meta = box.get("metadata") if isinstance(box.get("metadata"), dict) else {}
    desc = str(meta.get("desc") or meta.get("text") or "").strip()
    return x1, y1, x2, y2, desc


def normalize_boxes(editor_state):
    if isinstance(editor_state, dict):
        return [editor_state]
    if isinstance(editor_state, list):
        return [box for box in editor_state if isinstance(box, dict)]
    return []


def draw_strokes(image, boxes):
    rgb = image.convert("RGB")
    draw = ImageDraw.Draw(rgb)
    width, height = rgb.size
    for i, box in enumerate(boxes):
        parsed = _px_box(box, width, height)
        if parsed is None:
            continue
        x1, y1, x2, y2, _ = parsed
        _, color = box_style(i)
        # Grow the rect so a 6px stroke sits outside the box edge.
        pad = STROKE_WIDTH
        ox1 = max(0, x1 - pad)
        oy1 = max(0, y1 - pad)
        ox2 = min(width - 1, x2 + pad)
        oy2 = min(height - 1, y2 + pad)
        draw.rectangle([ox1, oy1, ox2, oy2], outline=color, width=STROKE_WIDTH)
    return rgb


def tensor_to_pils(image):
    frames = []
    arr = image.detach().cpu().numpy()
    if arr.ndim == 3:
        arr = arr[None]
    for frame in arr:
        pixels = np.clip(frame * 255.0, 0, 255).astype(np.uint8)
        if pixels.shape[-1] == 1:
            pixels = np.repeat(pixels, 3, axis=-1)
        frames.append(Image.fromarray(pixels[..., :3], mode="RGB"))
    return frames


def pils_to_tensor(frames, device, dtype):
    stacked = np.stack(
        [np.asarray(frame.convert("RGB"), dtype=np.float32) / 255.0 for frame in frames],
        axis=0,
    )
    return torch.from_numpy(stacked).to(device=device, dtype=dtype)


def boxes_to_masks(boxes, width, height, device):
    parsed = []
    for box in boxes:
        item = _px_box(box, width, height)
        if item is not None:
            parsed.append(item)
    if not parsed:
        masks = torch.zeros((1, height, width), dtype=torch.float32, device=device)
        return masks, 0, 0
    masks = torch.zeros((len(parsed), height, width), dtype=torch.float32, device=device)
    pad = STROKE_WIDTH * 2
    max_w = 0
    max_h = 0
    for i, (bx0, by0, bx1, by1, _) in enumerate(parsed):
        x0 = max(0, bx0 - pad)
        y0 = max(0, by0 - pad)
        x1 = min(width - 1, bx1 + pad)
        y1 = min(height - 1, by1 + pad)
        masks[i, y0:y1 + 1, x0:x1 + 1] = 1.0
        max_w = max(max_w, x1 - x0 + 1)
        max_h = max(max_h, y1 - y0 + 1)
    return masks, max_w, max_h


def boxes_to_array(boxes, width, height):
    items = []
    for i, box in enumerate(boxes):
        parsed = _px_box(box, width, height)
        if parsed is None:
            continue
        x1, y1, x2, y2, _ = parsed
        meta = box.get("metadata") if isinstance(box.get("metadata"), dict) else {}
        color_name, _ = box_style(i)
        box_id = box.get("id")
        if box_id is None:
            box_id = meta.get("id")
        if box_id is None:
            box_id = i + 1
        items.append({
            "id": box_id,
            "x": x1,
            "y": y1,
            "width": x2 - x1 + 1,
            "height": y2 - y1 + 1,
            "desc": str(meta.get("desc") or "").strip(),
            "text": str(meta.get("text") or "").strip(),
            "color": color_name,
        })
    return items


class MarkImage(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        editor_state = io.BoundingBoxes.Input(
            "editor_state",
            socketless=True,
            tooltip="在图上拖拽画框，点选后填写该区域要执行的操作。",
        )
        return io.Schema(
            node_id="MarkImage",
            display_name="标记图像",
            category="image",
            description="在图像上画框并填写操作，输出描边图像和选区遮罩。",
            search_aliases=["Mark Image", "bbox", "bounding box", "标记", "描边"],
            inputs=[
                io.Image.Input(
                    "background",
                    display_name="图像",
                    tooltip="要标记的原图。",
                ),
                io.Int.Input(
                    "width",
                    default=1024,
                    min=64,
                    max=16384,
                    step=16,
                    tooltip="画布宽度。接入图像后会按原图像素自动对齐。",
                ),
                io.Int.Input(
                    "height",
                    default=1024,
                    min=64,
                    max=16384,
                    step=16,
                    tooltip="画布高度。接入图像后会按原图像素自动对齐。",
                ),
                editor_state,
                io.BoundingBoxes.Input(
                    "last_incoming",
                    optional=True,
                    tooltip="画布内部状态，无需填写。",
                ),
            ],
            outputs=[
                io.Image.Output(display_name="图像"),
                io.Mask.Output("masks", display_name="masks", tooltip="每个框一张与原图同尺寸的遮罩，已包含描边。"),
                io.Int.Output("maxMaskW", display_name="maxMaskW", tooltip="所有遮罩中的最大宽度。"),
                io.Int.Output("maxMaskH", display_name="maxMaskH", tooltip="所有遮罩中的最大高度。"),
                io.Array.Output("array", display_name="array", tooltip="每个框的 id、坐标、desc、text、color。"),
                io.Int.Output("length", display_name="length", tooltip="array 子项数量。"),
            ],
        )

    @classmethod
    def execute(cls, background, width, height, editor_state=None, last_incoming=None) -> io.NodeOutput:
        frames = tensor_to_pils(background)
        img_w, img_h = frames[0].size
        boxes = normalize_boxes(editor_state)
        marked = [draw_strokes(frame, boxes) for frame in frames]
        out = pils_to_tensor(marked, background.device, background.dtype)
        masks, max_mask_w, max_mask_h = boxes_to_masks(boxes, img_w, img_h, background.device)
        array = boxes_to_array(boxes, img_w, img_h)
        return io.NodeOutput(out, masks, max_mask_w, max_mask_h, array, len(array))


class MarkImageGetBox(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MarkImageGetBox",
            display_name="标记图像子项",
            category="image",
            description="按索引取出标记数组中一框的全部属性，以及对应遮罩。",
            inputs=[
                io.Array.Input("array"),
                io.Int.Input("index", default=0, min=0),
                io.Mask.Input("masks", optional=True, tooltip="标记图像输出的 masks，按同一索引取出一张。"),
            ],
            outputs=[
                io.String.Output("color", display_name="color"),
                io.String.Output("desc", display_name="desc"),
                io.Int.Output("id", display_name="id"),
                io.Int.Output("x", display_name="x"),
                io.Int.Output("y", display_name="y"),
                io.Int.Output("width", display_name="width"),
                io.Int.Output("height", display_name="height"),
                io.String.Output("text", display_name="text"),
                io.Mask.Output("mask", display_name="mask"),
            ],
        )

    @classmethod
    def execute(cls, array, index, masks=None) -> io.NodeOutput:
        idx = max(0, int(index))
        empty_mask = torch.zeros((1, 1, 1), dtype=torch.float32)
        if masks is not None:
            mask_batch = masks
            if mask_batch.ndim == 4:
                mask_batch = mask_batch[:, 0]
            if mask_batch.ndim == 2:
                mask_batch = mask_batch.unsqueeze(0)
            empty_mask = torch.zeros((1, mask_batch.shape[-2], mask_batch.shape[-1]), dtype=mask_batch.dtype, device=mask_batch.device)
            mi = min(idx, mask_batch.shape[0] - 1)
            mask_out = mask_batch[mi:mi + 1]
        else:
            mask_out = empty_mask
        empty = ("", "", 0, 0, 0, 0, 0, "", mask_out)
        if not array:
            return io.NodeOutput(*empty)
        item = array[min(idx, len(array) - 1)]
        if not isinstance(item, dict):
            return io.NodeOutput(*empty)
        try:
            box_id = int(item.get("id") or 0)
        except (TypeError, ValueError):
            box_id = 0
        return io.NodeOutput(
            str(item.get("color") or ""),
            str(item.get("desc") or ""),
            box_id,
            int(item.get("x") or 0),
            int(item.get("y") or 0),
            int(item.get("width") or 0),
            int(item.get("height") or 0),
            str(item.get("text") or ""),
            mask_out,
        )


class MarkImageToBatch(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MarkImageToBatch",
            display_name="标记图像列表转批次",
            category="image",
            description="把循环输出的图像列表拼成一个批次，供 Inpaint Stitch 使用。",
            is_input_list=True,
            inputs=[
                io.Image.Input("images", tooltip="来自 End Loop 的图像列表。"),
            ],
            outputs=[
                io.Image.Output(display_name="图像"),
            ],
        )

    @classmethod
    def execute(cls, images) -> io.NodeOutput:
        if isinstance(images, torch.Tensor):
            return io.NodeOutput(images)
        chunks = []
        for item in images:
            if isinstance(item, torch.Tensor):
                chunks.append(item)
            elif isinstance(item, (list, tuple)) and item and isinstance(item[0], torch.Tensor):
                chunks.append(item[0])
        if not chunks:
            raise ValueError("没有可拼接的图像")
        return io.NodeOutput(torch.cat(chunks, dim=0))


def border_weight(height, width, border, device):
    span = max(1, min(int(border), height // 2, width // 2))
    weight = torch.zeros((height, width), dtype=torch.float32, device=device)
    weight[:span, :] = 1
    weight[-span:, :] = 1
    weight[:, :span] = 1
    weight[:, -span:] = 1
    return weight


def resize_image_frame(frame, height, width):
    if frame.shape[0] == height and frame.shape[1] == width:
        return frame
    scaled = torch.nn.functional.interpolate(
        frame.permute(2, 0, 1).unsqueeze(0),
        size=(height, width),
        mode="bilinear",
        align_corners=False,
    )
    return scaled[0].permute(1, 2, 0)


def resize_mask_frame(mask, height, width):
    if mask.shape[-2] == height and mask.shape[-1] == width:
        return mask
    scaled = torch.nn.functional.interpolate(
        mask.reshape(1, 1, mask.shape[-2], mask.shape[-1]).float(),
        size=(height, width),
        mode="nearest",
    )
    return scaled[0, 0]


def correct_frame(edited, reference, border, strength, mask):
    height, width = reference.shape[0], reference.shape[1]
    edited = resize_image_frame(edited, height, width)
    channels = min(edited.shape[-1], reference.shape[-1])
    edited = edited[..., :channels].float()
    reference = reference[..., :channels].float()
    context = torch.ones((height, width), dtype=torch.float32, device=edited.device)
    if mask is not None:
        context = (resize_mask_frame(mask.float(), height, width) < 0.5).to(context.dtype)
    sample = border_weight(height, width, border, edited.device) * context
    if float(sample.sum()) < 32:
        sample = context
    delta = reference - edited
    stable = sample * (delta.abs().amax(dim=-1) <= 0.12).to(sample.dtype)
    if float(stable.sum()) < 32:
        return edited
    picked = delta[stable > 0]
    offset = picked.median(dim=0).values * float(strength)
    return (edited + offset).clamp(0, 1)


def correct_batch(image, reference, border, strength, mask):
    if mask is not None:
        if mask.ndim == 4:
            mask = mask[:, 0]
        if mask.ndim == 2:
            mask = mask.unsqueeze(0)
    frames = []
    for index in range(image.shape[0]):
        ref = reference[min(index, reference.shape[0] - 1)]
        region = None if mask is None else mask[min(index, mask.shape[0] - 1)]
        frames.append(correct_frame(image[index], ref, border, strength, region))
    return torch.stack(frames, dim=0).to(device=image.device, dtype=image.dtype)


class MarkImageBorderColor(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MarkImageBorderColor",
            display_name="标记图像边缘校色",
            category="image",
            description="只根据四周里几乎没被改动的像素，去掉模型带来的轻微偏色。主体改色不会被算进校正。",
            search_aliases=["border color", "color match", "边缘校色", "偏色"],
            inputs=[
                io.Image.Input("image", display_name="编辑图", tooltip="Image Edit 输出的裁剪结果。"),
                io.Image.Input("reference", display_name="裁剪图", tooltip="Inpaint Crop 输出的裁剪原图。"),
                io.Mask.Input(
                    "mask",
                    optional=True,
                    display_name="裁剪遮罩",
                    tooltip="接 Inpaint Crop 的 cropped mask 后，边缘取样会避开要修改的区域。",
                ),
                io.Int.Input("border", default=32, min=1, max=256, display_name="边缘宽度"),
                io.Float.Input("strength", default=1.0, min=0.0, max=1.0, step=0.01, display_name="强度"),
            ],
            outputs=[
                io.Image.Output(display_name="图像"),
            ],
        )

    @classmethod
    def execute(cls, image, reference, border, strength, mask=None) -> io.NodeOutput:
        return io.NodeOutput(correct_batch(image, reference, border, strength, mask))


class MarkImageExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [MarkImage, MarkImageGetBox, MarkImageToBatch, MarkImageBorderColor]


async def comfy_entrypoint() -> MarkImageExtension:
    return MarkImageExtension()

