import { describe, expect, it } from 'vitest'
import { SseDecoder } from './sse'

describe('SseDecoder', () => {
  it('parses events split across transport chunks', () => {
    const decoder = new SseDecoder()

    expect(decoder.push('event: sources\ndata: {"sources":[')).toEqual([])
    expect(
      decoder.push('{"citation_id":"C1"}]}\n\nevent: token\ndata: "电磁'),
    ).toEqual([
      {
        event: 'sources',
        data: { sources: [{ citation_id: 'C1' }] },
      },
    ])
    expect(decoder.push('兼容"\n\n')).toEqual([
      { event: 'token', data: '电磁兼容' },
    ])
  })

  it('supports CRLF and multiline data while ignoring unsupported events', () => {
    const decoder = new SseDecoder()
    const events = decoder.push(
      'event: heartbeat\r\ndata: ok\r\n\r\n' +
        'event: error\r\ndata: first\r\ndata: second\r\n\r\n',
    )

    expect(events).toEqual([{ event: 'error', data: 'first\nsecond' }])
  })

  it('flushes a final event without a trailing separator', () => {
    const decoder = new SseDecoder()
    decoder.push('event: done\ndata: {"status":"answered"}')
    expect(decoder.finish()).toEqual([
      { event: 'done', data: { status: 'answered' } },
    ])
  })
})
