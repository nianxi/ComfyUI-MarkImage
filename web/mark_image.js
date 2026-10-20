import { app } from "../../scripts/app.js";

const COLORS = [
  "#FF0000",
  "#0000FF",
  "#00C853",
  "#9C27B0",
  "#FF9800",
  "#FF69B4",
  "#00BCD4",
  "#FFEB3B",
  "#8D6E63",
  "#C6FF00",
  "#FF00FF",
  "#1565C0",
  "#B71C1C",
  "#827717",
  "#FF7043",
  "#FFD54F",
  "#3F51B5",
  "#90A4AE",
];

const HIDDEN_WIDGETS = new Set(["last_incoming"]);
const STYLE_ID = "comfy-mark-image-style";

function ensureStyle() {
  if (document.getElementById(STYLE_ID)) {
    return;
  }
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = `
    .comfy-mark-image [data-testid="bounding-boxes"] .flex.h-8.items-center {
      display: none !important;
    }
    .comfy-mark-image [data-testid="bounding-boxes"] .flex.items-center.gap-2 {
      display: none !important;
    }
  `;
  document.head.appendChild(style);
}

function colorizeBoxes(value) {
  if (!Array.isArray(value)) {
    return value;
  }
  let changed = false;
  const next = value.map((box, index) => {
    const color = COLORS[index % COLORS.length];
    const metadata = box?.metadata && typeof box.metadata === "object" ? box.metadata : {};
    const palette = Array.isArray(metadata.palette) ? metadata.palette : [];
    if (palette[0] === color && metadata.type === "obj") {
      return box;
    }
    changed = true;
    return {
      ...box,
      metadata: {
        ...metadata,
        type: "obj",
        palette: [color],
      },
    };
  });
  return changed ? next : value;
}

function tagNodeElement(node) {
  const selectors = [
    `[data-node-id="${node.id}"]`,
    `[data-nodeid="${node.id}"]`,
  ];
  for (const selector of selectors) {
    const el = document.querySelector(selector);
    if (el) {
      el.classList.add("comfy-mark-image");
      return true;
    }
  }
  if (node.vue?.$el) {
    node.vue.$el.classList.add("comfy-mark-image");
    return true;
  }
  return false;
}

app.registerExtension({
  name: "ComfyUI.MarkImage",
  nodeCreated(node) {
    if (node.constructor.comfyClass !== "MarkImage") {
      return;
    }
    ensureStyle();
    const [w, h] = node.size;
    node.setSize([Math.max(w, 420), Math.max(h, 560)]);
    for (const widget of node.widgets ?? []) {
      if (HIDDEN_WIDGETS.has(widget.name)) {
        widget.hidden = true;
      }
    }

    const editor = node.widgets?.find((widget) => widget.name === "editor_state");
    if (editor) {
      const original = editor.callback;
      editor.callback = function (value) {
        const colored = colorizeBoxes(value);
        if (colored !== value) {
          editor.value = colored;
        }
        if (original) {
          return original.call(this, colored);
        }
      };
    }

    const tryTag = () => tagNodeElement(node);
    tryTag();
    requestAnimationFrame(tryTag);
    setTimeout(tryTag, 200);
    setTimeout(tryTag, 800);
  },
});
