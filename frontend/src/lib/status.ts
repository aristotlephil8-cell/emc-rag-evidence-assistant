import type { AnswerStatus } from '../types'

const statusAliases: Record<string, AnswerStatus> = {
  answered: 'answered',
  answer: 'answered',
  success: 'answered',
  insufficient_evidence: 'insufficient_evidence',
  insufficient: 'insufficient_evidence',
  refused: 'insufficient_evidence',
  no_evidence: 'insufficient_evidence',
  needs_review: 'needs_review',
  review: 'needs_review',
  invalid_citation: 'needs_review',
  invalid_structure: 'needs_review',
}

export function normalizeAnswerStatus(value: unknown): AnswerStatus {
  if (typeof value !== 'string') return 'needs_review'
  return statusAliases[value.trim().toLowerCase()] ?? 'needs_review'
}

export const statusPresentation: Record<
  AnswerStatus,
  { label: string; subtitle: string }
> = {
  answered: {
    label: '证据充分',
    subtitle: 'Answered',
  },
  insufficient_evidence: {
    label: '证据不足，已拒答',
    subtitle: 'Insufficient evidence',
  },
  needs_review: {
    label: '引用异常，待复核',
    subtitle: 'Needs review',
  },
}

export interface EvaluationPresentation {
  tone: 'verified' | 'failed' | 'not-verified'
  label: string
  detail: string
}

export function getEvaluationPresentation(
  status: string | undefined,
  evidenceStatus: string | undefined,
): EvaluationPresentation {
  const normalizedStatus = status?.trim().toLowerCase() ?? ''
  const normalizedEvidence = evidenceStatus?.trim().toUpperCase() || 'NOT_VERIFIED'

  if (normalizedStatus === 'failed') {
    return {
      tone: 'failed',
      label: 'GATES FAILED',
      detail: `评测门禁未通过 · ${normalizedEvidence}`,
    }
  }
  if (
    normalizedStatus === 'passed' &&
    normalizedEvidence === 'VERIFIED_SYNTHETIC'
  ) {
    return {
      tone: 'verified',
      label: normalizedEvidence,
      detail: '仅代表公开 / 合成评测',
    }
  }
  return {
    tone: 'not-verified',
    label: normalizedEvidence,
    detail: '尚无通过门禁的可验证评测报告',
  }
}
