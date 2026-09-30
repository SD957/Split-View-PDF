#!/usr/bin/env node

import { createRequire } from "node:module";
import readline from "node:readline";

const require = createRequire(import.meta.url);

let pdfjsLib = null;
let pdfjsLoadError = null;
let createCanvasFn = null;
let canvasLoadError = null;
let SkiaPath2D = null; // Captured after pdfjs loads

async function loadPdfjs() {
  if (pdfjsLib || pdfjsLoadError) {
    return;
  }

  const attempts = [];

  try {
    const mod = await import("pdfjs-dist/legacy/build/pdf.mjs");
    pdfjsLib = mod.default ?? mod;
    // Capture the Skia Path2D that pdfjs-dist bundles as globalThis.Path2D
    if (globalThis.Path2D && typeof globalThis.Path2D.prototype.toSVGString === "function") {
      SkiaPath2D = globalThis.Path2D;
    }
    return;
  } catch (err) {
    attempts.push(`legacy mjs: ${err && err.message ? err.message : String(err)}`);
  }

  try {
    const mod = await import("pdfjs-dist");
    pdfjsLib = mod.default ?? mod;
    if (globalThis.Path2D && typeof globalThis.Path2D.prototype.toSVGString === "function") {
      SkiaPath2D = globalThis.Path2D;
    }
    return;
  } catch (err) {
    attempts.push(`package import: ${err && err.message ? err.message : String(err)}`);
  }

  pdfjsLoadError = new Error(`Unable to load pdfjs-dist (${attempts.join(" | ")})`);
}

/**
 * patchContext — Monkey-patch a canvas 2D context so that ctx.fill(path),
 * ctx.stroke(path) and ctx.clip(path) work even when `path` is a Skia/CanvasKit
 * Path object (which pdfjs-dist 5.x bundles as globalThis.Path2D).
 *
 * node-canvas does NOT support the Path2D overload of fill/stroke/clip, so
 * calls like ctx.fill(skiaPath) silently do nothing, resulting in blank renders.
 *
 * The fix: intercept such calls, convert the Skia path to an SVG path string
 * via path.toSVGString(), replay the SVG commands as native canvas operations
 * (moveTo, lineTo, bezierCurveTo, etc.), then call the original fill/stroke/clip.
 */
function patchContext(ctx) {
  if (!SkiaPath2D || ctx._pdfdiff_patched) return ctx;
  ctx._pdfdiff_patched = true;

  const origFill = ctx.fill.bind(ctx);
  const origStroke = ctx.stroke.bind(ctx);
  const origClip = ctx.clip.bind(ctx);

  function replayPath(path) {
    const svg = path.toSVGString();
    ctx.beginPath();
    const re = /([MmLlHhVvCcSsQqTtAaZz])([^MmLlHhVvCcSsQqTtAaZz]*)/g;
    let match, cx = 0, cy = 0;
    while ((match = re.exec(svg)) !== null) {
      const cmd = match[1];
      const nums = match[2].trim().split(/[\s,]+/).filter(Boolean).map(Number);
      switch (cmd) {
        case 'M': ctx.moveTo(nums[0], nums[1]); cx = nums[0]; cy = nums[1]; break;
        case 'L': ctx.lineTo(nums[0], nums[1]); cx = nums[0]; cy = nums[1]; break;
        case 'H': ctx.lineTo(nums[0], cy); cx = nums[0]; break;
        case 'V': ctx.lineTo(cx, nums[0]); cy = nums[0]; break;
        case 'C':
          ctx.bezierCurveTo(nums[0], nums[1], nums[2], nums[3], nums[4], nums[5]);
          cx = nums[4]; cy = nums[5]; break;
        case 'Q':
          ctx.quadraticCurveTo(nums[0], nums[1], nums[2], nums[3]);
          cx = nums[2]; cy = nums[3]; break;
        case 'Z': case 'z': ctx.closePath(); break;
      }
    }
  }

  function isSkiaPath(arg) {
    return arg != null && arg instanceof SkiaPath2D;
  }

  ctx.fill = function (...args) {
    if (args.length > 0 && isSkiaPath(args[0])) {
      replayPath(args[0]);
      return args.length > 1 ? origFill(args[1]) : origFill();
    }
    return origFill(...args);
  };

  ctx.stroke = function (...args) {
    if (args.length > 0 && isSkiaPath(args[0])) {
      replayPath(args[0]);
      return origStroke();
    }
    return origStroke(...args);
  };

  ctx.clip = function (...args) {
    if (args.length > 0 && isSkiaPath(args[0])) {
      replayPath(args[0]);
      return args.length > 1 ? origClip(args[1]) : origClip();
    }
    return origClip(...args);
  };

  return ctx;
}

function loadCanvas() {
  if (typeof createCanvasFn === "function" || canvasLoadError) {
    return;
  }

  try {
    const canvasMod = require("canvas");
    if (canvasMod && typeof canvasMod.createCanvas === "function") {
      createCanvasFn = canvasMod.createCanvas;
    } else {
      canvasLoadError = new Error("canvas module loaded but createCanvas is missing");
    }
  } catch (err) {
    canvasLoadError = err;
  }
}

/**
 * NodeCanvasFactory — required so pdf.js can create internal helper canvases
 * (transparency groups, patterns, SMask, image compositing) in Node.js.
 * Without this, pdf.js falls back to document.createElement("canvas") which
 * does not exist in Node, causing all non-text painting to silently fail.
 */
class NodeCanvasFactory {
  create(width, height) {
    const canvas = createCanvasFn(width, height);
    const context = patchContext(canvas.getContext("2d"));
    return { canvas, context };
  }

  reset(canvasAndContext, width, height) {
    canvasAndContext.canvas.width = width;
    canvasAndContext.canvas.height = height;
  }

  destroy(canvasAndContext) {
    if (canvasAndContext.canvas) {
      canvasAndContext.canvas.width = 0;
      canvasAndContext.canvas.height = 0;
    }
    canvasAndContext.canvas = null;
    canvasAndContext.context = null;
  }
}

function toWarningMessage(err) {
  if (!err) {
    return "Unknown warning";
  }
  if (err instanceof Error) {
    return `${err.name}: ${err.message}`;
  }
  return String(err);
}

function writeResponse(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

function normalizeTextItems(items) {
  if (!Array.isArray(items)) {
    return "";
  }

  const chunks = [];
  for (const item of items) {
    if (item && typeof item.str === "string") {
      chunks.push(item.str);
    }
  }
  return chunks.join(" ").replace(/\s+/g, " ").trim();
}

async function extractText(dataBytes) {
  const loadingTask = pdfjsLib.getDocument({
    data: dataBytes,
    disableWorker: true,
    useSystemFonts: true,
  });

  const doc = await loadingTask.promise;
  try {
    const pages = [];
    for (let pageNo = 1; pageNo <= doc.numPages; pageNo += 1) {
      const page = await doc.getPage(pageNo);
      try {
        const content = await page.getTextContent();
        pages.push(normalizeTextItems(content.items));
      } finally {
        try {
          page.cleanup(true);
        } catch {
          // best-effort cleanup only
        }
      }
    }
    return pages;
  } finally {
    await doc.destroy();
  }
}

async function renderPage(dataBytes, pageIndex, dpi) {
  loadCanvas();
  if (typeof createCanvasFn !== "function") {
    const reason = canvasLoadError ? toWarningMessage(canvasLoadError) : "canvas module not installed";
    throw new Error(`Rendering is unavailable: ${reason}`);
  }

  const loadingTask = pdfjsLib.getDocument({
    data: dataBytes,
    disableWorker: true,
    useSystemFonts: true,
  });

  const doc = await loadingTask.promise;
  try {
    if (pageIndex < 0 || pageIndex >= doc.numPages) {
      throw new Error(`IndexError: page_num ${pageIndex} out of range`);
    }

    const page = await doc.getPage(pageIndex + 1);
    try {
      const viewport = page.getViewport({ scale: dpi / 72.0 });
      const width = Math.max(1, Math.ceil(viewport.width));
      const height = Math.max(1, Math.ceil(viewport.height));

      const canvas = createCanvasFn(width, height);
      const context = patchContext(canvas.getContext("2d", { alpha: false }));
      context.fillStyle = "#ffffff";
      context.fillRect(0, 0, width, height);

      const renderTask = page.render({
        canvasContext: context,
        viewport,
        background: "rgb(255,255,255)",
        canvasFactory: new NodeCanvasFactory(),
      });
      await renderTask.promise;

      return canvas.toBuffer("image/png").toString("base64");
    } finally {
      try {
        page.cleanup(true);
      } catch {
        // best-effort cleanup only
      }
    }
  } finally {
    await doc.destroy();
  }
}

async function handleRequest(req) {
  const warnings = [];

  const originalWarn = console.warn;
  console.warn = (...args) => {
    warnings.push(args.map((v) => String(v)).join(" "));
  };

  try {
    await loadPdfjs();
    if (pdfjsLoadError) {
      throw pdfjsLoadError;
    }

    if (!req || typeof req !== "object") {
      throw new Error("Invalid request payload");
    }

    const action = req.action;

    if (action === "ping") {
      loadCanvas();
      if (canvasLoadError) {
        warnings.push(toWarningMessage(canvasLoadError));
      }
      return {
        success: true,
        supports_render: typeof createCanvasFn === "function",
        warnings,
      };
    }

    const pdfB64 = typeof req.pdf === "string" ? req.pdf : "";
    const pdfBytes = new Uint8Array(Buffer.from(pdfB64, "base64"));

    if (action === "extract_text") {
      const pages = await extractText(pdfBytes);
      return { success: true, pages, warnings };
    }

    if (action === "render") {
      const page = Number.isFinite(req.page) ? Number(req.page) : 0;
      const dpi = Number.isFinite(req.dpi) ? Number(req.dpi) : 150;
      const png = await renderPage(pdfBytes, page, dpi);
      return { success: true, png, warnings };
    }

    throw new Error(`Unsupported action: ${String(action)}`);
  } finally {
    console.warn = originalWarn;
  }
}

async function handleLine(line) {
  let req;
  try {
    req = JSON.parse(line);
  } catch (err) {
    writeResponse({ success: false, error: `JSONParseError: ${err.message}`, warnings: [] });
    return;
  }

  try {
    const res = await handleRequest(req);
    writeResponse(res);
  } catch (err) {
    writeResponse({
      success: false,
      error: `${err && err.name ? err.name : "Error"}: ${err && err.message ? err.message : String(err)}`,
      warnings: [],
    });
  }
}

const rl = readline.createInterface({
  input: process.stdin,
  crlfDelay: Infinity,
  terminal: false,
});

let queue = Promise.resolve();
rl.on("line", (line) => {
  queue = queue.then(() => handleLine(line)).catch((err) => {
    writeResponse({
      success: false,
      error: `${err && err.name ? err.name : "Error"}: ${err && err.message ? err.message : String(err)}`,
      warnings: [],
    });
  });
});
