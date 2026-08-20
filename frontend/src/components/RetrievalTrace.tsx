import type { RetrievalCandidate, RetrievalTraceData } from '../types'
import { RouteIcon } from './Icons'

function formatScore(value?: number): string {
  return value === undefined ? '—' : value.toFixed(3)
}

function RankScore({ rank, score }: { rank?: number; score?: number }) {
  return (
    <span className="rank-score">
      <strong>{formatScore(score)}</strong>
      <small>{rank === undefined ? '未命中' : `#${rank}`}</small>
    </span>
  )
}

function CandidateRow({ candidate }: { candidate: RetrievalCandidate }) {
  return (
    <tr>
      <td>
        <span className="final-rank">{candidate.final_rank ?? '—'}</span>
      </td>
      <td>
        <strong className="candidate-file">{candidate.filename || '未命名文档'}</strong>
        <code title={candidate.chunk_id}>{candidate.chunk_id.slice(0, 14)}</code>
      </td>
      <td><RankScore rank={candidate.bm25_rank} score={candidate.bm25_score} /></td>
      <td><RankScore rank={candidate.vector_rank} score={candidate.vector_score} /></td>
      <td><RankScore score={candidate.rrf_score} /></td>
      <td><RankScore score={candidate.rerank_score} /></td>
    </tr>
  )
}

interface RetrievalTraceProps {
  trace?: RetrievalTraceData
}

export function RetrievalTrace({ trace }: RetrievalTraceProps) {
  if (!trace) {
    return (
      <div className="trace-empty">
        <RouteIcon />
        <p>检索事件到达后，这里会展示路由、双路召回、RRF 与 Rerank 得分。</p>
      </div>
    )
  }

  const route = trace.route
  return (
    <div className="trace-content">
      <div className="route-summary">
        <div className="route-icon"><RouteIcon /></div>
        <div>
          <span className="eyebrow">QUERY ROUTE</span>
          <strong>{route?.name || 'route 未返回'}</strong>
          <small>{route?.reason || '按查询特征动态分配召回权重'}</small>
        </div>
        <div className="route-weights">
          <span>BM25 <strong>{route?.lexical_weight === undefined ? '—' : route.lexical_weight.toFixed(1)}</strong></span>
          <span>Vector <strong>{route?.vector_weight === undefined ? '—' : route.vector_weight.toFixed(1)}</strong></span>
        </div>
        <div className="route-latency">
          <span>LATENCY</span>
          <strong>{trace.latency_ms === undefined ? '—' : `${trace.latency_ms.toFixed(0)} ms`}</strong>
        </div>
      </div>

      {trace.degraded && (
        <div className="trace-degraded" role="status">
          <span>DEGRADED</span>
          <strong>检索链路已降级</strong>
          <p>
            {trace.degradation_codes.length > 0
              ? trace.degradation_codes.join(' · ')
              : '服务端未返回降级原因码'}
          </p>
        </div>
      )}

      {trace.candidates.length > 0 ? (
        <div className="trace-table-wrap">
          <table className="trace-table">
            <caption className="sr-only">检索候选的分阶段排名与得分</caption>
            <thead>
              <tr>
                <th scope="col">最终</th>
                <th scope="col">证据块</th>
                <th scope="col">BM25</th>
                <th scope="col">Vector</th>
                <th scope="col">RRF</th>
                <th scope="col">Rerank</th>
              </tr>
            </thead>
            <tbody>
              {trace.candidates.slice(0, 8).map((candidate) => (
                <CandidateRow key={candidate.chunk_id} candidate={candidate} />
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="trace-no-candidates">路由已返回，暂无候选得分明细。</p>
      )}
    </div>
  )
}
