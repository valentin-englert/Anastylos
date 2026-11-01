package org.anastylos.capture.sync

import java.time.Instant
import java.util.UUID
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.anastylos.capture.data.GuidedAssociationRepository
import org.anastylos.capture.data.GuidedPrefs
import org.anastylos.capture.domain.nowUtc
import org.anastylos.capture.domain.toIsoUtc
import org.anastylos.capture.guidance.DeliveryState
import org.anastylos.capture.guidance.ReceiptKind
import org.anastylos.capture.guidance.RequestState
import org.anastylos.capture.guidance.SessionState

/**
 * Remote guided-session state mirrored from Core: the session's
 * mapped state, the latest revision of every known request
 * (unmapped spellings are skipped, never guessed), observed
 * receipts, and the poll cursor.
 */
data class RemoteSessionState(
    val attached: Boolean = false,
    val sessionId: String? = null,
    val session: SessionState? = null,
    val sessionRevision: Int = 0,
    val consentRevision: Int = 0,
    val grantId: String? = null,
    val sessionExpiresAt: String = "",
    val activeRequestId: String? = null,
    val requests: Map<String, GuidedViewRequest> = emptyMap(),
    val receipts: List<GuidedReceipt> = emptyList(),
    val cursor: String = "0",
    val lastError: String? = null,
    val authLost: Boolean = false,
    val sessionGone: Boolean = false,
)

sealed interface RemoteConsentOutcome {
    data class Applied(val session: SessionState, val revision: Int, val grantId: String?) :
        RemoteConsentOutcome
    data class Conflict(val currentRevision: Int?) : RemoteConsentOutcome
    data class NeedsEnrollment(val message: String) : RemoteConsentOutcome
    data class Failed(val message: String) : RemoteConsentOutcome
}

sealed interface RemoteEventOutcome {
    data class Sent(val eventSeq: Long, val sessionRevision: Int) : RemoteEventOutcome
    data class Failed(val message: String) : RemoteEventOutcome
}

sealed interface RemoteAttemptOutcome {
    data class Reserved(
        val attemptId: String,
        val attemptNumber: Int,
        val remainingExposures: Int,
        val status: String,
    ) : RemoteAttemptOutcome
    data class Conflict(val code: String, val currentRevision: Int?) : RemoteAttemptOutcome
    data class Expired(val message: String) : RemoteAttemptOutcome
    data class Failed(val message: String) : RemoteAttemptOutcome
}

/**
 * Source-role session driver: polls `changes`, applies revisions /
 * receipts to [RemoteSessionState] and the association store, and
 * posts consent, source events, device status, and attempt
 * reservations. One exchange per call; the poll loop owns backoff.
 * All id-bearing inputs are caller-generated so retries replay
 * identical bytes against the server's idempotency keys.
 */
interface GuidedRemoteSession {
    val state: StateFlow<RemoteSessionState>
    /** Binds state to a session; starts nothing. */
    fun attach(sessionId: String)
    /** Starts the changes poll loop (idempotent; needs attach first). */
    fun startPolling()
    /** Stops the poll loop, keeping the bound state. */
    fun stopPolling()
    fun detach()
    fun close()
    suspend fun arm(cameraId: String, maxImages: Int, expiresAt: String, allowAutoUpload: Boolean): RemoteConsentOutcome
    suspend fun disarm(cameraId: String, maxImages: Int, expiresAt: String): RemoteConsentOutcome
    suspend fun postEvent(event: GuidedSourceEvent): RemoteEventOutcome
    suspend fun postStatus(status: GuidedDeviceStatus): RemoteEventOutcome
    suspend fun reserveAttempt(input: GuidedAttemptInput): RemoteAttemptOutcome
    /** What this source can fulfil, so Core can validate requests against it. */
    suspend fun publishCapabilities(caps: GuidedCapabilities): RemoteEventOutcome
    suspend fun resolveToken(): GuidedTokenOutcome
    /** Builds a source event with fresh ids for a new occurrence. */
    fun buildEvent(
        kind: String,
        requestId: String?,
        requestRevision: Int?,
        attemptId: String? = null,
        reason: String? = null,
    ): GuidedSourceEvent?
}

class GuidedSessionController(
    private val pairingStore: PairingStore,
    transport: HttpTransport,
    private val associations: GuidedAssociationRepository,
    private val prefs: GuidedPrefs,
    private val ioDispatcher: kotlinx.coroutines.CoroutineDispatcher = Dispatchers.IO,
    private val pollIntervalMillis: Long = 2_000,
    private val maxBackoffMillis: Long = 30_000,
    private val uuid: () -> String = { UUID.randomUUID().toString() },
    private val utcClock: () -> Instant = ::nowUtc,
    private val nanoClock: () -> Long = { System.nanoTime() },
    private val sleeper: suspend (Long) -> Unit = { delay(it) },
) : GuidedRemoteSession {
    private val client = GuidedControlClient(transport, ioDispatcher)
    private val auth = GuidedAuth(pairingStore, client, clock = utcClock)
    private val scope = CoroutineScope(SupervisorJob() + ioDispatcher)
    private val _state = MutableStateFlow(RemoteSessionState())
    override val state: StateFlow<RemoteSessionState> = _state.asStateFlow()
    private var pollJob: Job? = null

    override fun attach(sessionId: String) {
        require(sessionId.isNotBlank()) { "session id must not be blank" }
        detach()
        _state.value = RemoteSessionState(attached = true, sessionId = sessionId)
    }

    override fun startPolling() {
        val sessionId = _state.value.sessionId ?: return
        if (pollJob?.isActive == true) return
        pollJob = scope.launch {
            val cursor = try {
                withContext(ioDispatcher) { prefs.changeCursor(sessionId).first() }
            } catch (_: Exception) {
                "0"
            }
            _state.update { it.copy(cursor = cursor.ifBlank { "0" }) }
            pollLoop(sessionId)
        }
    }

    override fun stopPolling() {
        pollJob?.cancel()
        pollJob = null
    }

    override fun detach() {
        stopPolling()
        if (_state.value.attached) {
            _state.value = RemoteSessionState()
        }
    }

    override fun close() {
        detach()
        scope.cancel()
    }

    override suspend fun resolveToken(): GuidedTokenOutcome = auth.guidedToken()

    override suspend fun arm(
        cameraId: String,
        maxImages: Int,
        expiresAt: String,
        allowAutoUpload: Boolean,
    ): RemoteConsentOutcome = consent("arm", cameraId, maxImages, expiresAt, allowAutoUpload)

    override suspend fun disarm(cameraId: String, maxImages: Int, expiresAt: String): RemoteConsentOutcome =
        consent("disarm", cameraId, maxImages, expiresAt, allowAutoUpload = false)

    private suspend fun consent(
        action: String,
        cameraId: String,
        maxImages: Int,
        expiresAt: String,
        allowAutoUpload: Boolean,
    ): RemoteConsentOutcome {
        val sessionId = _state.value.sessionId
            ?: return RemoteConsentOutcome.Failed("no remote session attached")
        val token = guidedTokenOrNull() ?: return RemoteConsentOutcome.Failed(lastAuthError())
        val snapshot = _state.value
        val input = GuidedConsentInput(
            operationId = uuid(),
            sessionId = sessionId,
            expectedSessionRevision = snapshot.sessionRevision,
            action = action,
            consentRevision = snapshot.consentRevision + 1,
            maxImages = maxImages,
            expiresAt = expiresAt,
            cameraId = cameraId,
            allowAutoUpload = allowAutoUpload,
        )
        return when (val outcome = client.consent(token, sessionId, input)) {
            is GuidedOutcome.Success -> {
                applySession(outcome.value)
                val mapped = mapSessionState(outcome.value.state)
                if (mapped == null) {
                    RemoteConsentOutcome.Failed("unknown session state ${outcome.value.state}")
                } else {
                    RemoteConsentOutcome.Applied(mapped, outcome.value.revision, outcome.value.grantId)
                }
            }
            is GuidedOutcome.Conflict -> {
                refreshSession(token, sessionId)
                RemoteConsentOutcome.Conflict(outcome.currentRevision)
            }
            is GuidedOutcome.Unauthorized -> {
                _state.update { it.copy(authLost = true, lastError = outcome.message) }
                RemoteConsentOutcome.Failed("guided token rejected")
            }
            is GuidedOutcome.NotFound ->
                RemoteConsentOutcome.Failed("session unknown to Core")
            is GuidedOutcome.InvalidInput ->
                RemoteConsentOutcome.Failed("consent refused: ${outcome.message}")
            is GuidedOutcome.Expired ->
                RemoteConsentOutcome.Failed("consent expired: ${outcome.message}")
            is GuidedOutcome.RateLimited, is GuidedOutcome.Failed ->
                RemoteConsentOutcome.Failed(consentMessage(outcome))
        }
    }

    override suspend fun postEvent(event: GuidedSourceEvent): RemoteEventOutcome {
        val sessionId = _state.value.sessionId
            ?: return RemoteEventOutcome.Failed("no remote session attached")
        val token = guidedTokenOrNull() ?: return RemoteEventOutcome.Failed(lastAuthError())
        return when (val outcome = client.postEvent(token, sessionId, event)) {
            is GuidedOutcome.Success ->
                RemoteEventOutcome.Sent(outcome.value.eventSeq, outcome.value.sessionRevision)
            is GuidedOutcome.Conflict ->
                RemoteEventOutcome.Failed("event conflict: ${outcome.message}")
            is GuidedOutcome.Unauthorized -> {
                _state.update { it.copy(authLost = true) }
                RemoteEventOutcome.Failed("guided token rejected")
            }
            is GuidedOutcome.NotFound ->
                RemoteEventOutcome.Failed("session unknown to Core")
            is GuidedOutcome.InvalidInput, is GuidedOutcome.Expired,
            is GuidedOutcome.RateLimited, is GuidedOutcome.Failed ->
                RemoteEventOutcome.Failed(eventMessage(outcome))
        }
    }

    override suspend fun postStatus(status: GuidedDeviceStatus): RemoteEventOutcome {
        val sessionId = _state.value.sessionId
            ?: return RemoteEventOutcome.Failed("no remote session attached")
        val token = guidedTokenOrNull() ?: return RemoteEventOutcome.Failed(lastAuthError())
        return when (val outcome = client.postStatus(token, sessionId, status)) {
            is GuidedOutcome.Success ->
                RemoteEventOutcome.Sent(outcome.value.acceptedSeq, _state.value.sessionRevision)
            is GuidedOutcome.Unauthorized -> {
                _state.update { it.copy(authLost = true) }
                RemoteEventOutcome.Failed("guided token rejected")
            }
            is GuidedOutcome.Conflict, is GuidedOutcome.NotFound,
            is GuidedOutcome.InvalidInput, is GuidedOutcome.Expired,
            is GuidedOutcome.RateLimited, is GuidedOutcome.Failed ->
                RemoteEventOutcome.Failed(eventMessage(outcome))
        }
    }

    override suspend fun publishCapabilities(caps: GuidedCapabilities): RemoteEventOutcome {
        val token = guidedTokenOrNull() ?: return RemoteEventOutcome.Failed(lastAuthError())
        return when (val outcome = client.publishCapabilities(token, caps)) {
            is GuidedOutcome.Success -> RemoteEventOutcome.Sent(0, _state.value.sessionRevision)
            is GuidedOutcome.Unauthorized -> {
                _state.update { it.copy(authLost = true) }
                RemoteEventOutcome.Failed("guided token rejected")
            }
            is GuidedOutcome.Conflict, is GuidedOutcome.NotFound,
            is GuidedOutcome.InvalidInput, is GuidedOutcome.Expired,
            is GuidedOutcome.RateLimited, is GuidedOutcome.Failed ->
                RemoteEventOutcome.Failed("capabilities not published: ${eventMessage(outcome)}")
        }
    }

    override suspend fun reserveAttempt(input: GuidedAttemptInput): RemoteAttemptOutcome {
        val sessionId = _state.value.sessionId
            ?: return RemoteAttemptOutcome.Failed("no remote session attached")
        val token = guidedTokenOrNull() ?: return RemoteAttemptOutcome.Failed(lastAuthError())
        return when (val outcome = client.registerAttempt(token, sessionId, input)) {
            is GuidedOutcome.Success -> {
                val ack = outcome.value
                RemoteAttemptOutcome.Reserved(
                    ack.attemptId, ack.attemptNumber, ack.remainingExposures, ack.status,
                )
            }
            is GuidedOutcome.Conflict ->
                RemoteAttemptOutcome.Conflict(outcome.code, outcome.currentRevision)
            is GuidedOutcome.Expired ->
                RemoteAttemptOutcome.Expired(outcome.message)
            is GuidedOutcome.Unauthorized -> {
                _state.update { it.copy(authLost = true) }
                RemoteAttemptOutcome.Failed("guided token rejected")
            }
            is GuidedOutcome.NotFound, is GuidedOutcome.InvalidInput,
            is GuidedOutcome.RateLimited, is GuidedOutcome.Failed ->
                RemoteAttemptOutcome.Failed(attemptMessage(outcome))
        }
    }

    override fun buildEvent(
        kind: String,
        requestId: String?,
        requestRevision: Int?,
        attemptId: String?,
        reason: String?,
    ): GuidedSourceEvent? {
        val snapshot = _state.value
        val sessionId = snapshot.sessionId ?: return null
        val pairing = pairingStore.load() ?: return null
        if (pairing.guidedSourceId.isBlank()) return null
        return GuidedSourceEvent(
            eventId = uuid(),
            sessionId = sessionId,
            sourceId = pairing.guidedSourceId,
            requestId = requestId,
            requestRevision = requestRevision,
            kind = kind,
            occurredAt = utcClock().toIsoUtc(),
            monotonicNs = nanoClock().toString(),
            attemptId = attemptId,
            reason = reason,
        )
    }

    private suspend fun guidedTokenOrNull(): String? {
        return when (val resolved = auth.guidedToken()) {
            is GuidedTokenOutcome.Ready -> resolved.token
            is GuidedTokenOutcome.NeedsEnrollment -> {
                _state.update { it.copy(lastError = "device not enrolled: ${resolved.message}") }
                null
            }
            is GuidedTokenOutcome.ExchangeSpent -> {
                _state.update { it.copy(lastError = "enrollment spent: ${resolved.message}") }
                null
            }
            is GuidedTokenOutcome.Unauthorized -> {
                _state.update { it.copy(authLost = true, lastError = resolved.message) }
                null
            }
            is GuidedTokenOutcome.Failed -> {
                _state.update { it.copy(lastError = resolved.message) }
                null
            }
        }
    }

    private fun lastAuthError(): String = _state.value.lastError ?: "guided token unavailable"

    private suspend fun pollLoop(sessionId: String) {
        var backoff = pollIntervalMillis
        while (true) {
            try {
                val cont = pollOnce(sessionId)
                backoff = if (cont) pollIntervalMillis else return
                if (cont) sleeper(pollIntervalMillis)
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                _state.update { it.copy(lastError = "poll failed: ${e.message}") }
                sleeper(backoff)
                backoff = minOf(backoff * 2, maxBackoffMillis)
            }
        }
    }

    /** One poll; false stops the loop (terminal remote state). */
    private suspend fun pollOnce(sessionId: String): Boolean {
        val token = guidedTokenOrNull() ?: return true
        return when (val outcome = client.pollChanges(token, sessionId, _state.value.cursor)) {
            is GuidedOutcome.Success -> {
                applyPage(sessionId, outcome.value)
                if (outcome.value.snapshotRequired) refreshSession(token, sessionId)
                true
            }
            is GuidedOutcome.Unauthorized -> {
                // One forced re-exchange: the cached token may have
                // rotated or been revoked server-side.
                when (auth.refresh()) {
                    is GuidedTokenOutcome.Ready -> true
                    else -> {
                        _state.update { it.copy(authLost = true, lastError = "guided token rejected") }
                        false
                    }
                }
            }
            is GuidedOutcome.NotFound -> {
                _state.update { it.copy(sessionGone = true, lastError = "session unknown to Core") }
                false
            }
            is GuidedOutcome.RateLimited -> {
                _state.update { it.copy(lastError = "Core busy; backing off") }
                true
            }
            is GuidedOutcome.Conflict, is GuidedOutcome.InvalidInput,
            is GuidedOutcome.Expired, is GuidedOutcome.Failed -> {
                _state.update { it.copy(lastError = pollMessage(outcome)) }
                true
            }
        }
    }

    private suspend fun applyPage(sessionId: String, page: GuidedChangesPage) {
        val skips = mutableListOf<String>()
        for (change in page.events) {
            applyChange(change)?.let { skips += it }
        }
        val skipNote = skips.joinToString("; ").ifBlank { null }
        _state.update { it.copy(cursor = page.nextCursor, lastError = skipNote) }
        try {
            withContext(ioDispatcher) { prefs.setChangeCursor(sessionId, page.nextCursor) }
        } catch (_: Exception) {
            // Cursor loss replays changes on restart; application is
            // idempotent (latest-revision-wins), so this only wastes a poll.
        }
    }

    /** Applies one change; returns a skip note when the change was ignored. */
    private suspend fun applyChange(change: GuidedChange): String? {
        when (change.kind) {
            "session_changed" -> {
                // Revision carried on request/receipt changes; snapshot covers the rest.
                return null
            }
            "request_created", "request_revised", "request_cancelled" -> {
                val request = change.request ?: return null
                if (mapRequestStatus(request.status) == null) {
                    return "unknown request status ${request.status} (ignored)"
                }
                _state.update { current ->
                    val known = current.requests[request.requestId]
                    if (known != null && known.revision >= request.revision) return@update current
                    current.copy(
                        requests = current.requests + (request.requestId to request),
                        sessionRevision = maxOf(current.sessionRevision, change.sessionRevision),
                    )
                }
                return null
            }
            "source_event" -> {
                // Our own echo; the ack already confirmed it.
                return null
            }
            "receipt_created" -> {
                val receipt = change.receipt ?: return null
                _state.update { current ->
                    if (current.receipts.any { it.receiptId == receipt.receiptId }) return@update current
                    current.copy(
                        receipts = current.receipts + receipt,
                        sessionRevision = maxOf(current.sessionRevision, change.sessionRevision),
                    )
                }
                applyReceipt(receipt)
                return null
            }
            else -> return "unknown change kind ${change.kind} (ignored)"
        }
    }

    private suspend fun applyReceipt(receipt: GuidedReceipt) {
        val delivery = mapReceiptToDelivery(receipt.status) ?: return
        val kind = ReceiptKind.entries.firstOrNull { it.wire == receipt.status } ?: return
        val association = try {
            withContext(ioDispatcher) { associations.getGuidedAssociation(receipt.imageId) }
        } catch (_: Exception) {
            return
        } ?: return
        // Only Core receipts for OUR attempt advance the link; a
        // receipt naming another attempt is left for its own image.
        if (association.attemptId != null && association.attemptId != receipt.attemptId) return
        try {
            withContext(ioDispatcher) {
                associations.recordGuidedAssociation(
                    association.copy(deliveryState = delivery, receiptStatus = kind),
                )
            }
        } catch (_: Exception) {
            // Association loss is surfaced on next read; the receipt
            // itself is immutable on Core and re-applies on replay.
        }
    }

    private suspend fun refreshSession(token: String, sessionId: String) {
        when (val outcome = client.getSession(token, sessionId)) {
            is GuidedOutcome.Success -> {
                val snapshot = outcome.value
                applySession(snapshot.session)
                for (request in snapshot.requests) {
                    if (mapRequestStatus(request.status) == null) continue
                    _state.update { current ->
                        val known = current.requests[request.requestId]
                        if (known != null && known.revision >= request.revision) {
                            current
                        } else {
                            current.copy(requests = current.requests + (request.requestId to request))
                        }
                    }
                }
                for (receipt in snapshot.receipts) applyReceipt(receipt)
                _state.update { it.copy(receipts = snapshot.receipts) }
            }
            else -> _state.update { it.copy(lastError = "session snapshot failed") }
        }
    }

    private fun applySession(session: GuidedSession) {
        val mapped = mapSessionState(session.state)
        _state.update {
            it.copy(
                session = mapped ?: it.session,
                sessionRevision = session.revision,
                consentRevision = session.consentRevision,
                grantId = session.grantId,
                sessionExpiresAt = session.expiresAt,
                activeRequestId = session.activeRequestId,
                lastError = if (mapped == null) {
                    "unknown session state ${session.state} (ignored)"
                } else {
                    it.lastError
                },
            )
        }
    }

    private fun consentMessage(outcome: GuidedOutcome<*>): String = when (outcome) {
        is GuidedOutcome.RateLimited -> "Core busy; retry shortly"
        is GuidedOutcome.Failed -> outcome.message
        else -> "consent failed"
    }

    private fun eventMessage(outcome: GuidedOutcome<*>): String = when (outcome) {
        is GuidedOutcome.InvalidInput -> "event refused: ${outcome.message}"
        is GuidedOutcome.Expired -> "event expired: ${outcome.message}"
        is GuidedOutcome.RateLimited -> "Core busy; retry shortly"
        is GuidedOutcome.Failed -> outcome.message
        else -> "event failed"
    }

    private fun attemptMessage(outcome: GuidedOutcome<*>): String = when (outcome) {
        is GuidedOutcome.InvalidInput -> "attempt refused: ${outcome.message}"
        is GuidedOutcome.RateLimited -> "Core busy; retry shortly"
        is GuidedOutcome.Failed -> outcome.message
        else -> "attempt failed"
    }

    private fun pollMessage(outcome: GuidedOutcome<*>): String = when (outcome) {
        is GuidedOutcome.Conflict -> "poll conflict: ${(outcome as GuidedOutcome.Conflict).message}"
        is GuidedOutcome.InvalidInput -> "poll refused: ${(outcome as GuidedOutcome.InvalidInput).message}"
        is GuidedOutcome.Expired -> "poll expired: ${(outcome as GuidedOutcome.Expired).message}"
        is GuidedOutcome.Failed -> (outcome as GuidedOutcome.Failed).message
        else -> "poll failed"
    }
}

/** Frozen Session states; unknown spellings map to null (fail closed). */
fun mapSessionState(wire: String): SessionState? =
    try {
        SessionState.valueOf(wire)
    } catch (_: IllegalArgumentException) {
        null
    }

/** Frozen ViewRequest statuses; unknown spellings map to null. */
fun mapRequestStatus(wire: String): RequestState? =
    try {
        RequestState.valueOf(wire)
    } catch (_: IllegalArgumentException) {
        null
    }

/**
 * Journal delivery mapping, mirrored from
 * `delivery_state_for_receipt`: qualified → ACKNOWLEDGED,
 * package_only → PACKAGE_ACCEPTED, anything else → REJECTED.
 * Late receipts are terminal rejections of *this* attempt.
 */
fun mapReceiptToDelivery(receiptStatus: String): DeliveryState? {
    if (ReceiptKind.entries.none { it.wire == receiptStatus }) return null
    return when (receiptStatus) {
        "qualified" -> DeliveryState.ACKNOWLEDGED
        "package_only" -> DeliveryState.PACKAGE_ACCEPTED
        else -> DeliveryState.REJECTED
    }
}
