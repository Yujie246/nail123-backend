import { spawn } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";

const projectRoot = resolve(new URL("..", import.meta.url).pathname);
const appJsPath = join(projectRoot, "Nail-main", "app.js");
const outputDir = process.env.TRYON_BATCH_OUTPUT_DIR || "/private/tmp/nail_tryon_all_styles";
const endpoint = process.env.TRYON_BATCH_ENDPOINT || "http://127.0.0.1:8002/api/generate-nail-tryon-v2";
const handPath = resolve(projectRoot, process.env.TRYON_BATCH_HAND_IMAGE || "Nail-main/public/assets/hand-before.jpg");
const maxSeconds = Number(process.env.TRYON_BATCH_MAX_SECONDS || "720");

function mimeFor(path) {
  const lower = path.toLowerCase();
  if (lower.endsWith(".jpg") || lower.endsWith(".jpeg")) return "image/jpeg";
  if (lower.endsWith(".webp")) return "image/webp";
  return "image/png";
}

async function dataUrl(path) {
  const bytes = await readFile(path);
  return `data:${mimeFor(path)};base64,${bytes.toString("base64")}`;
}

async function parseStyles() {
  const appJs = await readFile(appJsPath, "utf8");
  const styles = [];
  const pattern = /\[\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]*)"\s*,\s*\[([^\]]*)\]\s*\]/g;
  let match;
  while ((match = pattern.exec(appJs))) {
    const [, id, name, image, reason, rawTags] = match;
    const tags = [...rawTags.matchAll(/"([^"]+)"/g)].map((item) => item[1]);
    styles.push({ id, name, image, reason, tags, path: resolveStylePath(image) });
  }
  return styles;
}

function resolveStylePath(image) {
  const trimmed = String(image || "").trim();
  const candidates = [];
  if (trimmed.startsWith("/")) {
    candidates.push(join(projectRoot, "public", trimmed.slice(1)));
    candidates.push(join(projectRoot, "Nail-main", "public", trimmed.slice(1)));
  } else {
    candidates.push(join(projectRoot, trimmed));
    candidates.push(join(projectRoot, "Nail-main", trimmed));
  }
  const found = candidates.find((candidate) => existsSync(candidate));
  if (!found) throw new Error(`Style image not found: ${image}`);
  return found;
}

function curlPost(payloadPath, responsePath) {
  return new Promise((resolvePromise) => {
    const args = [
      "-sS",
      "--max-time",
      String(maxSeconds),
      "-X",
      "POST",
      endpoint,
      "-H",
      "Content-Type: application/json",
      "--data-binary",
      `@${payloadPath}`,
      "-o",
      responsePath,
      "-w",
      "http_code=%{http_code}\ntime_total=%{time_total}\nsize_download=%{size_download}\n",
    ];
    const started = Date.now();
    const child = spawn("curl", args, { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.on("close", (code) => {
      resolvePromise({
        code,
        stdout,
        stderr,
        elapsed_ms: Date.now() - started,
      });
    });
  });
}

function summarizeResponse(rawText) {
  try {
    const payload = JSON.parse(rawText);
    return {
      success: payload.success === true,
      mode: payload.mode || "",
      error: payload.error || "",
      latency_ms: payload.latency_ms || 0,
      timings: payload.timings || {},
      diagnostics: payload.diagnostics || {},
      style_prompt_plan: payload.style_prompt_plan
        ? {
            schema_version: payload.style_prompt_plan.schema_version,
            overall_style: payload.style_prompt_plan.overall_style,
            confidence: payload.style_prompt_plan.confidence,
            style_spec: payload.style_prompt_plan.style_spec,
            transfer_priority: payload.style_prompt_plan.transfer_priority,
          }
        : null,
      image: typeof payload.image === "string" ? payload.image : "",
    };
  } catch (error) {
    return {
      success: false,
      error: `response_json_parse_failed:${error.message}`,
      raw_prefix: rawText.slice(0, 500),
    };
  }
}

async function main() {
  await mkdir(outputDir, { recursive: true });
  const styles = await parseStyles();
  const handImage = await dataUrl(handPath);
  const results = [];

  console.log(`TRYON_BATCH_START styles=${styles.length} hand=${handPath} output=${outputDir}`);

  for (let index = 0; index < styles.length; index += 1) {
    const style = styles[index];
    const ordinal = `${String(index + 1).padStart(2, "0")}-${style.id}`;
    const payloadPath = join(outputDir, `${ordinal}.payload.json`);
    const responsePath = join(outputDir, `${ordinal}.response.json`);
    const imageUrlPath = join(outputDir, `${ordinal}.image_url.txt`);
    const styleImage = await dataUrl(style.path);
    const payload = {
      hand_image: handImage,
      style_image: styleImage,
      bti_result: {
        code: "batch-test",
        archetype: "批量测试手型",
        axes: {
          white_axis: "contrast_white",
          shape_axis: "natural_shape",
          design_axis: style.tags.some((tag) => /钻|宝石|蝴蝶结|格纹|豹纹|涂鸦|玫粉|派对/.test(tag))
            ? "rich_design"
            : "clean_design",
        },
        styleTags: style.tags,
      },
      fast_mode: true,
      length_mode: "match_reference",
    };
    await writeFile(payloadPath, JSON.stringify(payload), "utf8");

    console.log(`TRYON_STYLE_START ${index + 1}/${styles.length} ${style.id} ${style.name} file=${basename(style.path)}`);
    const curlResult = await curlPost(payloadPath, responsePath);
    let responseText = "";
    try {
      responseText = await readFile(responsePath, "utf8");
    } catch {
      responseText = "";
    }
    const summary = summarizeResponse(responseText);
    if (summary.image) {
      await writeFile(imageUrlPath, summary.image, "utf8");
    }
    const record = {
      index: index + 1,
      id: style.id,
      name: style.name,
      image: style.image,
      imagePath: style.path,
      tags: style.tags,
      curl: curlResult,
      responsePath,
      imageUrlPath: summary.image ? imageUrlPath : "",
      ...summary,
    };
    results.push(record);
    await writeFile(join(outputDir, "results.json"), JSON.stringify(results, null, 2), "utf8");
    console.log(
      [
        `TRYON_STYLE_DONE ${index + 1}/${styles.length}`,
        style.id,
        `success=${record.success}`,
        `latency_ms=${record.latency_ms || curlResult.elapsed_ms}`,
        `doubao_ms=${record.timings?.doubao_prompt_plan_ms ?? ""}`,
        `submit_ms=${record.timings?.apimart_submit_ms ?? ""}`,
        `wait_ms=${record.timings?.apimart_wait_image_ms ?? ""}`,
        `cache=${record.diagnostics?.doubao_prompt_plan_cache_hit ?? ""}`,
        record.error ? `error=${record.error}` : "",
      ]
        .filter(Boolean)
        .join(" "),
    );
  }

  const ok = results.filter((item) => item.success).length;
  const failed = results.length - ok;
  const summary = {
    total: results.length,
    ok,
    failed,
    outputDir,
    generatedAt: new Date().toISOString(),
    avgLatencyMs: Math.round(results.reduce((sum, item) => sum + Number(item.latency_ms || 0), 0) / Math.max(1, results.length)),
  };
  await writeFile(join(outputDir, "summary.json"), JSON.stringify(summary, null, 2), "utf8");
  console.log(`TRYON_BATCH_DONE total=${summary.total} ok=${ok} failed=${failed} avgLatencyMs=${summary.avgLatencyMs}`);
}

main().catch(async (error) => {
  await mkdir(outputDir, { recursive: true }).catch(() => {});
  await writeFile(join(outputDir, "fatal_error.txt"), error.stack || String(error), "utf8").catch(() => {});
  console.error(error.stack || String(error));
  process.exit(1);
});
