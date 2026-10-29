package org.anastylos.capture.sync

import java.time.Instant
import org.anastylos.capture.domain.nowUtc

/**
 * Guided-token lifecycle: reuse the cached scoped token while it is
 * safely unexpired, otherwise exchange the legacy pairing token once
 * via [GuidedControlClient.enroll] and persist the result beside the
 * pairing secrets. Transport failures propagate to the caller (which
 * owns retry); enrollment states are typed outcomes, never guesses.
 */
sealed interface GuidedTokenOutcome {
    data class Ready(val sourceId: String, val token: String) : GuidedTokenOutcome
    /** No usable token and the operator has not enrolled this device. */
    data class NeedsEnrollment(val message: String) : GuidedTokenOutcome
    /** The enrollment was spent elsewhere; only re-enroll helps. */
    data class ExchangeSpent(val message: String) : GuidedTokenOutcome
    data class Unauthorized(val message: String) : GuidedTokenOutcome
    data class Failed(val message: String) : GuidedTokenOutcome
}

class GuidedAuth(
    private val store: PairingStore,
    private val client: GuidedControlClient,
    /** Refresh when fewer than this many seconds of life remain. */
    private val refreshMarginSeconds: Long = 60,
    private val clock: () -> Instant = ::nowUtc,
) {
    suspend fun guidedToken(): GuidedTokenOutcome {
        val pairing = store.load() ?: return GuidedTokenOutcome.Failed("not paired")
        if (pairing.guidedSourceId.isNotBlank() && pairing.guidedToken.isNotBlank() &&
            !expiresSoon(pairing.guidedTokenExpiresAt)
        ) {
            return GuidedTokenOutcome.Ready(pairing.guidedSourceId, pairing.guidedToken)
        }
        return enroll(pairing)
    }

    /**
     * Unconditional re-exchange (after a 401): the cache is
     * ignored because Core just rejected it.
     */
    suspend fun refresh(): GuidedTokenOutcome {
        val pairing = store.load() ?: return GuidedTokenOutcome.Failed("not paired")
        return enroll(pairing)
    }

    private suspend fun enroll(pairing: StoredPairing): GuidedTokenOutcome =
        when (val enrolled = client.enroll(pairing.token)) {
            is EnrollOutcome.Enrolled -> {
                store.save(
                    pairing.copy(
                        guidedSourceId = enrolled.sourceId,
                        guidedToken = enrolled.token,
                        guidedTokenExpiresAt = enrolled.expiresAt,
                    ),
                )
                GuidedTokenOutcome.Ready(enrolled.sourceId, enrolled.token)
            }
            is EnrollOutcome.NotEnrolled ->
                GuidedTokenOutcome.NeedsEnrollment(enrolled.message)
            is EnrollOutcome.AlreadyExchanged ->
                GuidedTokenOutcome.ExchangeSpent(enrolled.message)
            is EnrollOutcome.Unauthorized ->
                GuidedTokenOutcome.Unauthorized(enrolled.message)
            is EnrollOutcome.Failed ->
                GuidedTokenOutcome.Failed(enrolled.message)
        }

    /** Blank or unparseable expiry refreshes; never treated as live. */
    private fun expiresSoon(expiresAt: String): Boolean {
        val expiry = try {
            if (expiresAt.isBlank()) return true
            Instant.parse(expiresAt)
        } catch (_: Exception) {
            return true
        }
        return !expiry.isAfter(clock().plusSeconds(refreshMarginSeconds))
    }
}
