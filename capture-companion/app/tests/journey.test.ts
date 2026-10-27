import { describe, expect, it } from 'vitest';
import { PHOTO_ROLES } from '../src/lib/schema.js';
import {
  JOURNEY_ORDER,
  ROLE_LABELS,
  activeJourneyStop,
  journeyStops,
  nextRole,
} from '../src/ui/journey.js';
import { hashToRoute, routeToHash, type Route } from '../src/ui/shell.js';

describe('journey stops', () => {
  it('follows the Capture / Review / Sessions / Export order', () => {
    expect([...JOURNEY_ORDER]).toEqual(['capture', 'review', 'sessions', 'export']);
    expect(journeyStops(null).map((stop) => stop.label)).toEqual([
      'Capture',
      'Review',
      'Sessions',
      'Export',
    ]);
  });

  it('lands Review on the overview without a selection', () => {
    const review = journeyStops(null).find((stop) => stop.id === 'review')!;
    expect(review.hash).toBe('#/inspect');
  });

  it('resumes the selected component from Review', () => {
    const id = ['12345678', '1234', '1234', '1234', '1234567890ab'].join('-');
    const review = journeyStops(id).find((stop) => stop.id === 'review')!;
    expect(review.hash).toBe(`#/inspect/${id}`);
  });

  it('maps every screen to its journey stop, BOM aside', () => {
    expect(activeJourneyStop('capture')).toBe('capture');
    expect(activeJourneyStop('inspect')).toBe('review');
    expect(activeJourneyStop('sessions')).toBe('sessions');
    expect(activeJourneyStop('session')).toBe('sessions');
    expect(activeJourneyStop('export')).toBe('export');
    expect(activeJourneyStop('bom')).toBeNull();
  });
});

describe('deep links', () => {
  const routes: Route[] = [
    { name: 'capture' },
    { name: 'sessions' },
    { name: 'session', id: 's-1' },
    { name: 'inspect', id: null },
    { name: 'inspect', id: 'o-2' },
    { name: 'bom' },
    { name: 'export' },
  ];

  it('round-trips every route through the hash', () => {
    for (const route of routes) {
      expect(hashToRoute(routeToHash(route))).toEqual(route);
    }
  });

  it('falls back to Capture on unknown hashes', () => {
    expect(hashToRoute('#/nope')).toEqual({ name: 'capture' });
    expect(hashToRoute('')).toEqual({ name: 'capture' });
    expect(hashToRoute('#/session')).toEqual({ name: 'capture' });
  });
});

describe('photo roles', () => {
  it('labels every role', () => {
    for (const role of PHOTO_ROLES) {
      expect(ROLE_LABELS[role].length).toBeGreaterThan(0);
    }
  });

  it('advances through the role sequence, then holds', () => {
    const [front, back, closeup, other] = PHOTO_ROLES;
    expect(nextRole(front!)).toBe(back);
    expect(nextRole(back!)).toBe(closeup);
    expect(nextRole(closeup!)).toBe(other);
    expect(nextRole(other!)).toBe(other);
  });
});
