import { useState } from 'react'
import { AppHeader, type AppView } from './components/AppHeader'
import { ChatPanel } from './components/ChatPanel'
import { DocumentPanel } from './components/DocumentPanel'
import { EvaluationPanel } from './components/EvaluationPanel'

export default function App() {
  const [activeView, setActiveView] = useState<AppView>('workspace')

  return (
    <div className="app-shell">
      <AppHeader activeView={activeView} onViewChange={setActiveView} />
      {activeView === 'workspace' ? (
        <main id="main-content" className="workspace-layout">
          <DocumentPanel />
          <ChatPanel />
        </main>
      ) : (
        <EvaluationPanel />
      )}
      <footer className="app-footer">
        <span>CVRAG · Public Synthetic Demo</span>
        <span>FastAPI · React · Elasticsearch · SQLite</span>
        <span>Evidence before confidence.</span>
      </footer>
    </div>
  )
}
