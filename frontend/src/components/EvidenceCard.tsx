import type { BBox, EvidenceSource } from '../types'
import { LinkIcon } from './Icons'

function formatSection(section: EvidenceSource['section_path']): string {
  if (Array.isArray(section)) return section.join(' / ')
  return section || '未标注章节'
}

function formatBBox(bbox: BBox | null | undefined): string {
  if (!bbox) return '—'
  if (Array.isArray(bbox)) return `[${bbox.map((value) => value.toFixed(0)).join(', ')}]`
  const values = [
    bbox.x1 ?? bbox.left,
    bbox.y1 ?? bbox.top,
    bbox.x2 ?? bbox.right,
    bbox.y2 ?? bbox.bottom,
  ]
  return values.every((value) => typeof value === 'number')
    ? `[${values.map((value) => Number(value).toFixed(0)).join(', ')}]`
    : '结构化区域'
}

function Score({ label, value }: { label: string; value?: number }) {
  if (value === undefined) return null
  return (
    <span className="score-chip">
      {label} <strong>{value.toFixed(3)}</strong>
    </span>
  )
}

interface EvidenceCardProps {
  source: EvidenceSource
  index: number
}

export function EvidenceCard({ source, index }: EvidenceCardProps) {
  return (
    <article className="evidence-card">
      <header>
        <span className="citation-index">
          <LinkIcon />
          {source.citation_id || `C${index + 1}`}
        </span>
        <span className="chunk-id" title={source.chunk_id}>
          {source.chunk_id.slice(0, 12)}
        </span>
      </header>
      <h4>{source.filename}</h4>
      <dl className="locator-grid">
        <div>
          <dt>页码</dt>
          <dd>{source.page_number ?? '—'}</dd>
        </div>
        <div>
          <dt>章节路径</dt>
          <dd>{formatSection(source.section_path)}</dd>
        </div>
        <div>
          <dt>区域坐标</dt>
          <dd>{formatBBox(source.bbox)}</dd>
        </div>
        <div>
          <dt>表格行</dt>
          <dd>{source.table_row ?? '—'}</dd>
        </div>
      </dl>
      {(source.snippet || source.content) && (
        <p className="evidence-snippet">{source.snippet || source.content}</p>
      )}
      <div className="score-row" aria-label="证据得分">
        <Score label="BM25" value={source.bm25_score} />
        <Score label="Vector" value={source.vector_score} />
        <Score label="RRF" value={source.rrf_score} />
        <Score label="Rerank" value={source.rerank_score ?? source.score} />
      </div>
    </article>
  )
}
