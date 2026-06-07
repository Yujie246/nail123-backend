import { createServer } from "node:http";
import { readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { extname, join, resolve } from "node:path";

const root = resolve(new URL("..", import.meta.url).pathname);
const docsDir = join(root, "docs");
const selectionPath = join(docsDir, "share-card-roi-selection.json");
const port = Number(process.env.ROI_PORT ?? 5174);

const contentTypes = {
  ".html": "text/html; charset=utf-8",
  ".svg": "image/svg+xml; charset=utf-8",
  ".json": "application/json; charset=utf-8",
};

const server = createServer(async (request, response) => {
  try {
    if (!request.url) {
      respond(response, 400, "Missing URL");
      return;
    }

    const url = new URL(request.url, `http://${request.headers.host}`);
    if (request.method === "GET" && (url.pathname === "/" || url.pathname === "/annotator")) {
      await serveFile(response, join(docsDir, "share-card-roi-annotator.html"));
      return;
    }

    if (request.method === "GET" && url.pathname === "/template.svg") {
      await serveFile(response, join(docsDir, "share-card-roi-template.svg"));
      return;
    }

    if (request.method === "GET" && url.pathname === "/selection") {
      if (!existsSync(selectionPath)) {
        respondJson(response, 200, null);
        return;
      }
      await serveFile(response, selectionPath);
      return;
    }

    if (request.method === "POST" && url.pathname === "/save") {
      const body = await readRequestBody(request);
      const payload = JSON.parse(body);
      const normalized = normalizeSelection(payload);
      await writeFile(selectionPath, `${JSON.stringify(normalized, null, 2)}\n`, "utf8");
      respondJson(response, 200, { ok: true, file: "docs/share-card-roi-selection.json" });
      return;
    }

    respond(response, 404, "Not found");
  } catch (error) {
    respondJson(response, 500, { ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});

server.listen(port, "127.0.0.1", () => {
  console.log(`ROI annotator ready: http://127.0.0.1:${port}/annotator`);
  console.log("Saves to docs/share-card-roi-selection.json");
});

async function serveFile(response, filePath) {
  const content = await readFile(filePath);
  response.writeHead(200, { "Content-Type": contentTypes[extname(filePath)] ?? "application/octet-stream" });
  response.end(content);
}

function respond(response, status, text) {
  response.writeHead(status, { "Content-Type": "text/plain; charset=utf-8" });
  response.end(text);
}

function respondJson(response, status, payload) {
  response.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
  response.end(JSON.stringify(payload));
}

function readRequestBody(request) {
  return new Promise((resolveBody, rejectBody) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk) => {
      body += chunk;
      if (body.length > 1_000_000) {
        request.destroy();
        rejectBody(new Error("Request body too large"));
      }
    });
    request.on("end", () => resolveBody(body));
    request.on("error", rejectBody);
  });
}

function normalizeSelection(payload) {
  const regions = Array.isArray(payload?.regions) ? payload.regions : [];
  return {
    version: 1,
    savedAt: new Date().toISOString(),
    canvas: { width: 1080, height: 1350 },
    regions: regions.map((region, index) => ({
      id: String(region.id || `roi-${index + 1}`),
      label: String(region.label || "未命名区域"),
      shape: region.shape === "ellipse" ? "ellipse" : "rect",
      x: clampNumber(region.x, 0, 1080),
      y: clampNumber(region.y, 0, 1350),
      width: clampNumber(region.width, 1, 1080),
      height: clampNumber(region.height, 1, 1350),
      note: String(region.note || ""),
    })),
  };
}

function clampNumber(value, min, max) {
  const number = Number(value);
  if (!Number.isFinite(number)) return min;
  return Math.round(Math.min(max, Math.max(min, number)));
}
