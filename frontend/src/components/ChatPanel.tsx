import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { streamChat } from '../api/client'
import type { AnswerStatus, EvidenceSource, RetrievalTraceData } from '../types'
import { EvidenceCard } from './EvidenceCard'
import { LinkIcon, SendIcon, SparkIcon, StopIcon, WarningIcon } from './Icons'
import { RetrievalTrace } from './RetrievalTrace'
import { StatusBadge } from './StatusBadge'

const exampleQuestions = [
  'C1 测试开始前需要等待多久，扫频范围是多少？',
  'P1 优先级的整改顺序和处理要求是什么？',
  '请给出资料中未收录的法定认证机构名称。',
]

function fallbackTrace(sources: EvidenceSource[]): RetrievalTraceData {
  return {
    candidates: sources.map((source, index) => ({
      chunk_id: source.chunk_id,
      filename: source.filename,
      bm25_score: source.bm25_score,
      vector_score: source.vector_score,
      rrf_score: source.rrf_score,
      rerank_score: source.rerank_score ?? source.score,
      final_rank: index + 1,
    })),
    degradation_codes: [],
  }
}

export function ChatPanel() {
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState('')
  const [sources, setSources] = useState<EvidenceSource[]>([])
  const [trace, setTrace] = useState<RetrievalTraceData | undefined>()
  const [status, setStatus] = useState<AnswerStatus | null>(null)
  const [evidenceStatus, setEvidenceStatus] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [error, setError] = useState('')
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => () => abortRef.current?.abort(), [])

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const normalizedQuestion = question.trim()
    if (!normalizedQuestion || streaming) return

    const controller = new AbortController()
    abortRef.current = controller
    setAnswer('')
    setSources([])
    setTrace(undefined)
    setStatus(null)
    setEvidenceStatus('')
    setError('')
    setStreaming(true)
    let receivedDone = false

    try {
      await streamChat(
        normalizedQuestion,
        {
          onSources: (nextSources, nextTrace) => {
            setSources(nextSources)
            setTrace(nextTrace ?? fallbackTrace(nextSources))
          },
          onToken: (token) => setAnswer((current) => current + token),
          onDone: (data) => {
            receivedDone = true
            setStatus(data.status)
            setEvidenceStatus(data.evidence_status ?? '')
            if (data.answer) setAnswer(data.answer)
            if (data.trace) setTrace(data.trace)
          },
          onError: (message) => {
            setError(message)
            setStatus('needs_review')
          },
        },
        controller.signal,
      )
      if (!receivedDone && !controller.signal.aborted) {
        setStatus('needs_review')
        setError('连接已结束，但服务端未返回 done 事件。')
      }
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') return
      setStatus('needs_review')
      setError(caught instanceof Error ? caught.message : '问答请求失败')
    } finally {
      if (abortRef.current === controller) abortRef.current = null
      setStreaming(false)
    }
  }

  function stopStreaming() {
    abortRef.current?.abort()
    setStreaming(false)
  }

  const hasResult = Boolean(answer || status || error || sources.length)
  const isOfflineFake = evidenceStatus === 'IMPLEMENTED_FAKE_VERIFIED'

  return (
    <section className="chat-workspace" aria-labelledby="qa-heading">
      <div className="workspace-intro">
        <div>
          <span className="eyebrow">GROUND · VERIFY · ANSWER</span>
          <h1 id="qa-heading">让每条整改建议，<br /><em>都有证据坐标。</em></h1>
          <p>面向电磁兼容试验与整改知识的可验证增强问答系统</p>
          <span className="english-subtitle">Traceable retrieval for electromagnetic compatibility engineering.</span>
        </div>
        <div className="pipeline-mini" aria-label="检索流程">
          <span>BM25<small>Top 50</small></span>
          <i>+</i>
          <span>Vector<small>Top 50</small></span>
          <i>→</i>
          <span>RRF<small>k = 60</small></span>
          <i>→</i>
          <span>Rerank<small>Top 5</small></span>
        </div>
      </div>

      <div className="question-card panel">
        <form onSubmit={handleSubmit}>
          <label htmlFor="question-input">输入试验现象、标准条款或整改问题</label>
          <div className="question-input-wrap">
            <SparkIcon className="question-spark" />
            <textarea
              id="question-input"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="例如：C1 测试开始前需要等待多久，扫频范围是多少？"
              rows={3}
              maxLength={1000}
              disabled={streaming}
            />
            {streaming ? (
              <button className="submit-button stop" type="button" onClick={stopStreaming}>
                <StopIcon />
                <span>停止</span>
              </button>
            ) : (
              <button
                className="submit-button"
                type="submit"
                disabled={!question.trim()}
              >
                <SendIcon />
                <span>检索并回答</span>
              </button>
            )}
          </div>
          <div className="question-footer">
            <div className="example-questions" aria-label="示例问题">
              <span>试一试</span>
              {exampleQuestions.map((example, index) => (
                <button
                  type="button"
                  key={example}
                  onClick={() => setQuestion(example)}
                  disabled={streaming}
                  title={example}
                >
                  {index + 1}
                </button>
              ))}
            </div>
            <span>{question.length} / 1000</span>
          </div>
        </form>
      </div>

      <div className={`answer-panel panel${hasResult ? ' has-result' : ''}`} aria-live="polite">
        <div className="answer-heading">
          <div>
            <span className="eyebrow">
              {isOfflineFake ? 'FAKE PROVIDER OUTPUT' : 'GROUNDED RESPONSE'}
            </span>
            <h2>{isOfflineFake ? '离线证据占位回显' : '证据回答'}</h2>
          </div>
          {status && !isOfflineFake && <StatusBadge status={status} />}
          {streaming && (
            <span className="stream-indicator" role="status">
              <i /><span>正在生成</span>
            </span>
          )}
        </div>

        {isOfflineFake && (
          <div className="offline-fake-banner" role="alert">
            <WarningIcon />
            <div>
              <strong>OFFLINE_FAKE / NOT EFFECTIVENESS EVIDENCE</strong>
              <p>
                当前内容是离线 Fake Provider 对首条证据的占位回显，不是模型生成结果，
                也不构成检索、回答或引用效果证明。
              </p>
            </div>
          </div>
        )}

        {!hasResult && !streaming && (
          <div className="answer-empty">
            <span className="answer-orbit"><SparkIcon /></span>
            <strong>等待一个可检索的问题</strong>
            <p>回答只会引用当前证据库中的内容；证据不足时系统将明确拒答。</p>
          </div>
        )}

        {(answer || streaming) && (
          <div className="answer-copy">
            {answer ? <p>{answer}</p> : <p className="generating-placeholder">正在核验引用与事实声明…</p>}
            {streaming && <span className="typing-cursor" aria-hidden="true" />}
          </div>
        )}

        {status === 'insufficient_evidence' && !answer && (
          <div className="refusal-copy">
            <WarningIcon />
            <p>当前知识库没有足以支撑结论的证据。请补充对应标准、试验记录或整改文档后再试。</p>
          </div>
        )}

        {error && (
          <div className="inline-error answer-error" role="alert">
            <WarningIcon />
            <div><strong>需要复核</strong><p>{error}</p></div>
          </div>
        )}

        {sources.length > 0 && (
          <section className="evidence-section" aria-labelledby="evidence-heading">
            <div className="section-title-row">
              <div>
                <LinkIcon />
                <h3 id="evidence-heading">引用证据</h3>
              </div>
              <span>{sources.length} SOURCES</span>
            </div>
            <div className="evidence-grid">
              {sources.map((source, index) => (
                <EvidenceCard key={`${source.citation_id}-${source.chunk_id}`} source={source} index={index} />
              ))}
            </div>
          </section>
        )}
      </div>

      <details className="trace-panel panel" open={Boolean(trace)}>
        <summary>
          <span><span className="trace-node" />检索轨迹 <small>Retrieval trace</small></span>
          <span className="summary-hint">Route · BM25 · Vector · RRF · Rerank</span>
        </summary>
        <RetrievalTrace trace={trace} />
      </details>
    </section>
  )
}
