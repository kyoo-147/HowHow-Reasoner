import { describe, expect, it, vi } from 'vitest';
import { createApiClient, lifecycleToStatus, readStreamChunk } from './api';

describe('control-plane client', () => {
  it('uses a configurable base URL and typed endpoints', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'ok' }), { status: 200 }));
    const client = createApiClient('http://localhost:8000/', fetchImpl);
    await client.health();
    expect(fetchImpl).toHaveBeenCalledWith('http://localhost:8000/health', expect.anything());
  });
  it('surfaces API failures instead of fabricating state', async () => {
    const client = createApiClient('http://api', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'rejected' }), { status: 403 })));
    await expect(client.approve('p', 'direction', 'actor')).rejects.toMatchObject({ status: 403, message: 'rejected' });
  });
  it('classifies an offline fetch as a transport error', async () => {
    const client = createApiClient('http://api', vi.fn().mockRejectedValue(new Error('offline')));
    await expect(client.health()).rejects.toMatchObject({ status: 0, message: 'offline' });
  });
  it('resumes an event stream from its cursor and ignores malformed frames', () => {
    const result = readStreamChunk('id: 4\ndata: {"event_id":{"value":"e1"}}\n\nid: 5\ndata: not-json\n', 3);
    expect(result.after).toBe(4);
    expect(result.events).toHaveLength(1);
  });
  it('keeps explicit lifecycle semantics', () => {
    expect(lifecycleToStatus('READY_FOR_HUMAN_REVIEW')).toBe('READY');
    expect(lifecycleToStatus('PAUSED')).toBe('PAUSED');
    expect(lifecycleToStatus('INCONCLUSIVE')).toBe('INCONCLUSIVE');
  });
});
