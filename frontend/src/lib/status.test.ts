import { describe, expect, it } from 'vitest'
import { getEvaluationPresentation, normalizeAnswerStatus } from './status'

describe('normalizeAnswerStatus', () => {
  it.each([
    ['answered', 'answered'],
    ['success', 'answered'],
    ['insufficient_evidence', 'insufficient_evidence'],
    ['refused', 'insufficient_evidence'],
    ['needs_review', 'needs_review'],
    ['invalid_citation', 'needs_review'],
  ])('maps %s to %s', (input, expected) => {
    expect(normalizeAnswerStatus(input)).toBe(expected)
  })

  it('fails closed for unknown or malformed status values', () => {
    expect(normalizeAnswerStatus('unexpected')).toBe('needs_review')
    expect(normalizeAnswerStatus(undefined)).toBe('needs_review')
  })
})

describe('getEvaluationPresentation', () => {
  it('is green only for a passed verified synthetic report', () => {
    expect(getEvaluationPresentation('passed', 'VERIFIED_SYNTHETIC')).toEqual({
      tone: 'verified',
      label: 'VERIFIED_SYNTHETIC',
      detail: '仅代表公开 / 合成评测',
    })
    expect(getEvaluationPresentation('completed', 'VERIFIED_SYNTHETIC').tone).toBe(
      'not-verified',
    )
  })

  it('renders a failed report as explicit GATES FAILED', () => {
    expect(getEvaluationPresentation('failed', 'VERIFIED_SYNTHETIC')).toEqual({
      tone: 'failed',
      label: 'GATES FAILED',
      detail: '评测门禁未通过 · VERIFIED_SYNTHETIC',
    })
  })
})
