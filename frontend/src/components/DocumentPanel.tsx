import { useCallback, useEffect, useRef, useState } from 'react'
import type { ChangeEvent, DragEvent } from 'react'
import { ingestDocument, listDocuments } from '../api/client'
import type { DocumentItem } from '../types'
import { CheckIcon, FileIcon, RefreshIcon, UploadIcon, WarningIcon } from './Icons'

const MAX_FILE_SIZE = 20 * 1024 * 1024
const allowedExtensions = new Set(['pdf', 'docx', 'txt'])

function fileExtension(filename: string): string {
  return filename.split('.').pop()?.toLowerCase() ?? ''
}

function formatDate(value?: string | null): string {
  if (!value) return '时间未返回'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function statusLabel(status: string): string {
  const normalized = status.toLowerCase()
  if (['ready', 'indexed', 'completed', 'success'].includes(normalized)) return '已索引'
  if (['parsing', 'processing', 'indexing', 'pending'].includes(normalized)) return '处理中'
  if (['failed', 'error'].includes(normalized)) return '失败'
  return status || '未知'
}

interface UploadNotice {
  tone: 'success' | 'error' | 'info'
  text: string
}

export function DocumentPanel() {
  const [documents, setDocuments] = useState<DocumentItem[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [uploading, setUploading] = useState(false)
  const [dragActive, setDragActive] = useState(false)
  const [notice, setNotice] = useState<UploadNotice | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const refreshDocuments = useCallback(async () => {
    setLoading(true)
    setLoadError('')
    try {
      setDocuments(await listDocuments())
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : '无法加载文档列表')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refreshDocuments()
  }, [refreshDocuments])

  async function uploadFiles(files: File[]) {
    if (files.length === 0 || uploading) return
    const invalid = files.find(
      (file) => !allowedExtensions.has(fileExtension(file.name)) || file.size > MAX_FILE_SIZE,
    )
    if (invalid) {
      setNotice({
        tone: 'error',
        text: `${invalid.name} 不符合要求：仅支持 PDF / DOCX / TXT，单文件不超过 20 MiB。`,
      })
      return
    }

    setUploading(true)
    setNotice({ tone: 'info', text: `正在按顺序入库 ${files.length} 个文件…` })
    let completed = 0
    try {
      for (const file of files) {
        await ingestDocument(file)
        completed += 1
        setNotice({
          tone: 'info',
          text: `已提交 ${completed}/${files.length}：${file.name}`,
        })
      }
      setNotice({
        tone: 'success',
        text: `${completed} 个文件已提交；重复文件将由文档哈希幂等处理。`,
      })
      await refreshDocuments()
    } catch (error) {
      setNotice({
        tone: 'error',
        text: error instanceof Error ? error.message : '文件入库失败',
      })
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  function handleInputChange(event: ChangeEvent<HTMLInputElement>) {
    void uploadFiles(Array.from(event.target.files ?? []))
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setDragActive(false)
    void uploadFiles(Array.from(event.dataTransfer.files))
  }

  return (
    <aside className="document-panel panel" aria-labelledby="documents-heading">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">KNOWLEDGE BASE</span>
          <h2 id="documents-heading">证据库</h2>
        </div>
        <button
          className="icon-button"
          type="button"
          onClick={() => void refreshDocuments()}
          disabled={loading}
          aria-label="刷新文档列表"
          title="刷新文档列表"
        >
          <RefreshIcon className={loading ? 'spin' : ''} />
        </button>
      </div>

      <div
        className={`upload-zone${dragActive ? ' drag-active' : ''}`}
        onDragEnter={(event) => {
          event.preventDefault()
          setDragActive(true)
        }}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={() => setDragActive(false)}
        onDrop={handleDrop}
      >
        <input
          ref={fileInputRef}
          id="document-upload"
          className="visually-hidden-input"
          type="file"
          accept=".pdf,.docx,.txt,application/pdf,text/plain,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          multiple
          onChange={handleInputChange}
          disabled={uploading}
        />
        <span className="upload-icon"><UploadIcon /></span>
        <strong>{uploading ? '正在安全入库…' : '拖放试验资料'}</strong>
        <span>PDF · DOCX · TXT</span>
        <button
          className="secondary-button compact"
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={uploading}
        >
          选择文件
        </button>
        <small>单文件 ≤ 20 MiB；PDF ≤ 50 页（服务端校验）</small>
      </div>

      <div className="data-egress-notice" role="note" aria-label="DashScope 数据外发边界">
        <WarningIcon />
        <div>
          <strong>DashScope 数据外发边界</strong>
          <p>
            DashScope 模式会将分块文本与 Top 5 证据发送到外部 API；仅允许上传公开或合成数据。
          </p>
        </div>
      </div>

      {notice && (
        <div className={`upload-notice ${notice.tone}`} role="status" aria-live="polite">
          {notice.tone === 'error' ? <WarningIcon /> : <CheckIcon />}
          <span>{notice.text}</span>
        </div>
      )}

      <div className="document-list-header">
        <span>已入库文档</span>
        <strong>{loading ? '—' : documents.length}</strong>
      </div>

      <div className="document-list" aria-busy={loading}>
        {loading && (
          <div className="skeleton-stack" aria-label="正在加载文档">
            <span /><span /><span />
          </div>
        )}
        {!loading && loadError && (
          <div className="inline-error" role="alert">
            <WarningIcon />
            <div>
              <strong>文档接口不可用</strong>
              <p>{loadError}</p>
            </div>
          </div>
        )}
        {!loading && !loadError && documents.length === 0 && (
          <div className="empty-documents">
            <FileIcon />
            <p>还没有公开演示文档</p>
            <small>上传后将展示哈希身份、解析状态与索引版本。</small>
          </div>
        )}
        {!loading &&
          documents.map((document) => (
            <article className="document-item" key={document.document_id}>
              <span className={`file-type ${fileExtension(document.filename)}`}>
                {fileExtension(document.filename).toUpperCase() || 'FILE'}
              </span>
              <div className="document-copy">
                <strong title={document.filename}>{document.filename}</strong>
                <span>
                  {document.page_count ? `${document.page_count} 页` : '页数 —'}
                  <i aria-hidden="true" />
                  {document.chunk_count ? `${document.chunk_count} chunks` : 'chunks —'}
                </span>
                <small>{formatDate(document.created_at)}</small>
              </div>
              <span className={`document-status ${document.status.toLowerCase()}`}>
                {statusLabel(document.status)}
              </span>
            </article>
          ))}
      </div>

      <div className="index-footnote">
        <span>INDEX VERSION</span>
        <code>{documents.find((item) => item.index_version)?.index_version || '等待接口'}</code>
      </div>
    </aside>
  )
}
