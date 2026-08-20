import { readSseStream, type ParsedSseEvent } from '../lib/sse'
import { normalizeAnswerStatus } from '../lib/status'
import type {
  BBox,
  ChatDoneData,
  ChatRequest,
  DocumentItem,
  EvaluationBadcase,
  EvaluationGate,
  EvaluationReport,
  EvaluationVariant,
  EvidenceSource,
  RetrievalCandidate,
  RetrievalRoute,
  RetrievalTraceData,
} from '../types'

const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim() ?? ''
const API_BASE_URL = configuredBaseUrl.replace(/\/$/, '')

type UnknownRecord = Record<string, unknown>

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function asOptionalString(value: unknown): string | null | undefined {
  if (value === null) return null
  return typeof value === 'string' ? value : undefined
}

function asNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function asNullableNumber(value: unknown): number | null | undefined {
  if (value === null) return null
  return asNumber(value)
}

function asBBox(value: unknown): BBox | null | undefined {
  if (value === null) return null
  if (
    Array.isArray(value) &&
    value.length === 4 &&
    value.every((coordinate) => typeof coordinate === 'number')
  ) {
    return value as [number, number, number, number]
  }
  if (isRecord(value)) return value as BBox
  return undefined
}

async function requestJson(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...init?.headers,
    },
  })

  if (!response.ok) {
    const detail = await response.text().catch(() => '')
    throw new Error(detail || `请求失败：HTTP ${response.status}`)
  }

  return response.json() as Promise<unknown>
}

function normalizeDocument(value: unknown): DocumentItem | null {
  if (!isRecord(value)) return null
  const documentId = asString(value.document_id ?? value.id)
  const filename = asString(value.filename ?? value.name)
  if (!documentId || !filename) return null

  return {
    document_id: documentId,
    filename,
    status: asString(value.status, 'unknown'),
    page_count: asNullableNumber(value.page_count),
    chunk_count: asNullableNumber(value.chunk_count),
    index_version: asOptionalString(value.index_version),
    created_at: asOptionalString(value.created_at ?? value.ingested_at),
    sha256: asOptionalString(value.sha256 ?? value.content_hash),
  }
}

export async function listDocuments(): Promise<DocumentItem[]> {
  const payload = await requestJson('/api/v1/documents')
  const rawItems = Array.isArray(payload)
    ? payload
    : isRecord(payload) && Array.isArray(payload.documents)
      ? payload.documents
      : isRecord(payload) && Array.isArray(payload.items)
        ? payload.items
        : []

  return rawItems
    .map(normalizeDocument)
    .filter((item): item is DocumentItem => item !== null)
}

export async function ingestDocument(file: File): Promise<DocumentItem | null> {
  const body = new FormData()
  body.append('file', file)
  const payload = await requestJson('/api/v1/documents/ingest', {
    method: 'POST',
    body,
  })
  if (isRecord(payload) && isRecord(payload.document)) {
    return normalizeDocument(payload.document)
  }
  return normalizeDocument(payload)
}

function normalizeSource(value: unknown, index: number): EvidenceSource | null {
  if (!isRecord(value)) return null
  const hit = isRecord(value.hit) ? value.hit : value
  const locator = isRecord(hit.source)
    ? hit.source
    : isRecord(value.source)
      ? value.source
      : isRecord(value.locator)
        ? value.locator
        : hit
  const scores = isRecord(hit.scores)
    ? hit.scores
    : isRecord(value.scores)
      ? value.scores
      : {}
  const chunkId = asString(locator.chunk_id ?? hit.chunk_id ?? value.chunk_id)
  const filename = asString(locator.filename ?? hit.filename ?? value.filename)
  if (!chunkId || !filename) return null

  const sectionPathValue = locator.section_path ?? value.section_path
  const sectionPath =
    typeof sectionPathValue === 'string' ||
    (Array.isArray(sectionPathValue) &&
      sectionPathValue.every((part) => typeof part === 'string'))
      ? sectionPathValue
      : undefined

  const tableRowValue = locator.table_row ?? value.table_row
  const tableRow =
    typeof tableRowValue === 'number' || typeof tableRowValue === 'string'
      ? tableRowValue
      : tableRowValue === null
        ? null
        : undefined

  return {
    citation_id: asString(value.citation_id ?? hit.citation_id ?? value.id, `C${index + 1}`),
    document_id: asString(
      locator.document_id ?? hit.document_id ?? value.document_id,
      'unknown',
    ),
    filename,
    page_number: asNullableNumber(
      locator.page_number ?? hit.page_number ?? value.page_number,
    ),
    section_path: sectionPath,
    bbox: asBBox(locator.bbox ?? hit.bbox ?? value.bbox),
    table_row: tableRow,
    chunk_id: chunkId,
    content: asOptionalString(hit.content ?? value.content) ?? undefined,
    snippet:
      asOptionalString(hit.text ?? hit.snippet ?? value.snippet ?? value.text) ??
      undefined,
    score: asNumber(hit.score ?? value.score),
    bm25_score: asNumber(
      scores.bm25_score ?? scores.bm25 ?? scores.lexical ?? hit.bm25_score,
    ),
    vector_score: asNumber(
      scores.vector_score ?? scores.vector ?? scores.dense ?? hit.vector_score,
    ),
    rrf_score: asNumber(
      scores.rrf_score ?? scores.rrf ?? scores.fusion ?? hit.rrf_score,
    ),
    rerank_score: asNumber(
      scores.rerank_score ?? scores.rerank ?? hit.rerank_score,
    ),
  }
}

function normalizeRoute(value: unknown): RetrievalRoute | undefined {
  if (typeof value === 'string') return { name: value }
  if (!isRecord(value)) return undefined
  const weights = isRecord(value.weights) ? value.weights : {}

  return {
    name: asString(
      value.name ?? value.route_name ?? value.route ?? value.type,
      'hybrid',
    ),
    reason: asOptionalString(value.reason) ?? undefined,
    lexical_weight: asNumber(
      value.lexical_weight ??
        value.bm25_weight ??
        weights.lexical_weight ??
        weights.lexical ??
        weights.bm25,
    ),
    vector_weight: asNumber(
      value.vector_weight ??
        weights.vector_weight ??
        weights.vector ??
        weights.dense,
    ),
  }
}

function normalizeCandidate(value: unknown): RetrievalCandidate | null {
  if (!isRecord(value)) return null
  const hit = isRecord(value.hit) ? value.hit : value
  const source = isRecord(hit.source) ? hit.source : {}
  const scores = isRecord(hit.scores)
    ? hit.scores
    : isRecord(value.scores)
      ? value.scores
      : {}
  const ranks = isRecord(value.ranks) ? value.ranks : {}
  const chunkId = asString(hit.chunk_id ?? value.chunk_id ?? value.id)
  if (!chunkId) return null

  return {
    chunk_id: chunkId,
    filename:
      asOptionalString(source.filename ?? hit.filename ?? value.filename) ??
      undefined,
    bm25_rank: asNumber(value.bm25_rank ?? ranks.bm25),
    bm25_score: asNumber(
      value.bm25_score ?? scores.bm25_score ?? scores.bm25 ?? scores.lexical,
    ),
    vector_rank: asNumber(value.vector_rank ?? ranks.vector),
    vector_score: asNumber(
      value.vector_score ??
        scores.vector_score ??
        scores.vector ??
        scores.dense,
    ),
    rrf_score: asNumber(
      value.rrf_score ?? scores.rrf_score ?? scores.rrf ?? scores.fusion,
    ),
    rerank_score: asNumber(
      value.rerank_score ??
        scores.rerank_score ??
        scores.rerank ??
        hit.score,
    ),
    final_rank: asNumber(value.final_rank ?? value.rank ?? ranks.final),
  }
}

function normalizeDegradationCodes(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.filter((code): code is string => typeof code === 'string' && Boolean(code))
}

function normalizeTrace(value: unknown): RetrievalTraceData | undefined {
  if (!isRecord(value)) return undefined
  const rawCandidates = Array.isArray(value.candidates)
    ? value.candidates
    : Array.isArray(value.results)
      ? value.results
      : []
  const candidates = rawCandidates
    .map(normalizeCandidate)
    .filter((item): item is RetrievalCandidate => item !== null)

  return {
    route: normalizeRoute(value.route),
    candidates,
    latency_ms: asNumber(value.latency_ms),
    rerank_applied:
      typeof value.rerank_applied === 'boolean'
        ? value.rerank_applied
        : undefined,
    degraded: typeof value.degraded === 'boolean' ? value.degraded : undefined,
    degradation_codes: normalizeDegradationCodes(
      value.degradation_codes ?? value.degradation_reasons,
    ),
  }
}

function normalizeSourcesPayload(payload: unknown): {
  sources: EvidenceSource[]
  trace?: RetrievalTraceData
} {
  const rawSources = Array.isArray(payload)
    ? payload
    : isRecord(payload) && Array.isArray(payload.sources)
      ? payload.sources
      : isRecord(payload) && Array.isArray(payload.hits)
        ? payload.hits
      : []
  const sources = rawSources
    .map(normalizeSource)
    .filter((item): item is EvidenceSource => item !== null)
  const traceValue = isRecord(payload)
    ? payload.retrieval_trace ?? payload.trace
    : undefined
  let trace = normalizeTrace(traceValue)
  if (!trace && isRecord(payload) && (payload.route || Array.isArray(payload.hits))) {
    trace = {
      route: normalizeRoute(payload.route),
      candidates: sources.map((source, index) => ({
        chunk_id: source.chunk_id,
        filename: source.filename,
        bm25_score: source.bm25_score,
        vector_score: source.vector_score,
        rrf_score: source.rrf_score,
        rerank_score: source.rerank_score ?? source.score,
        final_rank: index + 1,
      })),
      latency_ms: asNumber(payload.latency_ms),
      rerank_applied:
        typeof payload.rerank_applied === 'boolean'
          ? payload.rerank_applied
          : undefined,
      degraded:
        typeof payload.degraded === 'boolean' ? payload.degraded : undefined,
      degradation_codes: normalizeDegradationCodes(
        payload.degradation_codes ?? payload.degradation_reasons,
      ),
    }
  }

  return { sources, trace }
}

function normalizeErrorMessage(payload: unknown): string {
  if (typeof payload === 'string') return payload
  if (isRecord(payload)) {
    return asString(payload.message ?? payload.detail ?? payload.error, '流式请求失败')
  }
  return '流式请求失败'
}

function normalizeDone(payload: unknown): ChatDoneData {
  if (typeof payload === 'string') {
    return { status: normalizeAnswerStatus(payload) }
  }
  if (!isRecord(payload)) return { status: 'needs_review' }

  return {
    status: normalizeAnswerStatus(payload.status ?? payload.answer_status),
    evidence_status:
      asOptionalString(payload.evidence_status)?.toUpperCase() ?? undefined,
    answer: asOptionalString(payload.answer ?? payload.text) ?? undefined,
    trace: normalizeTrace(payload.retrieval_trace ?? payload.trace),
    request_id: asOptionalString(payload.request_id) ?? undefined,
  }
}

export interface ChatStreamHandlers {
  onSources: (sources: EvidenceSource[], trace?: RetrievalTraceData) => void
  onToken: (token: string) => void
  onDone: (data: ChatDoneData) => void
  onError: (message: string) => void
}

function dispatchChatEvent(
  event: ParsedSseEvent,
  handlers: ChatStreamHandlers,
): void {
  if (event.event === 'sources') {
    const payload = normalizeSourcesPayload(event.data)
    handlers.onSources(payload.sources, payload.trace)
    return
  }
  if (event.event === 'token') {
    if (typeof event.data === 'string') {
      handlers.onToken(event.data)
    } else if (isRecord(event.data)) {
      handlers.onToken(asString(event.data.token ?? event.data.text))
    }
    return
  }
  if (event.event === 'done') {
    handlers.onDone(normalizeDone(event.data))
    return
  }
  handlers.onError(normalizeErrorMessage(event.data))
}

export async function streamChat(
  query: string,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const requestBody: ChatRequest = { query }
  const response = await fetch(`${API_BASE_URL}/api/v1/chat/stream`, {
    method: 'POST',
    headers: {
      Accept: 'text/event-stream',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(requestBody),
    signal,
  })

  await readSseStream(
    response,
    (event) => dispatchChatEvent(event, handlers),
    signal,
  )
}

export async function searchRetrieval(
  query: string,
): Promise<{ sources: EvidenceSource[]; trace?: RetrievalTraceData }> {
  const payload = await requestJson('/api/v1/retrieval/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query }),
  })
  return normalizeSourcesPayload(payload)
}

function normalizeMetrics(value: unknown): Record<string, number | null> {
  if (!isRecord(value)) return {}
  const result: Record<string, number | null> = {}
  for (const [key, metric] of Object.entries(value)) {
    if (metric === null || (typeof metric === 'number' && Number.isFinite(metric))) {
      result[key] = metric
    }
  }
  return result
}

function normalizeVariant(value: unknown): EvaluationVariant | null {
  if (!isRecord(value)) return null
  const name = asString(value.name ?? value.variant ?? value.method)
  if (!name) return null
  const metricsSource = isRecord(value.metrics) ? value.metrics : value

  return {
    name,
    label: asOptionalString(value.label) ?? undefined,
    metrics: normalizeMetrics(metricsSource),
    latency_p50_ms:
      asNullableNumber(value.latency_p50_ms) ??
      asNullableNumber(metricsSource.latency_p50_ms),
    latency_p95_ms:
      asNullableNumber(value.latency_p95_ms) ??
      asNullableNumber(metricsSource.latency_p95_ms),
  }
}

function normalizeBadcase(value: unknown, index: number): EvaluationBadcase | null {
  if (!isRecord(value)) return null
  const missingEvidence = Array.isArray(value.missing_evidence)
    ? value.missing_evidence
        .map((item) =>
          typeof item === 'string'
            ? item
            : isRecord(item)
              ? asString(item.evidence_id ?? item.chunk_id ?? item.description)
              : '',
        )
        .filter(Boolean)
    : typeof value.missing_evidence === 'string'
      ? [value.missing_evidence]
      : []
  const missingAfterRerank = value.missing_after_rerank
  const generatedSummary = [
    missingEvidence.length > 0
      ? `缺失证据：${missingEvidence.join('、')}`
      : '',
    missingAfterRerank === true
      ? '关键证据在 Rerank 后缺失'
      : Array.isArray(missingAfterRerank) && missingAfterRerank.length > 0
        ? `Rerank 后缺失：${missingAfterRerank.map(String).join('、')}`
        : typeof missingAfterRerank === 'string'
          ? `Rerank 后缺失：${missingAfterRerank}`
          : '',
  ]
    .filter(Boolean)
    .join('；')
  const summary =
    asString(value.summary ?? value.description ?? value.reason) ||
    generatedSummary ||
    '该 Badcase 未提供文字说明。'

  return {
    id: asString(value.id ?? value.case_id, `badcase-${index + 1}`),
    category: asString(value.category ?? value.type, '未分类'),
    question: asOptionalString(value.question) ?? undefined,
    summary,
    split: asOptionalString(value.split) ?? undefined,
  }
}

function normalizeGate(
  value: unknown,
  fallbackName = '',
): EvaluationGate | null {
  if (typeof value === 'boolean' || value === null) {
    return fallbackName ? { name: fallbackName, passed: value } : null
  }
  if (typeof value === 'string') {
    const normalizedStatus = value.toLowerCase()
    const passed = ['pass', 'passed', 'true'].includes(normalizedStatus)
      ? true
      : ['fail', 'failed', 'false'].includes(normalizedStatus)
        ? false
        : null
    return fallbackName ? { name: fallbackName, passed, detail: value } : null
  }
  if (!isRecord(value)) return null
  const name = asString(value.name ?? value.gate, fallbackName)
  if (!name) return null
  const status = asString(value.status).toLowerCase()
  const passed = typeof value.passed === 'boolean'
    ? value.passed
    : value.passed === null
      ? null
      : ['pass', 'passed'].includes(status)
        ? true
        : ['fail', 'failed'].includes(status)
          ? false
          : null

  return {
    name,
    passed,
    detail: asOptionalString(value.detail ?? value.description) ?? undefined,
  }
}

function normalizeGates(value: unknown): EvaluationGate[] {
  if (Array.isArray(value)) {
    return value
      .map((gate) => normalizeGate(gate))
      .filter((gate): gate is EvaluationGate => gate !== null)
  }
  if (!isRecord(value)) return []
  return Object.entries(value)
    .map(([name, gate]) => normalizeGate(gate, name))
    .filter((gate): gate is EvaluationGate => gate !== null)
}

function normalizeVariants(value: unknown): EvaluationVariant[] {
  if (Array.isArray(value)) {
    return value
      .map(normalizeVariant)
      .filter((variant): variant is EvaluationVariant => variant !== null)
  }
  if (!isRecord(value)) return []

  return Object.entries(value)
    .map(([name, scopes]) => {
      if (!isRecord(scopes)) return null
      const selectedMetrics = isRecord(scopes.test)
        ? scopes.test
        : isRecord(scopes.metrics)
          ? scopes.metrics
          : scopes
      return normalizeVariant({ name, metrics: selectedMetrics })
    })
    .filter((variant): variant is EvaluationVariant => variant !== null)
}

function normalizeBadcases(value: unknown): EvaluationBadcase[] {
  if (Array.isArray(value)) {
    return value
      .map(normalizeBadcase)
      .filter((item): item is EvaluationBadcase => item !== null)
  }
  if (!isRecord(value)) return []

  const flattened: unknown[] = []
  for (const [category, cases] of Object.entries(value)) {
    const entries = Array.isArray(cases) ? cases : [cases]
    for (const item of entries) {
      flattened.push(isRecord(item) ? { category, ...item } : { category, summary: item })
    }
  }
  return flattened
    .map(normalizeBadcase)
    .filter((item): item is EvaluationBadcase => item !== null)
}

function normalizeLimitations(value: unknown): string[] {
  const values = Array.isArray(value) ? value : value ? [value] : []
  return values
    .map((limitation) =>
      typeof limitation === 'string'
        ? limitation
        : isRecord(limitation)
          ? asString(limitation.description ?? limitation.summary ?? limitation.message)
          : '',
    )
    .filter(Boolean)
}

function normalizeRefusalThreshold(value: unknown): number | null {
  const direct = asNullableNumber(value)
  if (direct !== undefined) return direct
  if (!isRecord(value)) return null
  const byVariant = isRecord(value.by_variant) ? value.by_variant : {}
  const routed = byVariant.routed_hybrid_rrf_rerank
  if (isRecord(routed)) return asNullableNumber(routed.value) ?? null
  return asNullableNumber(routed) ?? null
}

export async function getLatestEvaluation(): Promise<EvaluationReport> {
  const payload = await requestJson('/api/v1/evaluation/latest')
  if (!isRecord(payload)) throw new Error('评测报告格式无效')
  const metrics = isRecord(payload.metrics) ? payload.metrics : {}
  const rawVariants = metrics.variants ?? payload.variants ?? payload.results ?? payload.methods
  const status = asString(payload.status, 'unknown')
  const rawEvidenceStatus = asString(payload.evidence_status)
  const evidenceStatus = status.toLowerCase() === 'not_run' || !rawEvidenceStatus
    ? 'NOT_VERIFIED'
    : rawEvidenceStatus.toUpperCase()

  return {
    status,
    evidence_status: evidenceStatus,
    generated_at: asOptionalString(payload.generated_at) ?? null,
    corpus_hash: asOptionalString(payload.corpus_hash) ?? null,
    dataset_version: asOptionalString(payload.dataset_version) ?? null,
    split: asOptionalString(payload.split) ?? null,
    parser_pass_rate:
      asNullableNumber(metrics.parser_success_rate) ??
      asNullableNumber(payload.parser_pass_rate) ??
      null,
    refusal_threshold:
      normalizeRefusalThreshold(metrics.refusal_threshold),
    variants: normalizeVariants(rawVariants),
    badcases: normalizeBadcases(payload.badcases),
    gates: normalizeGates(metrics.gates ?? payload.gates),
    limitations: normalizeLimitations(payload.limitations),
  }
}
