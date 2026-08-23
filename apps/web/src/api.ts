export type Lifecycle =
  | 'INTAKE' | 'BRIEFING' | 'SCOPING' | 'LITERATURE' | 'CANDIDATES'
  | 'WAITING_FOR_HUMAN' | 'BASELINE' | 'EXPERIMENTING' | 'ANALYZING'
  | 'WRITING' | 'REVIEW' | 'REPRODUCIBILITY' | 'PACKAGING'
  | 'READY_FOR_HUMAN_REVIEW' | 'PAUSED' | 'BLOCKED' | 'FAILED'
  | 'INCONCLUSIVE' | 'CANCELLED' | 'ARCHIVED';

export type ApiStatus = 'READY' | 'WAITING' | 'PAUSED' | 'BLOCKED' | 'INCONCLUSIVE' | 'OFFLINE';
export type EventEnvelope = { event_id: { value: string }; event_type: string; aggregate_type: string; payload: Record<string, unknown>; occurred_at: string };
export type ProjectStatus = { project: { project_id?: string; name?: string }; projection: { aggregates: Record<string, Record<string, Record<string, unknown>>> }; event_count: number };
export type BriefProposal = { question: string; scope?: string[]; constraints?: string[] };
export type ApiClient = ReturnType<typeof createApiClient>;

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) { super(message); this.name = 'ApiError'; }
}

export function createApiClient(baseUrl: string, fetchImpl: typeof fetch = fetch) {
  const base = baseUrl.replace(/\/$/, '');
  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    let response: Response;
    try { response = await fetchImpl(`${base}${path}`, { ...init, headers: { Accept: 'application/json', ...init?.headers } }); }
    catch (error) { throw new ApiError(0, error instanceof Error ? error.message : 'Network request failed'); }
    if (!response.ok) {
      let detail = response.statusText || `HTTP ${response.status}`;
      try { const body = await response.json() as { detail?: string }; detail = body.detail ?? detail; } catch { /* non-json error */ }
      throw new ApiError(response.status, detail);
    }
    return response.json() as Promise<T>;
  }
  return {
    baseUrl: base,
    health: () => request<{ status: string; service: string; loopback_default: boolean }>('/health'),
    readiness: () => request<{ status: string; providers: Array<{ status: string; provider_id?: string }> }>('/readiness'),
    capabilities: () => request<Array<Record<string, unknown>>>('/providers/capabilities'),
    createProject: (projectId: string, name?: string) => request<{ project_id: string }>('/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ project_id: projectId, name }) }),
    status: (projectId: string) => request<ProjectStatus>(`/projects/${encodeURIComponent(projectId)}/status`),
    proposeBrief: (projectId: string, brief: BriefProposal) => request<{ status: string; event: EventEnvelope }>(`/projects/${encodeURIComponent(projectId)}/briefs`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': `web-brief-${projectId}-${brief.question}` }, body: JSON.stringify(brief) }),
    approve: (projectId: string, scope: string, actorId: string) => request<{ approval: Record<string, unknown>; event: EventEnvelope }>(`/projects/${encodeURIComponent(projectId)}/approvals`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scope, actor_id: actorId }) }),
    evidence: (projectId: string) => request<{ events: number; verified_chain: boolean; evidence: EventEnvelope[] }>(`/projects/${encodeURIComponent(projectId)}/evidence/audit`),
    events: (projectId: string, after: number) => request<EventEnvelope[]>(`/projects/${encodeURIComponent(projectId)}/events?after=${after}`),
  };
}

export function lifecycleToStatus(value: unknown): ApiStatus {
  if (value === 'PAUSED') return 'PAUSED';
  if (value === 'BLOCKED' || value === 'FAILED') return 'BLOCKED';
  if (value === 'INCONCLUSIVE') return 'INCONCLUSIVE';
  if (value === 'READY_FOR_HUMAN_REVIEW') return 'READY';
  return 'WAITING';
}

export function readStreamChunk(chunk: string, after: number): { after: number; events: EventEnvelope[] } {
  const events: EventEnvelope[] = [];
  let cursor = after;
  for (const block of chunk.split(/\n\s*\n/)) {
    const id = block.match(/^id:\s*(\d+)/m)?.[1];
    const data = block.match(/^data:\s*(.+)$/m)?.[1];
    if (id && data) { try { events.push(JSON.parse(data) as EventEnvelope); cursor = Number(id); } catch { /* ignore malformed reconnect chunk */ } }
  }
  return { after: cursor, events };
}
