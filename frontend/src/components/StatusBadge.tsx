import { statusPresentation } from '../lib/status'
import type { AnswerStatus } from '../types'

interface StatusBadgeProps {
  status: AnswerStatus
}

export function StatusBadge({ status }: StatusBadgeProps) {
  const content = statusPresentation[status]

  return (
    <span className={`answer-status ${status}`} role="status">
      <span className="status-indicator" aria-hidden="true" />
      <span>
        <strong>{content.label}</strong>
        <small>{content.subtitle}</small>
      </span>
    </span>
  )
}
