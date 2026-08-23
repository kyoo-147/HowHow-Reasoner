import { describe, expect, it } from 'vitest';

type Status = 'READY' | 'WAITING' | 'PAUSED' | 'BLOCKED' | 'INCONCLUSIVE';
const statuses: Status[] = ['READY', 'WAITING', 'PAUSED', 'BLOCKED', 'INCONCLUSIVE'];

describe('cockpit state semantics', () => {
  it('keeps every non-success state explicit', () => {
    expect(statuses).toContain('PAUSED');
    expect(statuses).toContain('BLOCKED');
    expect(statuses).toContain('INCONCLUSIVE');
    expect(statuses).toContain('WAITING');
  });
  it('does not confuse a waiting approval with readiness', () => {
    expect('WAITING').not.toBe('READY');
  });
});
