export type ProviderName = "ollama" | "gemini";
export type Risk = "normal" | "watch" | "alert" | "unknown";
export type Box = [number, number, number, number];
export type MouthNoseOcclusion = "clear" | "partially_covered" | "fully_covered" | "not_visible" | "unknown";
export type BlanketCoverage =
  | "absent"
  | "present_not_covering"
  | "lower_body"
  | "torso"
  | "near_mouth_nose"
  | "partially_covering_mouth_nose"
  | "covering_mouth_nose"
  | "unknown";
export type RelatedObjectKind = "blanket" | "pillow" | "toy" | "hand" | "other_occluder";
export type ObjectRelation =
  | "near_mouth_nose"
  | "partially_covers_mouth_nose"
  | "covers_mouth_nose"
  | "covers_body"
  | "near_body"
  | "unknown";

export interface ProviderInfo {
  available: boolean;
  detail: string;
  models: string[];
  default_model: string;
  version?: string;
  cloud: boolean;
  models_dynamic: boolean;
  key_configured?: boolean;
  key_source?: "none" | "environment" | "web";
}

export interface RelatedObject {
  kind: RelatedObjectKind;
  box: Box;
  relation: ObjectRelation;
}

export interface InfantObservation {
  infant_box: Box;
  mouth_nose_box: Box | null;
  posture: string;
  mouth_nose_occlusion: MouthNoseOcclusion;
  blanket_coverage: BlanketCoverage;
  related_objects: RelatedObject[];
  risk_level: Risk;
  confidence: number;
  evidence: string[];
}

export interface CatObservation {
  cat_box: Box;
  proximity_to_infant: "separate" | "near_infant" | "overlapping_infant" | "unknown";
  confidence: number;
  evidence: string[];
}

export type AdultPresence = "present" | "not_detected" | "unknown";

export interface AdultObservation {
  adult_box: Box;
  confidence: number;
  evidence: string[];
}

export interface FrameAnalysis {
  schema_version: "1.3";
  summary: string;
  image_quality: string;
  infants: InfantObservation[];
  adult_presence: AdultPresence;
  adults: AdultObservation[];
  cats: CatObservation[];
  overall_risk: Risk;
  risk_reasons: string[];
}

export interface HistorySummary {
  id: string;
  session_id: string;
  captured_at: string;
  completed_at: string | null;
  provider: ProviderName;
  model: string;
  status: "pending" | "success" | "error";
  analysis: FrameAnalysis | null;
  overall_risk: Risk | null;
  latency_ms: number | null;
  attempts: number;
  input_tokens: number | null;
  output_tokens: number | null;
  error: string | null;
  image_width: number;
  image_height: number;
  image_url: string;
}

export interface AnalysisAttempt {
  attempt: number;
  prompt: string;
  outcome: "success" | "validation_error" | "provider_error" | "cancelled";
  error_type: string | null;
  error: string | null;
  response_index: number | null;
  usage: Record<string, unknown>;
  warnings: string[];
  will_retry: boolean;
  retry_reason: string | null;
}

export interface HistoryDetail extends HistorySummary {
  source: string;
  analysis: FrameAnalysis | null;
  raw_responses: string[];
  errors: string[];
  warnings: string[];
  attempt_details: AnalysisAttempt[];
  prompt_version: string;
  prompt: string;
  output_schema: Record<string, unknown>;
  generation_params: Record<string, unknown>;
  image_width: number;
  image_height: number;
}

export interface MonitorStatus {
  state: "stopped" | "connecting" | "streaming" | "reconnecting";
  session_id: string | null;
  source: string | null;
  provider: ProviderName | null;
  model: string | null;
  fps: number | null;
  capture_count: number;
  submitted_count: number;
  completed_count: number;
  error_count: number;
  dropped_count: number;
  last_capture_at: string | null;
  last_analysis_at: string | null;
  last_latency_ms: number | null;
  last_record_id: string | null;
  last_error: string | null;
  reconnect_attempt: number;
  reconnect_delay_seconds: number | null;
  input_tokens: number;
  output_tokens: number;
  history: { items: number; bytes: number; max_bytes: number };
}
