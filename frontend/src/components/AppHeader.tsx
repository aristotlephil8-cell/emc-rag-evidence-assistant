import { ChartIcon, DatabaseIcon, ShieldIcon, SparkIcon } from './Icons'

export type AppView = 'workspace' | 'evaluation'

interface AppHeaderProps {
  activeView: AppView
  onViewChange: (view: AppView) => void
}

export function AppHeader({ activeView, onViewChange }: AppHeaderProps) {
  return (
    <header className="app-header">
      <div className="header-main">
        <button
          className="brand"
          type="button"
          onClick={() => onViewChange('workspace')}
          aria-label="返回问答工作台"
        >
          <span className="brand-mark" aria-hidden="true">
            <ShieldIcon />
            <span className="brand-pulse" />
          </span>
          <span className="brand-copy">
            <strong>EMC_RAG</strong>
            <span>EMC Evidence Engine</span>
          </span>
        </button>

        <nav className="primary-nav" aria-label="主导航">
          <button
            type="button"
            className={activeView === 'workspace' ? 'nav-item active' : 'nav-item'}
            onClick={() => onViewChange('workspace')}
            aria-current={activeView === 'workspace' ? 'page' : undefined}
          >
            <SparkIcon />
            <span>证据问答</span>
            <small>Evidence QA</small>
          </button>
          <button
            type="button"
            className={activeView === 'evaluation' ? 'nav-item active' : 'nav-item'}
            onClick={() => onViewChange('evaluation')}
            aria-current={activeView === 'evaluation' ? 'page' : undefined}
          >
            <ChartIcon />
            <span>评测报告</span>
            <small>Evaluation</small>
          </button>
        </nav>

        <div className="header-meta" aria-label="系统信息">
          <span className="protocol-badge">
            <span className="protocol-dot" />
            Evidence-first
          </span>
          <span className="stack-label">
            <DatabaseIcon /> ES 8.11 · SQLite
          </span>
        </div>
      </div>
      <div className="signal-line" aria-hidden="true">
        <svg viewBox="0 0 1200 18" preserveAspectRatio="none">
          <path d="M0 9h220l10-6 12 12 12-9 10 3h230l7-4 9 8 12-10 10 6h260l8-5 10 10 11-7 10 2h159" />
        </svg>
      </div>
    </header>
  )
}
