import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import hljs from "highlight.js/lib/core";
import jsonLanguage from "highlight.js/lib/languages/json";
import type {
  BlanketCoverage,
  Box,
  CatProximity,
  FrameAnalysis,
  HistoryDetail,
  HistorySummary,
  ImageQuality,
  MonitorStatus,
  MouthNoseOcclusion,
  ObjectRelation,
  Posture,
  ProviderInfo,
  ProviderName,
  RelatedObjectKind,
  Risk,
} from "./types";

hljs.registerLanguage("json", jsonLanguage);

const RTSP_DRAFT_STORAGE_KEY = "babymonitorvl.rtsp-draft";

interface LiveFrameState {
  imageUrl: string;
  capturedAt: string | null;
  width: number | null;
  height: number | null;
  actualIntervalSeconds: number | null;
  analysisBitrateKbps: number | null;
}

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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isNullableNumber(value: unknown): value is number | null {
  return value === null || (typeof value === "number" && Number.isFinite(value));
}

function isNonNegativeNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function isMonitorStatus(value: unknown): value is MonitorStatus {
  if (!isRecord(value) || !isRecord(value.history)) return false;
  const history = value.history;
  const states = ["stopped", "connecting", "streaming", "reconnecting"];
  const nullableStrings = [
    "session_id",
    "source",
    "model",
    "last_capture_at",
    "last_analysis_at",
    "last_record_id",
    "last_error",
  ];
  const counters = [
    "submitted_count",
    "completed_count",
    "error_count",
    "reconnect_attempt",
    "input_tokens",
    "output_tokens",
  ];
  return typeof value.state === "string"
    && states.includes(value.state)
    && (value.provider === null || value.provider === "ollama" || value.provider === "gemini")
    && nullableStrings.every((name) => isNullableString(value[name]))
    && isNullableNumber(value.min_frame_interval_seconds)
    && isNullableNumber(value.last_latency_ms)
    && isNullableNumber(value.reconnect_delay_seconds)
    && counters.every((name) => isNonNegativeNumber(value[name]))
    && ["items", "bytes", "max_bytes"].every((name) => isNonNegativeNumber(history[name]));
}

type DisplayLabelKey =
  | Risk
  | ImageQuality
  | Posture
  | MouthNoseOcclusion
  | BlanketCoverage
  | RelatedObjectKind
  | ObjectRelation
  | CatProximity;

const labels: Record<DisplayLabelKey, string> = {
  supine: "仰卧",
  prone: "俯卧",
  side_lying: "侧卧",
  not_lying: "非躺卧",
  clear: "口鼻无遮挡",
  partially_covered: "口鼻部分被覆盖",
  fully_covered: "口鼻完全被覆盖",
  not_visible: "口鼻未直接可见",
  absent: "未见被子",
  present_not_covering: "被子未盖住婴儿",
  lower_body: "覆盖下半身",
  torso: "覆盖躯干",
  near_mouth_nose: "靠近口鼻",
  partially_covering_mouth_nose: "部分覆盖口鼻",
  covering_mouth_nose: "覆盖口鼻",
  partially_covers_mouth_nose: "部分覆盖口鼻",
  covers_mouth_nose: "覆盖口鼻",
  covers_body: "覆盖身体",
  near_body: "靠近身体",
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
  mouth_nose: "#55e6a5",
  blanket: "#f2b84b",
  pillow: "#8b9cff",
  toy: "#ff8a5b",
  hand: "#45d4d4",
  other_occluder: "#ff5e6c",
  cat: "#d58cff",
  adult: "#ff6bd6",
};

const emptyStatus: MonitorStatus = {
  state: "stopped",
  session_id: null,
  source: null,
  provider: null,
  model: null,
  min_frame_interval_seconds: null,
  submitted_count: 0,
  completed_count: 0,
  error_count: 0,
  last_capture_at: null,
  last_analysis_at: null,
  last_latency_ms: null,
  last_record_id: null,
  last_error: null,
  reconnect_attempt: 0,
  reconnect_delay_seconds: null,
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

function formatAnalysisBitrate(value: number | null) {
  if (value === null) return "—";
  if (value >= 1000) return `${(value / 1000).toFixed(2)} Mbps`;
  return `${value.toFixed(0)} kbps`;
}

function formatFrameInterval(value: number | null | undefined) {
  return value == null ? "—" : `${value.toFixed(2)}s`;
}

function historySubjectText(item: HistorySummary) {
  if (!item.analysis) return "婴儿 — · 成人 — · 猫 —";
  return `婴儿 ${item.analysis.infants.length} · 成人 ${item.analysis.adult_presence === "unknown" ? "未知" : item.analysis.adults.length} · 猫 ${(item.analysis.cats ?? []).length}`;
}

function historyCallText(item: HistorySummary) {
  const statusText = item.status === "error"
    ? "分析失败"
    : item.attempts > 1 && item.latency_ms
      ? `重试后成功 · 耗时 ${(item.latency_ms / 1000).toFixed(1)}s`
      : item.latency_ms
        ? `耗时 ${(item.latency_ms / 1000).toFixed(1)}s`
        : "分析中";
  return `${statusText} · 调用 ${item.attempts} 次 · 输入 ${formatTokens(item.input_tokens)} · 输出 ${formatTokens(item.output_tokens)}`;
}

function connectionLabel(status: MonitorStatus) {
  if (status.state === "streaming") return "监控中";
  if (status.state === "stopped") return "已停止";
  if (status.state === "connecting") return "正在连接";
  const attempt = status.reconnect_attempt > 0 ? ` · 第 ${status.reconnect_attempt} 次` : "";
  const delay = status.reconnect_delay_seconds !== null ? ` · ${status.reconnect_delay_seconds}s 后重试` : "";
  return `正在重连${attempt}${delay}`;
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

function subjectLabel(name: string, index: number, total: number) {
  return total > 1 ? `${name} ${index + 1}` : name;
}

function analysisBoxes(analysis: FrameAnalysis | null | undefined): OverlayBox[] {
  if (!analysis) return [];
  const result: OverlayBox[] = [];
  analysis.infants.forEach((infant, index) => {
    result.push({
      box: infant.infant_box,
      label: subjectLabel("婴儿", index, analysis.infants.length),
      color: overlayColors.infant,
    });
    if (infant.mouth_nose_box) {
      result.push({ box: infant.mouth_nose_box, label: "口鼻", color: overlayColors.mouth_nose });
    }
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
  analysis.adults.forEach((adult, index) => {
    result.push({
      box: adult.adult_box,
      label: subjectLabel("成人", index, analysis.adults.length),
      color: overlayColors.adult,
    });
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
    <div
      className="annotated-frame"
      style={{ aspectRatio: `${detail.image_width} / ${detail.image_height}` }}
    >
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

const attemptOutcomeLabels = {
  success: "成功",
  validation_error: "结构化结果校验失败",
  provider_error: "模型后端调用失败",
  cancelled: "会话停止，调用已取消",
} as const;

function retryReasonText(reason: string | null): string | null {
  if (!reason) return null;
  if (reason.startsWith("local_validation:")) {
    return `本地结构化校验失败（${reason.slice("local_validation:".length)}），因此发起下一次调用`;
  }
  if (reason === "retryable_provider_error") {
    return "模型后端错误被判定为可重试，因此发起下一次调用";
  }
  return reason;
}

function AttemptAudit({ detail }: { detail: HistoryDetail }) {
  if (detail.attempt_details.length === 0) {
    return (
      <div className="attempt-audit legacy-attempt-audit">
        <p>此记录没有逐次调用元数据，只能显示旧版错误列表。</p>
        <JsonCode value={{ errors: detail.errors }} />
      </div>
    );
  }
  return (
    <div className="attempt-audit">
      {detail.attempt_details.map((attempt) => {
        const retryReason = retryReasonText(attempt.retry_reason);
        return (
          <section className="attempt-card" key={attempt.attempt}>
            <div className="attempt-header">
              <strong>调用 {attempt.attempt}</strong>
              <span className={`attempt-status attempt-${attempt.outcome}`}>
                {attemptOutcomeLabels[attempt.outcome]}{attempt.will_retry ? " · 已触发重试" : ""}
              </span>
            </div>
            <div className="attempt-metrics">
              <span>输入 {formatTokens(attempt.usage.input_tokens as number | null | undefined)}</span>
              <span>输出 {formatTokens(attempt.usage.output_tokens as number | null | undefined)}</span>
              <span>{attempt.response_index === null ? "未产生模型响应" : `对应模型响应 ${attempt.response_index + 1}`}</span>
            </div>
            {retryReason && <p className="attempt-retry-reason">{retryReason}</p>}
            {attempt.warnings.map((warning) => (
              <p className="attempt-warning" key={warning}>{warning}</p>
            ))}
            {attempt.error && (
              <div className="attempt-error">
                <div>{attempt.error_type ?? "Error"}</div>
                <pre>{attempt.error}</pre>
              </div>
            )}
            {Object.keys(attempt.usage).length > 0 && (
              <details className="attempt-usage">
                <summary>本次调用用量明细</summary>
                <JsonCode value={attempt.usage} />
              </details>
            )}
            <details className="attempt-prompt">
              <summary>
                本次实际 Prompt{attempt.prompt === detail.prompt ? " · 基线" : " · 含重试修正"}
              </summary>
              <pre>{attempt.prompt}</pre>
            </details>
          </section>
        );
      })}
    </div>
  );
}

function AnalysisPanel({ analysis }: { analysis: FrameAnalysis | null | undefined }) {
  if (!analysis) return <div className="empty-analysis">暂无结构化结果</div>;
  const cats = analysis.cats ?? [];
  const adultStatus = analysis.adult_presence === "present"
    ? `检测到 ${analysis.adults.length} 位成人`
    : analysis.adult_presence === "not_detected"
      ? (analysis.infants.length > 0 ? "未检测到成人，仅检测到婴儿" : "未检测到成人或婴儿")
      : "无法可靠判断成人是否在场";
  return (
    <div className="analysis-panel">
      <div className="analysis-heading">
        <RiskBadge risk={analysis.overall_risk} />
        <span>{labels[analysis.image_quality] ?? analysis.image_quality}</span>
      </div>
      <p className="summary">{analysis.summary}</p>
      <div className={`adult-presence adult-${analysis.adult_presence}`}>
        <strong>成人监测</strong>
        <span>{adultStatus}</span>
      </div>
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
            <strong>{subjectLabel("婴儿", index, analysis.infants.length)}</strong>
            <RiskBadge risk={infant.risk_level} />
          </div>
          <dl>
            <div><dt>姿势</dt><dd>{labels[infant.posture] ?? infant.posture}</dd></div>
            <div><dt>口鼻遮挡</dt><dd>{labels[infant.mouth_nose_occlusion] ?? infant.mouth_nose_occlusion}</dd></div>
            <div><dt>被子</dt><dd>{labels[infant.blanket_coverage] ?? infant.blanket_coverage}</dd></div>
            <div><dt>置信度</dt><dd>{Math.round(infant.confidence * 100)}%</dd></div>
          </dl>
          {infant.evidence.length > 0 && (
            <ul className="evidence">{infant.evidence.map((item) => <li key={item}>{item}</li>)}</ul>
          )}
        </section>
      ))}
      {analysis.infants.length === 0 && <div className="no-infant">当前画面未定位到婴儿</div>}
      {analysis.adults.map((adult, index) => (
        <section className="infant-card adult-card" key={`adult-${index}`}>
          <div className="infant-title">
            <strong>{subjectLabel("成人", index, analysis.adults.length)}</strong>
            <span className="adult-confidence">置信度 {Math.round(adult.confidence * 100)}%</span>
          </div>
          {adult.evidence.length > 0 && (
            <ul className="evidence">{adult.evidence.map((item) => <li key={item}>{item}</li>)}</ul>
          )}
        </section>
      ))}
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
  const [liveFrame, setLiveFrame] = useState<LiveFrameState | null>(null);
  const [provider, setProvider] = useState<ProviderName>("ollama");
  const [model, setModel] = useState("qwen3-vl:4b");
  const [rtspUrl, setRtspUrl] = useState(readRtspDraft);
  const [minFrameIntervalSeconds, setMinFrameIntervalSeconds] = useState(1);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [transport, setTransport] = useState<"tcp" | "udp">("tcp");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [geminiKey, setGeminiKey] = useState("");
  const [geminiKeyVisible, setGeminiKeyVisible] = useState(false);
  const [geminiKeyBusy, setGeminiKeyBusy] = useState(false);
  const [geminiKeyError, setGeminiKeyError] = useState<string | null>(null);
  const geminiDialogRef = useRef<HTMLDialogElement>(null);
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
        let event: unknown;
        try {
          event = typeof message.data === "string" ? JSON.parse(message.data) : null;
        } catch {
          return;
        }
        if (!isRecord(event) || typeof event.type !== "string") return;
        if (event.type === "heartbeat") return;
        if (event.type === "status" && isMonitorStatus(event.data)) setStatus(event.data);
        if (event.type === "capture" && isRecord(event.data) && typeof event.data.image_url === "string") {
          setLiveFrame({
            imageUrl: `${event.data.image_url}&t=${Date.now()}`,
            capturedAt: typeof event.data.captured_at === "string" ? event.data.captured_at : null,
            width: isNonNegativeNumber(event.data.width) ? event.data.width : null,
            height: isNonNegativeNumber(event.data.height) ? event.data.height : null,
            actualIntervalSeconds: isNullableNumber(event.data.actual_interval_seconds) ? event.data.actual_interval_seconds : null,
            analysisBitrateKbps: isNullableNumber(event.data.analysis_bitrate_kbps) ? event.data.analysis_bitrate_kbps : null,
          });
        }
        if (
          (event.type === "analysis_completed" || event.type === "analysis_failed")
          && isRecord(event.data)
          && typeof event.data.id === "string"
        ) {
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

  const updateGeminiProvider = (info: ProviderInfo) => {
    setProviders((current) => current ? { ...current, gemini: info } : current);
    if (provider === "gemini") {
      setModel((current) => info.models.includes(current) ? current : (info.models[0] ?? info.default_model));
    }
  };

  const openGeminiSettings = () => {
    setGeminiKey("");
    setGeminiKeyVisible(false);
    setGeminiKeyError(null);
    geminiDialogRef.current?.showModal();
  };

  const closeGeminiSettings = () => {
    if (geminiKeyBusy) return;
    setGeminiKey("");
    setGeminiKeyError(null);
    geminiDialogRef.current?.close();
  };

  async function saveGeminiKey(event: FormEvent) {
    event.preventDefault();
    setGeminiKeyBusy(true);
    setGeminiKeyError(null);
    try {
      const response = await fetch("/api/providers/gemini/key", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: geminiKey }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "Gemini Key 验证失败");
      updateGeminiProvider(body);
      setGeminiKey("");
      geminiDialogRef.current?.close();
    } catch (reason) {
      setGeminiKeyError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setGeminiKeyBusy(false);
    }
  }

  async function resetGeminiKey() {
    setGeminiKeyBusy(true);
    setGeminiKeyError(null);
    try {
      const response = await fetch("/api/providers/gemini/key", { method: "DELETE" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "恢复 Gemini Key 配置失败");
      updateGeminiProvider(body);
      setGeminiKey("");
      geminiDialogRef.current?.close();
    } catch (reason) {
      setGeminiKeyError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setGeminiKeyBusy(false);
    }
  }

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
          min_frame_interval_seconds: minFrameIntervalSeconds,
          provider,
          model,
          rtsp_transport: transport,
        }),
      });
      if (!response.ok) throw new Error((await response.json()).detail ?? "启动失败");
      setLiveFrame(null);
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
        <div className="topbar-actions">
          <button className="gemini-key-button" type="button" onClick={openGeminiSettings}>
            <i className={providers?.gemini.key_configured ? "configured" : "unconfigured"} />
            Gemini Key
          </button>
          <div className={`connection-state state-${status.state}`}>
            <i />{connectionLabel(status)}
          </div>
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
            <span>最小帧间隔（秒）</span>
            <input type="number" min="0.1" max="3600" step="0.1" value={minFrameIntervalSeconds} onChange={(e) => setMinFrameIntervalSeconds(Number(e.target.value))} disabled={active} />
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
          </div>
        )}
        <div className="provider-health">
          {providers && (Object.entries(providers) as [ProviderName, ProviderInfo][]).map(([name, info]) => (
            <span key={name} className={info.available ? "healthy" : "unhealthy"}><i />{name === "ollama" ? "Ollama" : "Gemini"}: {info.detail}{info.version ? ` · ${info.version}` : ""}</span>
          ))}
        </div>
        {error && <div className="error-banner">{error}</div>}
      </section>

      <dialog
        className="gemini-dialog"
        ref={geminiDialogRef}
        onCancel={(event) => {
          event.preventDefault();
          if (!geminiKeyBusy) closeGeminiSettings();
        }}
        onClick={(event) => {
          if (event.target === geminiDialogRef.current) closeGeminiSettings();
        }}
      >
        <form onSubmit={saveGeminiKey}>
          <div className="dialog-heading">
            <div>
              <span className="eyebrow">CLOUD PROVIDER</span>
              <h2>Gemini API Key</h2>
            </div>
            <button className="dialog-close" type="button" onClick={closeGeminiSettings} disabled={geminiKeyBusy} aria-label="关闭">×</button>
          </div>
          <div className={`key-status ${providers?.gemini.key_configured ? "configured" : "unconfigured"}`}>
            <i />
            <span>
              {providers?.gemini.key_source === "web"
                ? "正在使用网页临时配置"
                : providers?.gemini.key_source === "environment"
                  ? "正在使用环境变量配置"
                  : "尚未配置 Gemini Key"}
            </span>
          </div>
          <p className="dialog-copy">Key 只保存在后端进程内存中，不会写入浏览器存储、历史记录或 API 响应。服务重启后，网页配置会消失。</p>
          <label className="dialog-field">
            <span>新的 Gemini API Key</span>
            <div className="secret-input">
              <input
                data-testid="gemini-key-input"
                type={geminiKeyVisible ? "text" : "password"}
                value={geminiKey}
                onChange={(event) => setGeminiKey(event.target.value)}
                autoComplete="off"
                autoCapitalize="none"
                spellCheck={false}
                placeholder="粘贴 Google AI Studio API Key"
                disabled={geminiKeyBusy || active}
                required
              />
              <button type="button" onClick={() => setGeminiKeyVisible((visible) => !visible)} disabled={geminiKeyBusy}>
                {geminiKeyVisible ? "隐藏" : "显示"}
              </button>
            </div>
          </label>
          <div className="dialog-privacy">使用 Gemini 或 Gemma 云端模型时，采样帧会发送至 Google API。保存前会连接 Google API 验证 Key 并刷新可用模型；仅应在本机或可信 HTTPS 连接中提交。</div>
          {active && <div className="dialog-error">请先停止当前监控会话，再更换 Gemini Key。</div>}
          {geminiKeyError && <div className="dialog-error">{geminiKeyError}</div>}
          <div className="dialog-actions">
            {providers?.gemini.key_source === "web" && (
              <button className="reset-key" type="button" onClick={() => void resetGeminiKey()} disabled={geminiKeyBusy || active}>恢复启动配置</button>
            )}
            <button className="cancel-key" type="button" onClick={closeGeminiSettings} disabled={geminiKeyBusy}>取消</button>
            <button className="save-key" type="submit" disabled={geminiKeyBusy || active || !geminiKey.trim()}>
              {geminiKeyBusy ? "正在验证…" : "验证并使用"}
            </button>
          </div>
        </form>
      </dialog>

      <section className="metrics-grid">
        <div><span>已提交</span><strong title={String(status.submitted_count)}>{status.submitted_count}</strong></div>
        <div><span>已完成</span><strong title={String(status.completed_count)}>{status.completed_count}</strong></div>
        <div><span>失败</span><strong title={String(status.error_count)}>{status.error_count}</strong></div>
        <div><span>累计输入 Token</span><strong title={formatTokens(status.input_tokens)}>{formatTokens(status.input_tokens)}</strong></div>
        <div><span>累计输出 Token</span><strong title={formatTokens(status.output_tokens)}>{formatTokens(status.output_tokens)}</strong></div>
        <div><span>最近延迟</span><strong title={status.last_latency_ms ? `${(status.last_latency_ms / 1000).toFixed(1)}s` : "—"}>{status.last_latency_ms ? `${(status.last_latency_ms / 1000).toFixed(1)}s` : "—"}</strong></div>
        <div title={`${formatBytes(status.history.bytes)} / ${formatBytes(status.history.max_bytes)}`}><span>历史内存</span><strong>{formatBytes(status.history.bytes)}</strong><small>/ {formatBytes(status.history.max_bytes)}</small></div>
      </section>

      {status.last_error && <div className="stream-error" title={status.last_error}>{status.last_error}</div>}

      <main className="monitor-grid">
        <section className="panel primary-live">
          <div className="panel-heading">
            <div><span className="section-number">01</span><span className="live-dot" /><h2>最近即时抽帧</h2></div>
            <div className="frame-meta" title={formatTime(liveFrame?.capturedAt ?? status.last_capture_at)}>{formatTime(liveFrame?.capturedAt ?? status.last_capture_at)}</div>
          </div>
          {liveFrame ? (
            <div
              className="live-frame"
              style={liveFrame.width && liveFrame.height ? { aspectRatio: `${liveFrame.width} / ${liveFrame.height}` } : undefined}
            >
              <img src={liveFrame.imageUrl} alt="最近即时抽帧" />
            </div>
          ) : <div className="empty-live">尚无实时画面</div>}
          <div className="live-debug" aria-label="即时捕获调试信息">
            <div>
              <span>RTSP 原始分辨率</span>
              <strong title={liveFrame?.width && liveFrame.height ? `${liveFrame.width} × ${liveFrame.height}` : "—"}>
                {liveFrame?.width && liveFrame.height ? `${liveFrame.width} × ${liveFrame.height}` : "—"}
              </strong>
            </div>
            <div>
              <span>实际 / 设定最小帧间隔</span>
              <strong title={`${formatFrameInterval(liveFrame?.actualIntervalSeconds)} / ${formatFrameInterval(status.min_frame_interval_seconds)}`}>
                {formatFrameInterval(liveFrame?.actualIntervalSeconds)} / {formatFrameInterval(status.min_frame_interval_seconds)}
              </strong>
            </div>
            <div>
              <span>分析 JPEG 数据率</span>
              <strong title={`${formatAnalysisBitrate(liveFrame?.analysisBitrateKbps ?? null)}；不是摄像头原始编码码率`}>
                {formatAnalysisBitrate(liveFrame?.analysisBitrateKbps ?? null)}
              </strong>
            </div>
          </div>
          <p className="live-note">每次模型准备提交请求时才即时连接 RTSP 抓取一帧；模型返回前不会继续抽帧，也不会产生覆盖丢帧。</p>
        </section>

        <section className="panel analysis-result">
          <div className="panel-heading"><div><span className="section-number">02</span><h2>结构化状态</h2></div></div>
          <AnalysisPanel analysis={detail?.analysis} />
        </section>

      </main>

      <section className="panel latest-analysis-section">
        <div className="panel-heading">
          <div><span className="section-number">03</span><h2>最近完成分析</h2></div>
          <span title={`抽帧 ${formatTime(detail?.captured_at ?? null)} · 完成 ${formatTime(detail?.completed_at ?? null)}`}>
            抽帧 {formatTime(detail?.captured_at ?? null)} · 完成 {formatTime(detail?.completed_at ?? null)}
          </span>
        </div>
        <AnnotatedFrame detail={detail} />
        <p>完整尺寸标注视图；图片、模型框和当前选中的历史记录严格对应。</p>
      </section>

      <section className="history-section">
        <div className="history-heading">
          <div><span className="section-number">04</span><h2>进程内调试历史</h2></div>
          <span title={`最新在前 · 已加载 ${history.length} / 内存共 ${status.history.items} 帧`}>最新在前 · 已加载 {history.length} / 内存共 {status.history.items} 帧</span>
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
                <strong title={formatTime(item.captured_at)}>{formatTime(item.captured_at)}</strong>
                <span title={`${item.provider} · ${item.model}`}>{item.provider} · {item.model}</span>
                <span title={historySubjectText(item)}>{historySubjectText(item)}</span>
                <span title={historyCallText(item)}>{historyCallText(item)}</span>
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
          <div className="history-heading"><div><span className="section-number">05</span><h2>请求审计</h2></div><span title={detail.prompt_version}>{detail.prompt_version}</span></div>
          <div className="debug-grid">
            <details open><summary>逐次调用审计</summary><AttemptAudit detail={detail} /></details>
            <details><summary>原始模型响应</summary>{detail.raw_responses.length > 0 ? detail.raw_responses.map((response, index) => {
              const attempt = detail.attempt_details.find((item) => item.response_index === index);
              return <div className="response-attempt" key={index}><div className="attempt-label">调用 {attempt?.attempt ?? index + 1} · 模型响应 {index + 1}</div><JsonCode value={response} /></div>;
            }) : <pre>No response</pre>}</details>
            <details><summary>会话基线 Prompt（首次调用）</summary><pre>{detail.prompt}</pre></details>
            <details><summary>JSON Schema</summary><JsonCode value={detail.output_schema} /></details>
            <details><summary>调用参数与汇总用量</summary><JsonCode value={{ generation: detail.generation_params }} /></details>
          </div>
        </section>
      )}

      <footer>BabyMonitorVL MVP · 所有语义定位与状态判断均来自多模态大模型</footer>
    </div>
  );
}
