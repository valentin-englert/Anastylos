package org.anastylos.capture.pairing

import java.time.Instant
import org.anastylos.capture.sync.QrPairPayload

/**
 * The two-phase QR pairing exchange with the receiver.
 *
 * Phase 1 ([redeem]) spends the scanned single-use code for a pending
 * request id. Phase 2 ([awaitApproval]) polls that request until the
 * workstation operator approves, the request lapses, or the poll
 * gives up. The phases stay separate so a retry after a transient
 * failure re-polls the live request instead of re-spending the
 * consumed code (which would wrongly report "already used").
 *
 * Every exchange runs over the SPKI pin from [QrPairPayload]; a peer
 * whose key differs is [RedeemOutcome.PinMismatch] /
 * [ApprovalOutcome.PinMismatch], never a silent fallback.
 */
interface QrPairClient {
    /**
     * Spend the scanned code for a pending approval request.
     * Exactly one redeem attempt; the caller decides about retries.
     */
    suspend fun redeem(payload: QrPairPayload, deviceLabel: String): RedeemOutcome

    /**
     * Poll [request] until it resolves. Returns when the operator
     * approves or the request lapses (rejected, expired, or gone —
     * the receiver answers all three identically, so the phone
     * cannot tell them apart), or when repeated transport failures
     * make further polling pointless. Cancellation aborts the poll.
     */
    suspend fun awaitApproval(payload: QrPairPayload, request: PendingRequest): ApprovalOutcome

    /**
     * Confirm an approved token with an authenticated status request.
     * Runs over the same SPKI pin; safe to retry freely (no code is
     * spent). The caller saves the token only after [VerifyOutcome.Verified].
     */
    suspend fun verify(payload: QrPairPayload, token: String): VerifyOutcome
}

/**
 * A live approval request: the poll capability the redeem call earned.
 *
 * @param requestId 128-bit server id; bearer for the poll endpoint only.
 * @param expiresAt server-stated lapse time; polling past it is pointless.
 */
data class PendingRequest(val requestId: String, val expiresAt: Instant)

sealed interface RedeemOutcome {
    data class Pending(val request: PendingRequest) : RedeemOutcome

    /** Code unknown, consumed, or expired; a fresh QR is the only way on. */
    data class CodeInvalid(val message: String) : RedeemOutcome

    data class RateLimited(val retryAfterSeconds: Long?) : RedeemOutcome

    /** Peer key differs from the scanned pin. Never retry silently. */
    data class PinMismatch(val message: String) : RedeemOutcome

    /** Transport failure before any answer; the code may be unspent. */
    data class NetworkError(val message: String) : RedeemOutcome

    data class ServerError(val message: String) : RedeemOutcome
}

sealed interface ApprovalOutcome {
    /** Operator approved. [workstationId] is the server's authoritative echo. */
    data class Approved(val token: String, val workstationId: String) : ApprovalOutcome

    /**
     * Not approved: rejected by the operator, lapsed unapproved, or
     * unknown to the server. Deliberately one outcome — the receiver
     * answers all three with the same status, so distinguishing them
     * would be guessing.
     */
    data class NotApproved(val message: String) : ApprovalOutcome

    data class PinMismatch(val message: String) : ApprovalOutcome

    /** Polling failed too often to continue; the request may still be live. */
    data class NetworkError(val message: String) : ApprovalOutcome

    data class ServerError(val message: String) : ApprovalOutcome
}

sealed interface VerifyOutcome {
    /** The server accepted the token; [workstationId] is its echo. */
    data class Verified(val workstationId: String) : VerifyOutcome

    /** Issued but rejected now (e.g. revoked between approval and verify). Fail closed. */
    data object Unauthorized : VerifyOutcome

    data class PinMismatch(val message: String) : VerifyOutcome

    /** Transport failed after approval; the token is likely fine — retry. */
    data class NetworkError(val message: String) : VerifyOutcome

    data class ServerError(val message: String) : VerifyOutcome
}
