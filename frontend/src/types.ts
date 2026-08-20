export type AnswerStatus =
  | 'answered'
  | 'insufficient_evidence'
  | 'needs_review'

export type BBox =
  | [number, number, number, number]
  | {
      x1?: number
      y1?: number
      x2?: number
      y2?: number
      left?: number
      top?: number
      right?: number
      bottom?: number
    }

export interface SourceLocator {
  document_id: string
  filename: string
  page_number?: number | null
  section_path?: string | string[] | null
  bbox?: BBox | null
  table_row?: number | string | null
  chunk_id: string
}

export interface EvidenceSource extends SourceLocator {
  citation_id: string
  content?: string
  snippet?: string
  score?: number
  bm25_score?: number
  vector_score?: number
  rrf_score?: number
  rerank_score?: number
}

export interface RetrievalRoute {
  name: string
  reason?: string
  lexical_weight?: number
  vector_weight?: number
}

export interface RetrievalCandidate {
  chunk_id: string
  filename?: string
  bm25_rank?: number
  bm25_score?: number
  vector_rank?: number
  vector_score?: number
  rrf_score?: number
  rerank_score?: number
  final_rank?: number
}

export interface RetrievalTraceData {
  route?: RetrievalRoute
  candidates: RetrievalCandidate[]
  latency_ms?: number
  rerank_applied?: boolean
  degraded?: boolean
  degradation_codes: string[]
}

export interface DocumentItem {
  document_id: string
  filename: string
  status: string
  page_count?: number | null
  chunk_count?: number | null
  index_version?: string | null
  created_at?: string | null
  sha256?: string | null
}

export interface ChatDoneData {
  status: AnswerStatus
  evidence_status?: string
  answer?: string
  trace?: RetrievalTraceData
  request_id?: string
}

export interface ChatRequest {
  query: string
}

export interface EvaluationVariant {
  name: string
  label?: string
  metrics: Record<string, number | null>
  latency_p50_ms?: number | null
  latency_p95_ms?: number | null
}

export interface EvaluationBadcase {
  id: string
  category: 'Parser' | '召回池' | 'Rerank' | '引用' | string
  question?: string
  summary: string
  split?: string
}

export interface EvaluationGate {
  name: string
  passed: boolean | null
  detail?: string
}

export interface EvaluationReport {
  status: string
  evidence_status: string
  generated_at?: string | null
  corpus_hash?: string | null
  dataset_version?: string | null
  split?: string | null
  parser_pass_rate?: number | null
  refusal_threshold?: number | null
  variants: EvaluationVariant[]
  badcases: EvaluationBadcase[]
  gates: EvaluationGate[]
  limitations: string[]
}
