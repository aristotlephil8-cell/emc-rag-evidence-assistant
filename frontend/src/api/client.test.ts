import { afterEach, describe, expect, it, vi } from 'vitest'
import { getLatestEvaluation, searchRetrieval, streamChat } from './client'

describe('streamChat request contract', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('posts the backend-locked query field and consumes done', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        'event: done\ndata: {"status":"answered","evidence_status":"implemented_fake_verified"}\n\n',
        {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)
    const onDone = vi.fn()

    await streamChat('如何定位 CE102 超标路径？', {
      onSources: vi.fn(),
      onToken: vi.fn(),
      onDone,
      onError: vi.fn(),
    })

    expect(fetchMock).toHaveBeenCalledOnce()
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(JSON.parse(String(init.body))).toEqual({
      query: '如何定位 CE102 超标路径？',
    })
    expect(onDone).toHaveBeenCalledWith({
      status: 'answered',
      evidence_status: 'IMPLEMENTED_FAKE_VERIFIED',
    })
  })

  it('flattens the real nested sources event payload', async () => {
    const sourceEvent = {
      sources: [
        {
          citation_id: 'C1',
          hit: {
            chunk_id: 'chunk-001',
            text: '屏蔽层应优先采用 360° 端接。',
            score: 0.93,
            scores: {
              bm25: 7.2,
              vector: 0.81,
              rrf: 0.028,
              rerank: 0.93,
            },
            source: {
              document_id: 'doc-001',
              filename: '屏蔽整改指南.pdf',
              page_number: 8,
              section_path: ['连接器', '屏蔽端接'],
              bbox: [100, 220, 900, 420],
              table_row: 3,
            },
          },
        },
      ],
      retrieval_trace: {
        route: {
          route_name: 'identifier_heavy',
          weights: { bm25: 0.7, vector: 0.3 },
        },
        candidates: [
          {
            rank: 1,
            ranks: { bm25: 2, vector: 5, final: 1 },
            hit: {
              chunk_id: 'chunk-001',
              score: 0.93,
              scores: {
                bm25: 7.2,
                vector: 0.81,
                rrf: 0.028,
                rerank: 0.93,
              },
              source: { filename: '屏蔽整改指南.pdf' },
            },
          },
        ],
        latency_ms: 118,
        rerank_applied: false,
        degraded: true,
        degradation_codes: ['RERANK_PROVIDER_UNAVAILABLE'],
      },
    }
    const body =
      `event: sources\ndata: ${JSON.stringify(sourceEvent)}\n\n` +
      'event: done\ndata: {"status":"answered"}\n\n'
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(body, {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        }),
      ),
    )
    const onSources = vi.fn()

    await streamChat('如何端接屏蔽层？', {
      onSources,
      onToken: vi.fn(),
      onDone: vi.fn(),
      onError: vi.fn(),
    })

    expect(onSources).toHaveBeenCalledOnce()
    expect(onSources.mock.calls[0][0]).toEqual([
      expect.objectContaining({
        citation_id: 'C1',
        chunk_id: 'chunk-001',
        document_id: 'doc-001',
        filename: '屏蔽整改指南.pdf',
        page_number: 8,
        section_path: ['连接器', '屏蔽端接'],
        bbox: [100, 220, 900, 420],
        table_row: 3,
        snippet: '屏蔽层应优先采用 360° 端接。',
        score: 0.93,
        bm25_score: 7.2,
        vector_score: 0.81,
        rrf_score: 0.028,
        rerank_score: 0.93,
      }),
    ])
    expect(onSources.mock.calls[0][1]).toEqual({
      route: {
        name: 'identifier_heavy',
        lexical_weight: 0.7,
        vector_weight: 0.3,
      },
      candidates: [
        {
          chunk_id: 'chunk-001',
          filename: '屏蔽整改指南.pdf',
          bm25_rank: 2,
          bm25_score: 7.2,
          vector_rank: 5,
          vector_score: 0.81,
          rrf_score: 0.028,
          rerank_score: 0.93,
          final_rank: 1,
        },
      ],
      latency_ms: 118,
      rerank_applied: false,
      degraded: true,
      degradation_codes: ['RERANK_PROVIDER_UNAVAILABLE'],
    })
  })

  it('normalizes search hits and derives a visible retrieval trace', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            route: {
              name: 'identifier_route',
              lexical_weight: 0.7,
              vector_weight: 0.3,
            },
            latency_ms: 42,
            hits: [
              {
                chunk_id: 'chunk-search-1',
                text: 'RE102 限值条款证据。',
                score: 0.88,
                scores: { bm25_score: 6.4, vector_score: 0.76, rrf_score: 0.024 },
                source: {
                  document_id: 'doc-search-1',
                  filename: 'RE102 条款.txt',
                  page_number: 2,
                  section_path: 'RE102 / 限值',
                },
              },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    )

    const result = await searchRetrieval('RE102')

    expect(result.sources[0]).toEqual(
      expect.objectContaining({
        chunk_id: 'chunk-search-1',
        filename: 'RE102 条款.txt',
        snippet: 'RE102 限值条款证据。',
        bm25_score: 6.4,
        vector_score: 0.76,
        rrf_score: 0.024,
      }),
    )
    expect(result.trace).toEqual(
      expect.objectContaining({
        route: {
          name: 'identifier_route',
          lexical_weight: 0.7,
          vector_weight: 0.3,
        },
        latency_ms: 42,
        candidates: [
          expect.objectContaining({
            chunk_id: 'chunk-search-1',
            final_rank: 1,
          }),
        ],
      }),
    )
  })

  it('adapts nested evaluation variants, gates, badcases, and limitations', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            status: 'passed',
            evidence_status: 'VERIFIED_SYNTHETIC',
            generated_at: '2026-08-20T07:00:00Z',
            corpus_hash: 'sha256:public-corpus',
            metrics: {
              parser_success_rate: 0.95,
              refusal_threshold: {
                selected_on: 'dev',
                by_variant: {
                  routed_hybrid_rrf_rerank: { value: 0.62 },
                },
              },
              variants: {
                BM25: {
                  dev: { hit_at_5: 0.7 },
                  test: { hit_at_5: 0.75, ndcg_at_5: 0.61, latency_p95_ms: 90 },
                  all: { hit_at_5: 0.72 },
                },
                'Routed Hybrid RRF + Rerank': {
                  dev: { hit_at_5: 0.9 },
                  test: { hit_at_5: 0.94, ndcg_at_5: 0.83, latency_p95_ms: 155 },
                  all: { hit_at_5: 0.92 },
                },
              },
              gates: {
                recall_not_worse: { passed: true, detail: 'final >= BM25' },
                ndcg_improved: false,
              },
            },
            badcases: {
              Parser: [{ id: 'P-01', summary: '扫描页定位缺失' }],
              Rerank: [
                {
                  case_id: 'R-02',
                  missing_evidence: ['EV-7'],
                  missing_after_rerank: true,
                },
              ],
            },
            limitations: ['仅覆盖公开合成语料', { description: '不代表生产流量' }],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    )

    const report = await getLatestEvaluation()

    expect(report).toEqual(
      expect.objectContaining({
        status: 'passed',
        evidence_status: 'VERIFIED_SYNTHETIC',
        corpus_hash: 'sha256:public-corpus',
        parser_pass_rate: 0.95,
        refusal_threshold: 0.62,
        limitations: ['仅覆盖公开合成语料', '不代表生产流量'],
      }),
    )
    expect(report.variants[0]).toEqual(
      expect.objectContaining({
        name: 'BM25',
        metrics: expect.objectContaining({ hit_at_5: 0.75, ndcg_at_5: 0.61 }),
        latency_p95_ms: 90,
      }),
    )
    expect(report.gates).toEqual([
      { name: 'recall_not_worse', passed: true, detail: 'final >= BM25' },
      { name: 'ndcg_improved', passed: false },
    ])
    expect(report.badcases[0]).toEqual(
      expect.objectContaining({ id: 'P-01', category: 'Parser' }),
    )
    expect(report.badcases[1]).toEqual({
      id: 'R-02',
      category: 'Rerank',
      summary: '缺失证据：EV-7；关键证据在 Rerank 后缺失',
      question: undefined,
      split: undefined,
    })
  })

  it('fails closed to NOT_VERIFIED when an evaluation has not run', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            status: 'not_run',
            evidence_status: 'VERIFIED_SYNTHETIC',
            metrics: { variants: {}, gates: {} },
            badcases: [],
            limitations: [],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    )

    const report = await getLatestEvaluation()

    expect(report.evidence_status).toBe('NOT_VERIFIED')
    expect(report.variants).toEqual([])
  })
})
