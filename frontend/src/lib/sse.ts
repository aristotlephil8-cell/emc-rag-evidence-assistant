export const SSE_EVENT_NAMES = ['sources', 'token', 'done', 'error'] as const

export type SseEventName = (typeof SSE_EVENT_NAMES)[number]

export interface ParsedSseEvent {
  event: SseEventName
  data: unknown
}

const supportedEvents = new Set<string>(SSE_EVENT_NAMES)

function decodeData(data: string): unknown {
  if (!data) return ''

  try {
    return JSON.parse(data) as unknown
  } catch {
    return data
  }
}

function parseBlock(block: string): ParsedSseEvent | null {
  let eventName = ''
  const dataLines: string[] = []

  for (const rawLine of block.split(/\r?\n/)) {
    if (!rawLine || rawLine.startsWith(':')) continue
    const separatorIndex = rawLine.indexOf(':')
    const field = separatorIndex === -1 ? rawLine : rawLine.slice(0, separatorIndex)
    let value = separatorIndex === -1 ? '' : rawLine.slice(separatorIndex + 1)
    if (value.startsWith(' ')) value = value.slice(1)

    if (field === 'event') eventName = value
    if (field === 'data') dataLines.push(value)
  }

  if (!supportedEvents.has(eventName) || dataLines.length === 0) return null

  return {
    event: eventName as SseEventName,
    data: decodeData(dataLines.join('\n')),
  }
}

export class SseDecoder {
  private buffer = ''

  push(chunk: string): ParsedSseEvent[] {
    this.buffer += chunk
    const events: ParsedSseEvent[] = []
    let boundary = this.buffer.search(/\r?\n\r?\n/)

    while (boundary >= 0) {
      const block = this.buffer.slice(0, boundary)
      const separator = this.buffer.slice(boundary).match(/^\r?\n\r?\n/)?.[0] ?? '\n\n'
      this.buffer = this.buffer.slice(boundary + separator.length)
      const event = parseBlock(block)
      if (event) events.push(event)
      boundary = this.buffer.search(/\r?\n\r?\n/)
    }

    return events
  }

  finish(): ParsedSseEvent[] {
    const block = this.buffer.trim()
    this.buffer = ''
    if (!block) return []
    const event = parseBlock(block)
    return event ? [event] : []
  }
}

export async function readSseStream(
  response: Response,
  onEvent: (event: ParsedSseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  if (!response.ok) {
    throw new Error(`请求失败：HTTP ${response.status}`)
  }
  if (!response.body) {
    throw new Error('浏览器未收到可读取的流式响应')
  }

  const reader = response.body.getReader()
  const textDecoder = new TextDecoder()
  const sseDecoder = new SseDecoder()

  try {
    while (true) {
      if (signal?.aborted) throw new DOMException('请求已取消', 'AbortError')
      const { done, value } = await reader.read()
      if (done) break
      const text = textDecoder.decode(value, { stream: true })
      for (const event of sseDecoder.push(text)) onEvent(event)
    }

    const tail = textDecoder.decode()
    for (const event of sseDecoder.push(tail)) onEvent(event)
    for (const event of sseDecoder.finish()) onEvent(event)
  } finally {
    reader.releaseLock()
  }
}
