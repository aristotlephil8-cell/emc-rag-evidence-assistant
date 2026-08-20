import { useCallback, useEffect, useMemo, useState } from 'react'
import { getLatestEvaluation } from '../api/client'
import { getEvaluationPresentation } from '../lib/status'
import type {
  EvaluationBadcase,
  EvaluationReport,
  EvaluationVariant,
} from '../types'
import { ChartIcon, CheckIcon, RefreshIcon, ShieldIcon, WarningIcon } from './Icons'

const experimentVariants = [
  { key: 'bm25', name: 'BM25', subtitle: 'Lexical baseline' },
  { key: 'vector', name: 'Vector', subtitle: 'Semantic baseline' },
  { key: 'hybridrrf', name: 'Hybrid RRF', subtitle: 'Dual retrieval' },
  { key: 'hybridrrfrerank', name: 'Hybrid RRF + Rerank', subtitle: 'Fixed weights' },
  {
    key: 'routedhybridrrfrerank',
    name: 'Routed Hybrid RRF + Rerank',
    subtitle: 'Final pipeline',
  },
] as const

const metricRows = [
  { label: 'Hit@5', aliases: ['hit_at_5', 'hit5', 'hit@5'], type: 'percent' },
  {
    label: 'Evidence Recall@5',
    aliases: ['evidence_recall_at_5', 'recall_at_5', 'recall5', 'evidence_recall@5'],
    type: 'percent',
  },
  { label: 'MRR@10', aliases: ['mrr_at_10', 'mrr10', 'mrr@10'], type: 'decimal' },
  { label: 'nDCG@5', aliases: ['ndcg_at_5', 'ndcg5', 'ndcg@5'], type: 'decimal' },
  {
    label: 'Context Precision@5',
    aliases: ['context_precision_at_5', 'context_precision5', 'context_precision@5'],
    type: 'percent',
  },
  {
    label: '引用 ID 有效率',
    aliases: ['citation_id_validity', 'citation_id_valid_rate', 'valid_citation_id_rate'],
    type: 'percent',
  },
  {
    label: 'Citation Precision',
    aliases: ['citation_precision'],
    type: 'percent',
  },
  {
    label: 'Citation Recall',
    aliases: ['citation_recall'],
    type: 'percent',
  },
  {
    label: '拒答准确率',
    aliases: ['refusal_accuracy', 'abstention_accuracy'],
    type: 'percent',
  },
] as const

function normalizedKey(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9\u4e00-\u9fff]/g, '')
}

function findVariant(
  variants: EvaluationVariant[],
  key: string,
): EvaluationVariant | undefined {
  return variants.find((variant) => normalizedKey(variant.name) === key)
}

function metricValue(
  variant: EvaluationVariant | undefined,
  aliases: readonly string[],
): number | null | undefined {
  if (!variant) return undefined
  const entries = Object.entries(variant.metrics)
  for (const alias of aliases) {
    const target = normalizedKey(alias)
    const match = entries.find(([key]) => normalizedKey(key) === target)
    if (match) return match[1]
  }
  return undefined
}

function formatMetric(
  value: number | null | undefined,
  type: 'percent' | 'decimal',
): string {
  if (value === null || value === undefined) return '—'
  if (type === 'percent') {
    const percentage = Math.abs(value) <= 1 ? value * 100 : value
    return `${percentage.toFixed(1)}%`
  }
  return value.toFixed(3)
}

function formatTimestamp(value?: string | null): string {
  if (!value) return '尚未返回'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

const emptyBadcaseCategories = ['Parser', '召回池', 'Rerank', '引用']

export function EvaluationPanel() {
  const [report, setReport] = useState<EvaluationReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const loadReport = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setReport(await getLatestEvaluation())
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '无法加载评测报告')
      setReport(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadReport()
  }, [loadReport])

  const variants = useMemo(
    () =>
      experimentVariants.map((definition) => ({
        ...definition,
        data: findVariant(report?.variants ?? [], definition.key),
      })),
    [report],
  )
  const displayedBadcases: EvaluationBadcase[] = report?.badcases.length
    ? report.badcases.slice(0, 8)
    : emptyBadcaseCategories.map((category) => ({
        id: `empty-${category}`,
        category,
        summary: '最新报告尚未返回该类具体样例。',
      }))
  const evidenceStatus = report?.evidence_status || 'NOT_VERIFIED'
  const evaluationPresentation = getEvaluationPresentation(
    report?.status,
    evidenceStatus,
  )

  return (
    <main id="main-content" className="evaluation-page">
      <section className="evaluation-hero">
        <div>
          <span className="eyebrow">REPRODUCIBLE EVALUATION</span>
          <h1>消融实验与证据边界</h1>
          <p>固定数据、固定 Gold、开发集选阈值、冻结测试集只运行一次。</p>
        </div>
        <div className={`verified-badge ${evaluationPresentation.tone}`}>
          <ShieldIcon />
          <span>
            <strong>{evaluationPresentation.label}</strong>
            <small>{evaluationPresentation.detail}</small>
          </span>
        </div>
      </section>

      <section className="evaluation-summary" aria-label="评测协议摘要">
        <article>
          <span>DOCUMENTS</span>
          <strong>16</strong>
          <small>6 PDF · 5 DOCX · 5 TXT</small>
        </article>
        <article>
          <span>QUESTIONS</span>
          <strong>40</strong>
          <small>32 answerable · 8 refusal</small>
        </article>
        <article>
          <span>FROZEN SPLIT</span>
          <strong>24 / 16</strong>
          <small>dev / frozen test</small>
        </article>
        <article>
          <span>PARSER PASS</span>
          <strong>
            {report?.parser_pass_rate === null || report?.parser_pass_rate === undefined
              ? '—'
              : `${(report.parser_pass_rate <= 1
                  ? report.parser_pass_rate * 100
                  : report.parser_pass_rate
                ).toFixed(1)}%`}
          </strong>
          <small>latest verified run</small>
        </article>
      </section>

      <section className="metric-panel panel" aria-labelledby="metrics-heading">
        <div className="panel-heading evaluation-heading">
          <div>
            <span className="eyebrow">ABLATION MATRIX</span>
            <h2 id="metrics-heading">五组检索方案</h2>
          </div>
          <div className="report-meta">
            <span>语料哈希 <code>{report?.corpus_hash || report?.dataset_version || '—'}</code></span>
            <span>
              拒答阈值
              <code>
                {report?.refusal_threshold === null || report?.refusal_threshold === undefined
                  ? '—'
                  : report.refusal_threshold.toFixed(3)}
              </code>
            </span>
            <span>生成时间 <strong>{formatTimestamp(report?.generated_at)}</strong></span>
            <button
              className="icon-button"
              type="button"
              onClick={() => void loadReport()}
              disabled={loading}
              aria-label="刷新评测报告"
            >
              <RefreshIcon className={loading ? 'spin' : ''} />
            </button>
          </div>
        </div>

        {error && (
          <div className="evaluation-alert" role="alert">
            <WarningIcon />
            <div>
              <strong>评测接口暂不可用</strong>
              <p>{error}。表格不会使用演示数值填充。</p>
            </div>
          </div>
        )}

        <div className="metric-table-wrap" aria-busy={loading}>
          <table className="metric-table">
            <caption className="sr-only">五组检索消融方案的冻结测试集指标</caption>
            <thead>
              <tr>
                <th scope="col">指标</th>
                {variants.map((variant, index) => (
                  <th
                    scope="col"
                    key={variant.key}
                    className={index === variants.length - 1 ? 'final-variant' : ''}
                  >
                    {index === variants.length - 1 && <span className="final-tag">FINAL</span>}
                    <strong>{variant.name}</strong>
                    <small>{variant.subtitle}</small>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {metricRows.map((metric) => (
                <tr key={metric.label}>
                  <th scope="row">{metric.label}</th>
                  {variants.map((variant, index) => (
                    <td
                      key={variant.key}
                      className={index === variants.length - 1 ? 'final-variant' : ''}
                    >
                      {loading ? (
                        <span className="metric-loading" />
                      ) : (
                        formatMetric(metricValue(variant.data, metric.aliases), metric.type)
                      )}
                    </td>
                  ))}
                </tr>
              ))}
              <tr className="latency-row">
                <th scope="row">Latency P50 / P95</th>
                {variants.map((variant, index) => (
                  <td
                    key={variant.key}
                    className={index === variants.length - 1 ? 'final-variant' : ''}
                  >
                    {variant.data?.latency_p50_ms === null ||
                    variant.data?.latency_p50_ms === undefined
                      ? '—'
                      : `${variant.data.latency_p50_ms.toFixed(0)} / ${
                          variant.data.latency_p95_ms?.toFixed(0) ?? '—'
                        } ms`}
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
        <p className="metric-note">
          “—” 表示最新报告未返回该指标，不代表 0；本页不会把设计目标渲染成实测结果。
        </p>
      </section>

      <div className="evaluation-lower-grid">
        <section className="gate-panel panel" aria-labelledby="gates-heading">
          <div className="section-title-row">
            <div><ChartIcon /><h2 id="gates-heading">效果门禁</h2></div>
            <span>FROZEN TEST</span>
          </div>
          {report?.gates.length ? (
            <div className="gate-list">
              {report.gates.map((gate) => (
                <article key={gate.name} className={gate.passed === true ? 'passed' : gate.passed === false ? 'failed' : 'unknown'}>
                  {gate.passed === true ? <CheckIcon /> : <WarningIcon />}
                  <div><strong>{gate.name}</strong>{gate.detail && <p>{gate.detail}</p>}</div>
                  <span>{gate.passed === true ? 'PASS' : gate.passed === false ? 'FAIL' : 'N/A'}</span>
                </article>
              ))}
            </div>
          ) : (
            <div className="gate-empty">
              <ChartIcon />
              <p>等待评测报告返回门禁结果；未通过时不生成“效果提升”表述。</p>
            </div>
          )}
        </section>

        <section className="badcase-panel panel" aria-labelledby="badcases-heading">
          <div className="section-title-row">
            <div><WarningIcon /><h2 id="badcases-heading">Badcase 归因</h2></div>
            <span>{report?.badcases.length ?? 0} CASES</span>
          </div>
          <div className="badcase-grid">
            {displayedBadcases.map((badcase) => (
              <article key={badcase.id}>
                <span>{badcase.category}</span>
                {badcase.question && <strong>{badcase.question}</strong>}
                <p>{badcase.summary}</p>
                {badcase.split && <small>{badcase.split}</small>}
              </article>
            ))}
          </div>
        </section>
      </div>

      <section className="boundary-panel panel" aria-labelledby="boundary-heading">
        <div className="boundary-title">
          <ShieldIcon />
          <div><span className="eyebrow">EVIDENCE BOUNDARY</span><h2 id="boundary-heading">可公开陈述的边界</h2></div>
        </div>
        <div className="boundary-grid">
          <article>
            <span className="boundary-number">01</span>
            <strong>只证明本仓库当前能力</strong>
            <p>代码、测试与报告只对应公开 Demo，不反向证明 2023–2024 年历史职责。</p>
          </article>
          <article>
            <span className="boundary-number">02</span>
            <strong>只使用公开 / 合成材料</strong>
            <p>仓库不包含个人简历、联系方式、内部试验数据、私有模型资产或凭据。</p>
          </article>
          <article>
            <span className="boundary-number">03</span>
            <strong>不等同于生产效果</strong>
            <p>合成集指标不代表真实企业流量、12 人试用、3,200 请求或生产成功率。</p>
          </article>
          <article>
            <span className="boundary-number">04</span>
            <strong>失败结果同样保留</strong>
            <p>冻结测试集不用于继续调参；门禁失败时公开真实指标与四类 Badcase。</p>
          </article>
        </div>
      </section>

      {report && report.limitations.length > 0 && (
        <section className="report-limitations panel" aria-labelledby="limitations-heading">
          <div>
            <WarningIcon />
            <h2 id="limitations-heading">本次评测限制</h2>
            <span>LIMITATIONS</span>
          </div>
          <ul>
            {report.limitations.map((limitation) => (
              <li key={limitation}>{limitation}</li>
            ))}
          </ul>
        </section>
      )}
    </main>
  )
}
