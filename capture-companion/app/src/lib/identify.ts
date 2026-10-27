/** Provider-neutral identification interface (future functionality).
 *
 * No provider is implemented in this iteration and no network call is made.
 * When providers exist, they implement {@link IdentificationProvider},
 * return `suggested` candidates only, and the operator promotes nothing
 * automatically: review stays explicit, exactly as the core memory requires.
 */

import type { IdentificationCandidate } from './schema.js';

export const IDENTIFICATION_STATUS = 'not-implemented' as const;

export interface IdentificationImageRef {
  image_id: string;
  sha256: string;
  role: string;
}

export interface IdentificationRequest {
  observation_id: string;
  images: IdentificationImageRef[];
  operator_context: {
    notes: string | null;
    markings: string[];
    category_hint: string | null;
  } | null;
}

export interface IdentificationResult {
  provider: string;
  model: string;
  requested_at: string;
  candidates: IdentificationCandidate[];
  provenance: {
    provider_version: string | null;
    request_sha256: string | null;
  };
  review_status: 'pending-review';
}

export interface IdentificationProvider {
  readonly providerId: string;
  readonly modelId: string;
  identify(request: IdentificationRequest): Promise<IdentificationResult>;
}

/** Registered providers. Empty until a real provider ships. */
const providers = new Map<string, IdentificationProvider>();

export function registerProvider(provider: IdentificationProvider): void {
  providers.set(provider.providerId, provider);
}

export function listProviders(): IdentificationProvider[] {
  return [...providers.values()];
}

export function describeIdentificationStatus(): string {
  return 'Automatic identification is not implemented in this version. Candidates will appear here for operator review once a provider is connected.';
}
