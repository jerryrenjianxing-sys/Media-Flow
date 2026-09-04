import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { URL } from "node:url";

import { AdbServerClient } from "@yume-chan/adb";
import {
  AdbScrcpyClient,
  AdbScrcpyOptions3_3_3,
} from "@yume-chan/adb-scrcpy";
import { AdbServerNodeTcpConnector } from "@yume-chan/adb-server-node-tcp";
import {
  AndroidKeyCode,
  AndroidKeyEventAction,
  AndroidKeyEventMeta,
  AndroidMotionEventAction,
  AndroidMotionEventButton,
  DefaultServerPath,
  ScrcpyVideoCodecNameMap,
} from "@yume-chan/scrcpy";
import { WebSocket, WebSocketServer } from "ws";

const HOST = process.env.MEDIAFLOW_STREAM_HOST || process.env.RISKFLOW_STREAM_HOST || "127.0.0.1";
const PORT = Number(process.env.MEDIAFLOW_STREAM_PORT || process.env.RISKFLOW_STREAM_PORT || "48139");
const API = process.env.MEDIAFLOW_STREAM_API || process.env.RISKFLOW_STREAM_API || "http://127.0.0.1:48138";
const SECRET_PATH = process.env.MEDIAFLOW_STREAM_SECRET_PATH || process.env.RISKFLOW_STREAM_SECRET_PATH;
const SCRCPY_SERVER = process.env.MEDIAFLOW_SCRCPY_SERVER || process.env.RISKFLOW_SCRCPY_SERVER;
const ALLOWED_ORIGINS = new Set([
  "http://127.0.0.1:3000",
  "http://localhost:3000",
]);
const MAX_BUFFERED_BYTES = 8 * 1024 * 1024;
const VERSION = 1;

if (HOST !== "127.0.0.1") {
  throw new Error("device-stream-host only supports the 127.0.0.1 loopback address");
}
if (!SECRET_PATH || !SCRCPY_SERVER) {
  throw new Error("device-stream-host is missing its supervised runtime paths");
}

const internalSecret = readFileSync(SECRET_PATH, "utf8").trim();
if (internalSecret.length < 32) {
  throw new Error("device-stream-host secret is invalid");
}

const active = new Map();

async function internalRequest(sessionId, action, body = {}) {
  const response = await fetch(
    `${API}/api/internal/device-view-sessions/${encodeURIComponent(sessionId)}/${action}`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-MediaFlow-Stream-Secret": internalSecret,
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(5000),
    },
  );
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `session ${action} failed (${response.status})`);
  }
  return payload;
}

function sendJson(socket, value) {
  if (socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ protocol: VERSION, ...value }));
  }
}

function frameEnvelope(packet) {
  const header = Buffer.allocUnsafe(9);
  header[0] = packet.type === "configuration" ? 0 : packet.keyframe ? 1 : 2;
  header.writeBigInt64BE(packet.type === "data" && packet.pts !== undefined ? packet.pts : -1n, 1);
  return Buffer.concat([
    header,
    Buffer.from(packet.data.buffer, packet.data.byteOffset, packet.data.byteLength),
  ]);
}

function clampUnit(value) {
  return Math.min(1, Math.max(0, Number(value)));
}

function clampScroll(value) {
  return Math.min(1, Math.max(-1, Number(value)));
}

async function dispatchInput(runtime, message) {
  if (runtime.session.mode !== "control" || !runtime.client.controller) {
    throw new Error("当前会话只允许观看");
  }
  const controller = runtime.client.controller;
  const width = runtime.width;
  const height = runtime.height;
  if (!width || !height) {
    throw new Error("画面尺寸尚未就绪");
  }
  if (message.type === "pointer") {
    const actions = {
      down: AndroidMotionEventAction.Down,
      move: AndroidMotionEventAction.Move,
      up: AndroidMotionEventAction.Up,
      cancel: AndroidMotionEventAction.Cancel,
    };
    const action = actions[message.phase];
    if (action === undefined) throw new Error("触控阶段无效");
    const x = clampUnit(message.x);
    const y = clampUnit(message.y);
    await controller.injectTouch({
      action,
      pointerId: 1n,
      pointerX: Math.round(x * Math.max(0, width - 1)),
      pointerY: Math.round(y * Math.max(0, height - 1)),
      videoWidth: width,
      videoHeight: height,
      pressure: message.phase === "up" || message.phase === "cancel" ? 0 : 1,
      actionButton: AndroidMotionEventButton.Primary,
      buttons: message.phase === "up" || message.phase === "cancel" ? 0 : AndroidMotionEventButton.Primary,
    });
    return;
  }
  if (message.type === "scroll") {
    await controller.injectScroll({
      pointerX: Math.round(clampUnit(message.x) * Math.max(0, width - 1)),
      pointerY: Math.round(clampUnit(message.y) * Math.max(0, height - 1)),
      videoWidth: width,
      videoHeight: height,
      scrollX: clampScroll(message.deltaX),
      scrollY: clampScroll(message.deltaY),
      buttons: 0,
    });
    return;
  }
  if (message.type === "key") {
    const keys = {
      back: AndroidKeyCode.AndroidBack,
      home: AndroidKeyCode.AndroidHome,
      recent: AndroidKeyCode.AndroidAppSwitch,
      enter: AndroidKeyCode.Enter,
      backspace: AndroidKeyCode.Backspace,
    };
    const keyCode = keys[message.key];
    if (keyCode === undefined) throw new Error("按键不在允许列表中");
    for (const action of [AndroidKeyEventAction.Down, AndroidKeyEventAction.Up]) {
      await controller.injectKeyCode({
        action,
        keyCode,
        repeat: 0,
        metaState: AndroidKeyEventMeta.None,
      });
    }
    return;
  }
  if (message.type === "text") {
    const content = String(message.content || "");
    if (!content || content.length > 200 || /[^\x20-\x7E\r\n\t]/u.test(content)) {
      throw new Error("键盘输入仅支持最多200个英文字符");
    }
    await controller.injectText(content);
    return;
  }
  if (message.type === "clipboard") {
    const content = String(message.content || "");
    if (!content || content.length > 1000) {
      throw new Error("剪贴板输入内容长度无效");
    }
    await controller.setClipboard({
      sequence: BigInt(Date.now()),
      paste: true,
      content,
    });
    return;
  }
  throw new Error("不支持的输入类型");
}

async function drainOutput(runtime) {
  const reader = runtime.client.output.getReader();
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) return;
      const line = String(value || "").trim();
      if (line) runtime.lastServerMessage = line.slice(0, 240);
    }
  } finally {
    reader.releaseLock();
  }
}

async function startScrcpy(adbEndpoint, session) {
  const connector = new AdbServerNodeTcpConnector({ host: "127.0.0.1", port: 5037 });
  const server = new AdbServerClient(connector);
  const devices = await server.getDevices(["device"]);
  const target = devices.find((device) => device.serial === adbEndpoint);
  if (!target) throw new Error("ADB中没有找到目标MuMu实例");
  const adb = await server.createAdb({ transportId: target.transportId });
  const serverBytes = await import("node:fs/promises").then(({ readFile }) => readFile(SCRCPY_SERVER));
  const serverStream = new ReadableStream({
    start(controller) {
      controller.enqueue(new Uint8Array(serverBytes.buffer, serverBytes.byteOffset, serverBytes.byteLength));
      controller.close();
    },
  });
  await AdbScrcpyClient.pushServer(adb, serverStream, DefaultServerPath);
  const wall = session.stream_profile === "wall";
  const options = new AdbScrcpyOptions3_3_3({
    video: true,
    videoCodec: "h264",
    videoBitRate: wall ? 1_000_000 : 4_000_000,
    maxSize: wall ? 450 : 900,
    maxFps: wall ? 10 : 30,
    audio: false,
    control: session.mode === "control",
    clipboardAutosync: false,
    sendDeviceMeta: true,
    sendCodecMeta: true,
    sendFrameMeta: true,
    cleanup: true,
    powerOn: false,
    stayAwake: false,
    logLevel: "warn",
  });
  const client = await AdbScrcpyClient.start(adb, DefaultServerPath, options);
  return { adb, client };
}

async function runSession(socket, sessionId, token) {
  const validated = await internalRequest(sessionId, "validate", { token });
  const session = validated.session;
  const started = await startScrcpy(validated.adb_endpoint, session);
  const video = await started.client.videoStream;
  if (!video) throw new Error("scrcpy没有返回视频流");
  const runtime = {
    session,
    adb: started.adb,
    client: started.client,
    width: video.width || video.metadata.width || 0,
    height: video.height || video.metadata.height || 0,
    frames: 0,
    dropped: 0,
    lastMetricsAt: Date.now(),
    lastMetricsFrames: 0,
    lastServerMessage: "",
  };
  active.set(sessionId, runtime);
  sendJson(socket, {
    type: "ready",
    session_id: sessionId,
    mode: session.mode,
    profile: session.stream_profile,
    codec: ScrcpyVideoCodecNameMap.get(video.metadata.codec) || "h264",
    codec_id: video.metadata.codec,
    width: runtime.width,
    height: runtime.height,
  });

  const sizeDispose = video.sizeChanged(({ width, height }) => {
    runtime.width = width;
    runtime.height = height;
    sendJson(socket, { type: "size", width, height });
  });
  const outputTask = drainOutput(runtime).catch(() => undefined);
  const reader = video.stream.getReader();
  const heartbeat = setInterval(async () => {
    const now = Date.now();
    const elapsed = Math.max(1, now - runtime.lastMetricsAt);
    const fps = ((runtime.frames - runtime.lastMetricsFrames) * 1000) / elapsed;
    const metadata = {
      width: runtime.width,
      height: runtime.height,
      fps: Number(fps.toFixed(1)),
      frames: runtime.frames,
      dropped_frames: runtime.dropped,
    };
    runtime.lastMetricsAt = now;
    runtime.lastMetricsFrames = runtime.frames;
    try {
      await internalRequest(sessionId, "heartbeat", { metadata });
      sendJson(socket, { type: "metrics", ...metadata });
    } catch (error) {
      sendJson(socket, { type: "session_expired", message: error.message });
      socket.close(4003, "session expired");
    }
  }, 5000);

  socket.on("message", async (data, isBinary) => {
    if (isBinary) return;
    let message;
    try {
      message = JSON.parse(data.toString("utf8"));
      await dispatchInput(runtime, message);
      sendJson(socket, { type: "input_ack", input_id: message.input_id || null });
    } catch (error) {
      sendJson(socket, {
        type: "input_error",
        input_id: message?.input_id || null,
        message: error instanceof Error ? error.message : "输入失败",
      });
    }
  });

  try {
    while (socket.readyState === WebSocket.OPEN) {
      const { done, value } = await reader.read();
      if (done) break;
      runtime.frames += value.type === "data" ? 1 : 0;
      if (
        socket.bufferedAmount > MAX_BUFFERED_BYTES &&
        value.type === "data" &&
        !value.keyframe
      ) {
        runtime.dropped += 1;
        continue;
      }
      socket.send(frameEnvelope(value), { binary: true });
    }
  } finally {
    clearInterval(heartbeat);
    reader.releaseLock();
    sizeDispose?.();
    active.delete(sessionId);
    await started.client.close().catch(() => undefined);
    await outputTask;
    await internalRequest(sessionId, "disconnect", {
      metadata: {
        width: runtime.width,
        height: runtime.height,
        frames: runtime.frames,
        dropped_frames: runtime.dropped,
      },
    }).catch(() => undefined);
  }
}

const server = createServer((request, response) => {
  if (request.method === "GET" && request.url === "/health") {
    response.writeHead(200, {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    });
    response.end(JSON.stringify({ ok: true, protocol: VERSION, active_streams: active.size }));
    return;
  }
  response.writeHead(404, { "Content-Type": "application/json; charset=utf-8" });
  response.end(JSON.stringify({ error: "Not found" }));
});

const sockets = new WebSocketServer({ noServer: true, maxPayload: 64 * 1024 });
server.on("upgrade", (request, socket, head) => {
  try {
    const origin = request.headers.origin || "";
    if (!ALLOWED_ORIGINS.has(origin)) {
      socket.write("HTTP/1.1 403 Forbidden\r\n\r\n");
      socket.destroy();
      return;
    }
    const url = new URL(request.url, `http://${HOST}:${PORT}`);
    const match = /^\/v1\/sessions\/([a-f0-9]{32})$/u.exec(url.pathname);
    const token = url.searchParams.get("token") || "";
    if (!match || token.length < 32) {
      socket.write("HTTP/1.1 401 Unauthorized\r\n\r\n");
      socket.destroy();
      return;
    }
    sockets.handleUpgrade(request, socket, head, (webSocket) => {
      sockets.emit("connection", webSocket, request, match[1], token);
    });
  } catch {
    socket.destroy();
  }
});

sockets.on("connection", (socket, _request, sessionId, token) => {
  runSession(socket, sessionId, token).catch((error) => {
    sendJson(socket, {
      type: "fatal",
      message: error instanceof Error ? error.message : "实时画面启动失败",
    });
    socket.close(1011, "stream unavailable");
  });
});

server.listen(PORT, HOST, () => {
  process.stdout.write(`${JSON.stringify({ event: "device_stream_host_ready", host: HOST, port: PORT, protocol: VERSION })}\n`);
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    for (const socket of sockets.clients) socket.close(1001, "server shutdown");
    server.close(() => process.exit(0));
  });
}
