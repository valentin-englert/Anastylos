/** Journey model: pure navigation and capture-workflow rules.
 *
 * DOM-free on purpose: the node vitest suite covers the user journey without
 * a browser — tab order, deep-link targets, photo-role advance, and the
 * orphan-safety rule (an observation exists only once a photo is accepted).
 * Routes themselves stay owned by `shell.ts`; this module only maps them to
 * the visible journey.
 */

import type { CaptureSource, PhotoRole } from '../lib/schema.js';
import type { Repository } from '../lib/repository.js';

export type JourneyStopId = 'capture' | 'review' | 'sessions' | 'export';

export interface JourneyStop {
  id: JourneyStopId;
  label: string;
  hash: string;
}

/** Primary journey order. Secondary destinations (the BOM draft) stay
 *  reachable through deep links and contextual links, not through this bar. */
export const JOURNEY_ORDER: readonly JourneyStopId[] = ['capture', 'review', 'sessions', 'export'];

const STOP_LABELS: Record<JourneyStopId, string> = {
  capture: 'Capture',
  review: 'Review',
  sessions: 'Sessions',
  export: 'Export',
};

function staticHash(id: JourneyStopId): string {
  switch (id) {
    case 'capture':
      return '#/capture';
    case 'review':
      return '#/inspect';
    case 'sessions':
      return '#/sessions';
    case 'export':
      return '#/export';
  }
}

/** Journey stops with live targets. Review resumes the selected component
 *  when one exists, otherwise it lands on the review overview. */
export function journeyStops(observationId: string | null): JourneyStop[] {
  return JOURNEY_ORDER.map((id) => ({
    id,
    label: STOP_LABELS[id],
    hash: id === 'review' && observationId ? `#/inspect/${observationId}` : staticHash(id),
  }));
}

/** Shell route names that can be on screen. BOM is secondary: no journey stop. */
export type ScreenRoute = 'capture' | 'sessions' | 'session' | 'inspect' | 'bom' | 'export';

export function activeJourneyStop(route: ScreenRoute): JourneyStopId | null {
  switch (route) {
    case 'capture':
      return 'capture';
    case 'inspect':
      return 'review';
    case 'sessions':
    case 'session':
      return 'sessions';
    case 'export':
      return 'export';
    case 'bom':
      return null;
  }
}

export const ROLE_LABELS: Record<PhotoRole, string> = {
  front: 'Front',
  back: 'Back',
  detail: 'Detail',
  other: 'Other',
};

const ROLE_ADVANCE: Record<PhotoRole, PhotoRole> = {
  front: 'back',
  back: 'detail',
  detail: 'other',
  other: 'other',
};

/** Suggested next role after accepting a photo. Advisory only. */
export function nextRole(role: PhotoRole): PhotoRole {
  return ROLE_ADVANCE[role];
}

export interface WorkingObservation {
  observationId: string;
  /** True when this call created the observation (the caller selects it). */
  created: boolean;
}

/** Resolve the observation a new photo belongs to.
 *
 * Reuses the in-progress observation while it still exists, otherwise creates
 * exactly one observation inside the given session. The caller invokes this
 * at photo-accept time only, so no observation is ever left behind without
 * a photo. An unknown session rejects and nothing is written.
 */
export async function ensureWorkingObservationId(
  repo: Pick<Repository, 'getObservation' | 'createObservation'>,
  workingId: string | null,
  sessionId: string,
  source: CaptureSource,
): Promise<WorkingObservation> {
  if (workingId) {
    const existing = await repo.getObservation(workingId);
    if (existing) return { observationId: workingId, created: false };
  }
  const created = await repo.createObservation(sessionId, source);
  return { observationId: created.observation_id, created: true };
}
