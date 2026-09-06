"use client";

/* eslint-disable @next/next/no-img-element -- screenshot is the intentional fallback */

import {
  BitmapVideoFrameRenderer,
  WebCodecsVideoDecoder,
} from "@yume-chan/scrcpy-decoder-webcodecs";
import { ScrcpyVideoCodecId, type ScrcpyMediaStreamPacket } from "@yume-chan/scrcpy";
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { fetchLocalApi } from "../lib/local-api";

const API = "http://127.0.0.1:48138";

type ViewMode = "read_only" | "control";
type ViewProfile = "wall" | "focus";
type SessionResponse = {
  session: { id: string; mode: ViewMode; stream_profile: ViewProfile; expires_at: string };
  token: string;
  websocket_url: string;
};

type StreamState = "connecting" | "live" | "fallback" | "failed";

export function DeviceLiveView({
  deviceId,
  deviceName,
  mode = "read_only",
  profile = "wall",
  onState,
  onOpenNativeWindow,
}: {
  deviceId: string;
  deviceName: string;
  mode?: ViewMode;
  profile?: ViewProfile;
  onState?: (state: StreamState) => void;
  onOpenNativeWindow?: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const decoderRef = useRef<WebCodecsVideoDecoder | null>(null);
  const writerRef = useRef<{
    write: (packet: ScrcpyMediaStreamPacket) => Promise<void>;
    releaseLock: () => void;
  } | null>(null);
  const writeChainRef = useRef<Promise<unknown>>(Promise.resolve());
  const pointerDownRef = useRef(false);
  const [state, setState] = useState<StreamState>("connecting");
  const [message, setMessage] = useState("正在建立本机实时画面…");
  const [size, setSize] = useState({ width: 900, height: 1600 });
  const [fps, setFps] = useState(0);
  const [screenshotStamp, setScreenshotStamp] = useState(0);
  const [text, setText] = useState("");
  const [retryNonce, setRetryNonce] = useState(0);

  const updateState = (next: StreamState, detail: string) => {
    setState(next);
    setMessage(detail);
    onState?.(next);
  };

  useEffect(() => {
    if (state !== "fallback") return;
    const timer = window.setInterval(() => setScreenshotStamp(Date.now()), 5000);
    return () => window.clearInterval(timer);
  }, [state]);

  useEffect(() => {
    let disposed = false;
    let fallbackEntered = false;
    let connectionTimeout = 0;
    let firstFrameTimeout = 0;
    let firstFrameReceived = false;

    const closeSession = () => {
      const sessionId = sessionIdRef.current;
      sessionIdRef.current = null;
      socketRef.current?.close(1000, "view closed");
      socketRef.current = null;
      writerRef.current?.releaseLock();
      writerRef.current = null;
      decoderRef.current?.dispose();
      decoderRef.current = null;
      if (sessionId) {
        void fetchLocalApi(`${API}/api/device-view-sessions/${encodeURIComponent(sessionId)}`, {
          method: "DELETE",
          keepalive: true,
        }, 5_000).catch(() => undefined);
      }
    };

    const enterFallback = (detail: string) => {
      if (fallbackEntered || disposed) return;
      fallbackEntered = true;
      window.clearTimeout(connectionTimeout);
      window.clearTimeout(firstFrameTimeout);
      closeSession();
      updateState("fallback", detail);
    };

    const start = async () => {
      if (!WebCodecsVideoDecoder.isSupported || !canvasRef.current) {
        enterFallback("当前浏览器不支持实时解码，已切换为每5秒截图");
        return;
      }
      try {
        const response = await fetchLocalApi(
          `${API}/api/devices/${encodeURIComponent(deviceId)}/view-sessions`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mode, profile }),
          },
          8_000,
        );
        const payload = (await response.json()) as SessionResponse & { error?: string };
        if (!response.ok) throw new Error(payload.error || "画面会话创建失败");
        if (disposed) {
          await fetchLocalApi(`${API}/api/device-view-sessions/${encodeURIComponent(payload.session.id)}`, { method: "DELETE" }, 5_000);
          return;
        }
        sessionIdRef.current = payload.session.id;
        const socket = new WebSocket(payload.websocket_url);
        socket.binaryType = "arraybuffer";
        socketRef.current = socket;
        connectionTimeout = window.setTimeout(() => {
          if (socket.readyState !== WebSocket.OPEN) {
            enterFallback("实时串流连接超时，已切换为每5秒截图");
          }
        }, 5000);
        socket.onopen = () => setMessage("实时画面正在启动…");
        socket.onmessage = (event) => {
          if (typeof event.data === "string") {
            const item = JSON.parse(event.data) as Record<string, unknown>;
            if (item.type === "ready") {
              const width = Number(item.width || 900);
              const height = Number(item.height || 1600);
              setSize({ width, height });
              const renderer = new BitmapVideoFrameRenderer(canvasRef.current!);
              const codec = Number(item.codec_id);
              if (codec !== ScrcpyVideoCodecId.H264 && codec !== ScrcpyVideoCodecId.H265 && codec !== ScrcpyVideoCodecId.AV1) {
                enterFallback("视频编码不受支持，已切换截图模式");
                return;
              }
              const decoder = new WebCodecsVideoDecoder({
                codec,
                renderer,
              });
              decoderRef.current = decoder;
              writerRef.current = decoder.writable.getWriter();
              window.clearTimeout(connectionTimeout);
              setMessage("视频通道已连接，正在等待首帧…");
              firstFrameTimeout = window.setTimeout(() => {
                if (!firstFrameReceived && !disposed) {
                  enterFallback("实时画面5秒内没有首帧，已切换为每5秒截图");
                }
              }, 5000);
            } else if (item.type === "size") {
              setSize({ width: Number(item.width), height: Number(item.height) });
            } else if (item.type === "metrics") {
              setFps(Number(item.fps || 0));
            } else if (item.type === "fatal" || item.type === "session_expired") {
              enterFallback(`${String(item.message || "实时画面中断")}，已切换为截图`);
            } else if (item.type === "input_error") {
              setMessage(String(item.message || "本次输入未确认，系统不会自动重放"));
            }
            return;
          }
          const bytes = new Uint8Array(event.data as ArrayBuffer);
          if (bytes.byteLength < 9 || !writerRef.current) return;
          const kind = bytes[0];
          // scrcpy sends codec configuration before the first image. It must
          // not cancel the black-screen timeout or unlock manual input.
          if (kind !== 0 && !firstFrameReceived) {
            firstFrameReceived = true;
            window.clearTimeout(firstFrameTimeout);
            updateState("live", mode === "control" ? "实时画面 · 人工接管中" : "实时画面 · 只读");
          }
          const pts = new DataView(bytes.buffer, bytes.byteOffset + 1, 8).getBigInt64(0, false);
          const data = bytes.slice(9);
          const packet: ScrcpyMediaStreamPacket = kind === 0
            ? { type: "configuration", data }
            : {
                type: "data",
                keyframe: kind === 1,
                ...(pts >= 0n ? { pts } : {}),
                data,
              };
          writeChainRef.current = writeChainRef.current
            .then(() => writerRef.current?.write(packet))
            .catch(() => enterFallback("视频解码失败，已切换为每5秒截图"));
        };
        socket.onerror = () => enterFallback("实时画面连接失败，已切换为每5秒截图");
        socket.onclose = () => {
          if (!disposed) enterFallback("实时画面已断开，已切换为每5秒截图");
        };
      } catch (error) {
        enterFallback(error instanceof Error ? `${error.message}，已切换为截图` : "实时画面不可用，已切换为截图");
      }
    };

    void start();
    return () => {
      disposed = true;
      window.clearTimeout(connectionTimeout);
      window.clearTimeout(firstFrameTimeout);
      closeSession();
    };
    // A new session is intentionally created only when the device/view mode changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deviceId, mode, profile, retryNonce]);

  const send = (payload: Record<string, unknown>) => {
    const socket = socketRef.current;
    if (mode !== "control" || state !== "live" || !socket || socket.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify({ ...payload, input_id: crypto.randomUUID() }));
  };

  const pointerCoordinates = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    const x = (event.clientX - rect.left) / rect.width;
    const y = (event.clientY - rect.top) / rect.height;
    if (x < 0 || x > 1 || y < 0 || y > 1) return null;
    return { x, y };
  };

  const aspect = `${size.width} / ${size.height}`;
  return <div className={`device-live-view ${profile} ${mode} ${state}`}>
    <div className="device-live-stage" style={{ aspectRatio: aspect }}>
      <canvas
        ref={canvasRef}
        aria-label={`${deviceName} 实时画面`}
        className="device-live-canvas"
        onPointerDown={(event) => {
          if (mode !== "control") return;
          const point = pointerCoordinates(event);
          if (!point) return;
          event.currentTarget.setPointerCapture(event.pointerId);
          pointerDownRef.current = true;
          send({ type: "pointer", phase: "down", ...point });
        }}
        onPointerMove={(event) => {
          if (!pointerDownRef.current) return;
          const point = pointerCoordinates(event);
          if (point) send({ type: "pointer", phase: "move", ...point });
        }}
        onPointerUp={(event) => {
          if (!pointerDownRef.current) return;
          pointerDownRef.current = false;
          const point = pointerCoordinates(event);
          if (point) send({ type: "pointer", phase: "up", ...point });
        }}
        onPointerCancel={(event) => {
          pointerDownRef.current = false;
          const point = pointerCoordinates(event);
          if (point) send({ type: "pointer", phase: "cancel", ...point });
        }}
        onWheel={(event) => {
          if (mode !== "control") return;
          event.preventDefault();
          const rect = event.currentTarget.getBoundingClientRect();
          send({
            type: "scroll",
            x: (event.clientX - rect.left) / rect.width,
            y: (event.clientY - rect.top) / rect.height,
            deltaX: Math.max(-1, Math.min(1, -event.deltaX / 120)),
            deltaY: Math.max(-1, Math.min(1, -event.deltaY / 120)),
          });
        }}
      />
      {state === "fallback" && <img
        src={`${API}/api/device-image?device_id=${encodeURIComponent(deviceId)}&t=${screenshotStamp}`}
        alt={`${deviceName} 当前截图`}
      />}
      {state === "connecting" && <div className="device-live-loading"><span/><small>正在连接实时画面</small></div>}
    </div>
    <div className="device-live-status">
      <span className={`live-dot ${state}`}/>
      <span>{message}</span>
      {state === "live" && <b>{fps ? `${fps.toFixed(1)} FPS` : profile === "wall" ? "最高10 FPS" : "最高30 FPS"}</b>}
      {state === "fallback" && <button type="button" onClick={() => {
        updateState("connecting", "正在重新连接这台设备…");
        setRetryNonce((value) => value + 1);
      }}>重试实时画面</button>}
      {state === "fallback" && onOpenNativeWindow && <button type="button" onClick={onOpenNativeWindow}>打开单机窗口</button>}
    </div>
    {mode === "control" && <div className="device-control-bar">
      <button type="button" onClick={() => send({ type: "key", key: "back" })}>返回</button>
      <button type="button" onClick={() => send({ type: "key", key: "home" })}>主页</button>
      <button type="button" onClick={() => send({ type: "key", key: "recent" })}>最近任务</button>
      <div><input aria-label="输入到手机的文字" value={text} maxLength={1000} onChange={(event) => setText(event.target.value)} placeholder="输入文字后发送到手机"/></div>
      <button type="button" disabled={!text} onClick={() => { send({ type: /[^\x20-\x7E\r\n\t]/u.test(text) ? "clipboard" : "text", content: text }); setText(""); }}>输入</button>
    </div>}
  </div>;
}
