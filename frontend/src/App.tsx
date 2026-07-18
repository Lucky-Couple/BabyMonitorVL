import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import hljs from "highlight.js/lib/core";
import jsonLanguage from "highlight.js/lib/languages/json";
import type {
  Box,
  FrameAnalysis,
  HistoryDetail,
  HistorySummary,
  MonitorStatus,
  ProviderInfo,
  ProviderName,
  Risk,
} from "./types";

hljs.registerLanguage("json", jsonLanguage);

const RTSP_DRAFT_STORAGE_KEY = "babymonitorvl.rtsp-draft";

function readRtspDraft(): string {
  try {
    return window.sessionStorage.getItem(RTSP_DRAFT_STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

function writeRtspDraft(value: string) {
  try {
    window.sessionStorage.setItem(RTSP_DRAFT_STORAGE_KEY, value);
  } catch {
    // The controlled input still retains the draft when storage is unavailable.
  }
}

const labels: Record<string, string> = {
  supine: "仰卧",
  prone: "俯卧",
  side_lying: "侧卧",
  not_lying: "非躺卧",
  visible: "脸部可见",
  partially_occluded: "脸部部分遮挡",
  fully_occluded: "脸部完全遮挡",
  not_visible: "脸部不可见",
  absent: "未见被子",
  present_not_covering: "被子未盖住婴儿",
  lower_body: "覆盖下半身",
  torso: "覆盖躯干",
  near_face: "靠近脸部",
  covering_face: "覆盖脸部",
  unknown: "未知",
  normal: "正常",
  watch: "需关注",
  alert: "立即查看",
  good: "画面清晰",
  poor: "画面较差",
  unusable: "画面不可用",
  blanket: "被子",
  pillow: "枕头",
  toy: "玩具",
  hand: "手部",
  other_occluder: "其他遮挡物",
  separate: "与婴儿分开",
  near_infant: "靠近婴儿",
  overlapping_infant: "接触或覆盖婴儿区域",
};

const overlayColors: Record<string, string> = {
  infant: "#56b8ff",
  face: "#55e6a5",
  blanket: "#f2b84b",
  pillow: "#8b9cff",
  toy: "#ff8a5b",
  hand: "#45d4d4",
  other_occluder: "#ff5e6c",
  cat: "#d58cff",
};

const emptyStatus: MonitorStatus = {
  state: "stopped",
  session_id: null,
  source: null,
  provider: null,
  model: null,
  fps: null,
  capture_count: 0,
  submitted_count: 0,
  completed_count: 0,
  error_count: 0,
  dropped_count: 0,
  last_capture_at: null,
  last_analysis_at: null,
  last_latency_ms: null,
  last_record_id: null,
  last_error: null,
  reconnect_attempt: 0,
  input_tokens: 0,
  output_tokens: 0,
  history: { items: 0, bytes: 0, max_bytes: 0 },
};

function formatTime(value: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function formatBytes(value: number) {
  if (!value) return "0 MB";
  return `${(value / 1024 / 1024).toFixed(value > 100 * 1024 * 1024 ? 0 : 1)} MB`;
}

function formatTokens(value: number | null | undefined) {
  if (value == null) return "—";
  return new Intl.NumberFormat("zh-CN").format(value);
}

function RiskBadge({ risk }: { risk: Risk | null | undefined }) {
  const value = risk ?? "unknown";
  return <span className={`risk-badge risk-${value}`}>{labels[value]}</span>;
}

interface OverlayBox {
  box: Box;
  label: string;
  color: string;
}

function analysisBoxes(analysis: FrameAnalysis | null | undefined): OverlayBox[] {
  if (!analysis) return [];
  const result: OverlayBox[] = [];
  analysis.infants.forEach((infant, index) => {
    result.push({ box: infant.infant_box, label: `婴儿 ${index + 1}`, color: overlayColors.infant });
    if (infant.face_box) result.push({ box: infant.face_box, label: "脸部", color: overlayColors.face });
    infant.related_objects.forEach((object) => {
      result.push({
        box: object.box,
        label: labels[object.kind] ?? object.kind,
        color: overlayColors[object.kind] ?? "#c0cad4",
      });
    });
  });
  (analysis.cats ?? []).forEach((cat, index) => {
    result.push({ box: cat.cat_box, label: `猫 ${index + 1}`, color: overlayColors.cat });
  });
  return result;
}

function BoxOverlay({ analysis, compact = false }: { analysis: FrameAnalysis | null | undefined; compact?: boolean }) {
  const boxes = useMemo(() => analysisBoxes(analysis), [analysis]);
  return (
    <svg className={compact ? "compact-overlay" : undefined} viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-label="模型标注框">
      {boxes.map(({ box, label, color }, index) => {
        const [ymin, xmin, ymax, xmax] = box;
        const labelHeight = compact ? 90 : 35;
        const fontSize = compact ? 58 : 25;
        return (
          <g key={`${label}-${index}`}>
            <rect
              x={xmin}
              y={ymin}
              width={xmax - xmin}
              height={ymax - ymin}
              fill="transparent"
              stroke={color}
              strokeWidth={compact ? 1.5 : 2}
              vectorEffect="non-scaling-stroke"
            />
            <rect x={xmin} y={Math.max(0, ymin - labelHeight)} width={Math.max(compact ? 210 : 95, label.length * (compact ? 70 : 30))} height={labelHeight} fill={color} fillOpacity={0.45} />
            <text x={xmin + (compact ? 15 : 8)} y={Math.max(fontSize, ymin - (compact ? 22 : 10))} fill="#071018" fontSize={fontSize} fontWeight="700">
              {label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function AnnotatedFrame({ detail }: { detail: HistoryDetail | null }) {
  if (!detail) {
    return <div className="empty-frame">等待第一帧分析结果</div>;
  }
  return (
    <div className="annotated-frame" style={{ aspectRatio: `${detail.image_width} / ${detail.image_height}` }}>
      <img src={`${detail.image_url}?v=${detail.completed_at ?? detail.captured_at}`} alt="已分析监控帧" />
      <BoxOverlay analysis={detail.analysis} />
    </div>
  );
}

function prettyJson(value: unknown): string {
  if (typeof value !== "string") return JSON.stringify(value, null, 2);
  const trimmed = value.trim();
  const withoutFence = trimmed.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "");
  try {
    return JSON.stringify(JSON.parse(withoutFence), null, 2);
  } catch {
    return value;
  }
}

function JsonCode({ value }: { value: unknown }) {
  const formatted = useMemo(() => prettyJson(value), [value]);
  const highlighted = useMemo(() => hljs.highlight(formatted, { language: "json" }).value, [formatted]);
  return <pre className="json-code"><code className="hljs language-json" dangerouslySetInnerHTML={{ __html: highlighted }} /></pre>;
}

function AnalysisPanel({ analysis }: { analysis: FrameAnalysis | null | undefined }) {
  if (!analysis) return <div className="empty-analysis">暂无结构化结果</div>;
  const cats = analysis.cats ?? [];
  return (
    <div className="analysis-panel">
      <div className="analysis-heading">
        <RiskBadge risk={analysis.overall_risk} />
        <span>{labels[analysis.image_quality] ?? analysis.image_quality}</span>
      </div>
      <p className="summary">{analysis.summary}</p>
      <div className={`cat-presence ${cats.length > 0 ? "cat-detected" : "cat-absent"}`}>
        <strong>猫监测</strong>
        <span>{cats.length > 0 ? `检测到 ${cats.length} 只猫进入摄像头范围` : "未检测到猫进入摄像头范围"}</span>
      </div>
      {analysis.risk_reasons.length > 0 && (
        <ul className="risk-reasons">
          {analysis.risk_reasons.map((reason) => <li key={reason}>{reason}</li>)}
        </ul>
      )}
      {analysis.infants.map((infant, index) => (
        <section className="infant-card" key={index}>
          <div className="infant-title">
            <strong>婴儿 {index + 1}</strong>
            <RiskBadge risk={infant.risk_level} />
          </div>
          <dl>
            <div><dt>姿势</dt><dd>{labels[infant.posture] ?? infant.posture}</dd></div>
            <div><dt>脸部</dt><dd>{labels[infant.face_visibility] ?? infant.face_visibility}</dd></div>
            <div><dt>被子</dt><dd>{labels[infant.blanket_coverage] ?? infant.blanket_coverage}</dd></div>
            <div><dt>置信度</dt><dd>{Math.round(infant.confidence * 100)}%</dd></div>
          </dl>
          {infant.evidence.length > 0 && (
            <ul className="evidence">{infant.evidence.map((item) => <li key={item}>{item}</li>)}</ul>
          )}
        </section>
      ))}
      {analysis.infants.length === 0 && <div className="no-infant">当前画面未定位到婴儿</div>}
      {cats.map((cat, index) => (
        <section className="infant-card cat-card" key={`cat-${index}`}>
          <div className="infant-title">
            <strong>猫 {index + 1}</strong>
            <span className="cat-confidence">置信度 {Math.round(cat.confidence * 100)}%</span>
          </div>
          <dl>
            <div><dt>与婴儿关系</dt><dd>{labels[cat.proximity_to_infant] ?? cat.proximity_to_infant}</dd></div>
            <div><dt>摄像头范围</dt><dd>已进入</dd></div>
          </dl>
          {cat.evidence.length > 0 && (
            <ul className="evidence">{cat.evidence.map((item) => <li key={item}>{item}</li>)}</ul>
          )}
        </section>
      ))}
    </div>
  );
}

export default function App() {
  const [providers, setProviders] = useState<Record<ProviderName, ProviderInfo> | null>(null);
  const [status, setStatus] = useState<MonitorStatus>(emptyStatus);
  const [history, setHistory] = useState<HistorySummary[]>([]);
  const [nextHistoryCursor, setNextHistoryCursor] = useState<string | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [detail, setDetail] = useState<HistoryDetail | null>(null);
  const [liveUrl, setLiveUrl] = useState<string | null>(null);
  const [provider, setProvider] = useState<ProviderName>("ollama");
  const [model, setModel] = useState("qwen3-vl:4b");
  const [rtspUrl, setRtspUrl] = useState(readRtspDraft);
  const [fps, setFps] = useState(1);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [transport, setTransport] = useState<"tcp" | "udp">("tcp");
  const [maxEdge, setMaxEdge] = useState(1280);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reconnectTimer = useRef<number | null>(null);
  const loadedOlderHistory = useRef(false);

  const fetchHistory = useCallback(async (preferredId?: string) => {
    const response = await fetch("/api/history?limit=200");
    const body = await response.json();
    setHistory((existing) => {
      if (!preferredId || existing.length === 0) return body.items;
      const latestIds = new Set(body.items.map((item: HistorySummary) => item.id));
      return [...body.items, ...existing.filter((item) => !latestIds.has(item.id))];
    });
    if (!loadedOlderHistory.current) setNextHistoryCursor(body.next_cursor);
    const id = preferredId ?? body.items.find((item: HistorySummary) => item.status !== "pending")?.id;
    if (id) {
      const detailResponse = await fetch(`/api/history/${id}`);
      if (detailResponse.ok) setDetail(await detailResponse.json());
    }
  }, []);

  async function loadOlderHistory() {
    if (!nextHistoryCursor || loadingOlder) return;
    setLoadingOlder(true);
    try {
      const response = await fetch(`/api/history?limit=200&cursor=${encodeURIComponent(nextHistoryCursor)}`);
      if (!response.ok) throw new Error("加载更早历史失败");
      const body = await response.json();
      loadedOlderHistory.current = true;
      setHistory((existing) => {
        const existingIds = new Set(existing.map((item) => item.id));
        return [...existing, ...body.items.filter((item: HistorySummary) => !existingIds.has(item.id))];
      });
      setNextHistoryCursor(body.next_cursor);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoadingOlder(false);
    }
  }

  useEffect(() => {
    Promise.all([fetch("/api/providers"), fetch("/api/monitor/status")])
      .then(async ([providerResponse, statusResponse]) => {
        const providerBody = await providerResponse.json();
        const statusBody = await statusResponse.json();
        setProviders(providerBody);
        setStatus(statusBody);
        if (statusBody.provider && statusBody.model) {
          setProvider(statusBody.provider);
          setModel(statusBody.model);
        } else {
          setModel(providerBody.ollama.default_model);
        }
      })
      .catch((reason) => setError(String(reason)));
    void fetchHistory();
  }, [fetchHistory]);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let closed = false;
    const connect = () => {
      const protocol = location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${protocol}://${location.host}/api/events`);
      socket.onmessage = (message) => {
        const event = JSON.parse(message.data);
        if (event.type === "status") setStatus(event.data);
        if (event.type === "capture") setLiveUrl(`${event.data.image_url}&t=${Date.now()}`);
        if (event.type === "analysis_completed" || event.type === "analysis_failed") {
          void fetchHistory(event.data.id);
        }
      };
      socket.onclose = () => {
        if (!closed) reconnectTimer.current = window.setTimeout(connect, 1500);
      };
    };
    connect();
    return () => {
      closed = true;
      if (reconnectTimer.current) window.clearTimeout(reconnectTimer.current);
      socket?.close();
    };
  }, [fetchHistory]);

  const changeProvider = (next: ProviderName) => {
    setProvider(next);
    if (providers) {
      const info = providers[next];
      setModel(info.models.includes(info.default_model) ? info.default_model : (info.models[0] ?? info.default_model));
    }
  };

  const changeRtspUrl = (value: string) => {
    setRtspUrl(value);
    writeRtspDraft(value);
  };

  async function start(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/monitor/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          rtsp_url: rtspUrl,
          fps,
          provider,
          model,
          rtsp_transport: transport,
          max_image_edge: maxEdge,
        }),
      });
      if (!response.ok) throw new Error((await response.json()).detail ?? "启动失败");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/monitor/stop", { method: "POST" });
      if (!response.ok) throw new Error("停止失败");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function selectHistory(id: string) {
    const response = await fetch(`/api/history/${id}`);
    if (response.ok) setDetail(await response.json());
  }

  const active = status.state !== "stopped";
  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <div className="eyebrow">VISION LANGUAGE MONITOR</div>
          <h1>BabyMonitor<span>VL</span></h1>
        </div>
        <div className={`connection-state state-${status.state}`}>
          <i />{status.state === "streaming" ? "监控中" : status.state === "stopped" ? "已停止" : status.state === "reconnecting" ? "正在重连" : "正在连接"}
        </div>
      </header>

      <div className="safety-banner">
        <strong>实验性 Demo</strong> 仅供演示和人工复核，不可作为医疗设备、生命安全告警或无人值守监控系统。
      </div>

      <section className="control-card">
        <form onSubmit={start}>
          <label className="field field-source">
            <span>RTSP 地址</span>
            <input
              data-testid="rtsp-input"
              type="text"
              name="rtsp_url"
              placeholder="rtsp://user:password@camera/stream"
              value={rtspUrl}
              onChange={(event) => changeRtspUrl(event.target.value)}
              autoComplete="off"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              disabled={active}
              required
            />
          </label>
          <label className="field field-small">
            <span>FPS</span>
            <input type="number" min="0.1" max="10" step="0.1" value={fps} onChange={(e) => setFps(Number(e.target.value))} disabled={active} />
          </label>
          <label className="field">
            <span>模型后端</span>
            <select value={provider} onChange={(e) => changeProvider(e.target.value as ProviderName)} disabled={active}>
              <option value="ollama">Ollama · 本地</option>
              <option value="gemini">Gemini · 云端</option>
            </select>
          </label>
          <label className="field field-model">
            <span>模型</span>
            <select data-testid="model-select" value={model} onChange={(e) => setModel(e.target.value)} disabled={active}>
              {(providers?.[provider]?.models ?? [model]).map((item) => <option value={item} key={item}>{item}</option>)}
            </select>
          </label>
          {active ? (
            <button className="button stop" type="button" onClick={stop} disabled={busy}>停止</button>
          ) : (
            <button className="button start" type="submit" disabled={busy}>开始监控</button>
          )}
        </form>
        <button className="advanced-toggle" type="button" onClick={() => setShowAdvanced(!showAdvanced)}>{showAdvanced ? "收起高级设置" : "高级设置"}</button>
        {showAdvanced && (
          <div className="advanced-row">
            <label><span>RTSP Transport</span><select value={transport} onChange={(e) => setTransport(e.target.value as "tcp" | "udp")} disabled={active}><option value="tcp">TCP</option><option value="udp">UDP</option></select></label>
            <label><span>图像长边上限</span><input type="number" min="320" max="4096" value={maxEdge} onChange={(e) => setMaxEdge(Number(e.target.value))} disabled={active} /></label>
          </div>
        )}
        <div className="provider-health">
          {providers && (Object.entries(providers) as [ProviderName, ProviderInfo][]).map(([name, info]) => (
            <span key={name} className={info.available ? "healthy" : "unhealthy"}><i />{name === "ollama" ? "Ollama" : "Gemini"}: {info.detail}{info.version ? ` · ${info.version}` : ""}</span>
          ))}
        </div>
        {provider === "gemini" && <div className="cloud-warning">Gemini 模式会将采样帧发送至 Google API。</div>}
        {error && <div className="error-banner">{error}</div>}
      </section>

      <section className="metrics-grid">
        <div><span>已抽帧</span><strong>{status.capture_count}</strong></div>
        <div><span>已提交</span><strong>{status.submitted_count}</strong></div>
        <div><span>已完成</span><strong>{status.completed_count}</strong></div>
        <div><span>输入 Token</span><strong>{formatTokens(status.input_tokens)}</strong></div>
        <div><span>输出 Token</span><strong>{formatTokens(status.output_tokens)}</strong></div>
        <div><span>覆盖丢帧</span><strong>{status.dropped_count}</strong></div>
        <div><span>最近延迟</span><strong>{status.last_latency_ms ? `${(status.last_latency_ms / 1000).toFixed(1)}s` : "—"}</strong></div>
        <div><span>历史内存</span><strong>{formatBytes(status.history.bytes)}</strong><small>/ {formatBytes(status.history.max_bytes)}</small></div>
      </section>

      {status.last_error && <div className="stream-error">{status.last_error}</div>}

      <main className="monitor-grid">
        <section className="panel result-visual">
          <div className="panel-heading">
            <div><span className="section-number">01</span><h2>最近完成分析</h2></div>
            <div className="frame-meta">抽帧 {formatTime(detail?.captured_at ?? null)} · 完成 {formatTime(detail?.completed_at ?? null)}</div>
          </div>
          <AnnotatedFrame detail={detail} />
        </section>

        <section className="panel analysis-result">
          <div className="panel-heading"><div><span className="section-number">02</span><h2>结构化状态</h2></div></div>
          <AnalysisPanel analysis={detail?.analysis} />
        </section>

        <aside className="panel live-preview">
          <div className="panel-heading"><div><span className="live-dot" /><h2>最新 RTSP 抽帧</h2></div><span>{formatTime(status.last_capture_at)}</span></div>
          {liveUrl ? <img src={liveUrl} alt="最新 RTSP 抽帧" /> : <div className="empty-live">尚无实时画面</div>}
          <p>实时预览不叠加旧分析框，避免帧与结果错配。</p>
        </aside>
      </main>

      <section className="history-section">
        <div className="history-heading">
          <div><span className="section-number">03</span><h2>进程内调试历史</h2></div>
          <span>最新在前 · 已加载 {history.length} / 内存共 {status.history.items} 帧</span>
        </div>
        <div className="history-grid" data-testid="history-grid">
          {history.map((item) => (
            <button className={`history-card ${detail?.id === item.id ? "selected" : ""}`} key={item.id} onClick={() => void selectHistory(item.id)}>
              <div className="thumbnail">
                <div className="thumbnail-canvas" style={{ aspectRatio: `${item.image_width} / ${item.image_height}` }}>
                  <img src={item.image_url} alt="历史帧" loading="lazy" />
                  <BoxOverlay analysis={item.analysis} compact />
                </div>
                <RiskBadge risk={item.overall_risk} />
              </div>
              <div className="history-info">
                <strong>{formatTime(item.captured_at)}</strong>
                <span>{item.provider} · {item.model}</span>
                <span>{item.analysis ? ((item.analysis.cats ?? []).length > 0 ? `检测到猫 ${(item.analysis.cats ?? []).length} 只` : "未检测到猫") : "猫监测 —"}</span>
                <span>{item.status === "error" ? "分析失败" : item.latency_ms ? `${(item.latency_ms / 1000).toFixed(1)}s · ${item.attempts} 次调用` : "分析中"}</span>
                <span>Token 输入 {formatTokens(item.input_tokens)} · 输出 {formatTokens(item.output_tokens)}</span>
              </div>
            </button>
          ))}
          {history.length === 0 && <div className="empty-history">分析记录会出现在这里，服务重启后自动清空。</div>}
        </div>
        {nextHistoryCursor && (
          <div className="history-load-more">
            <button type="button" onClick={() => void loadOlderHistory()} disabled={loadingOlder}>
              {loadingOlder ? "正在加载…" : "加载更早的记录"}
            </button>
          </div>
        )}
      </section>

      {detail && (
        <section className="debug-section">
          <div className="history-heading"><div><span className="section-number">04</span><h2>请求审计</h2></div><span>{detail.prompt_version}</span></div>
          <div className="debug-grid">
            <details open><summary>原始模型响应</summary>{detail.raw_responses.length > 0 ? detail.raw_responses.map((response, index) => <div className="response-attempt" key={index}>{detail.raw_responses.length > 1 && <div className="attempt-label">尝试 {index + 1}</div>}<JsonCode value={response} /></div>) : <pre>No response</pre>}</details>
            <details><summary>实际发送的 Prompt</summary><pre>{detail.prompt}</pre></details>
            <details><summary>JSON Schema</summary><JsonCode value={detail.output_schema} /></details>
            <details><summary>调用参数与错误</summary><JsonCode value={{ generation: detail.generation_params, errors: detail.errors }} /></details>
          </div>
        </section>
      )}

      <footer>BabyMonitorVL MVP · 所有语义定位与状态判断均来自多模态大模型</footer>
    </div>
  );
}
