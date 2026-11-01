package org.anastylos.capture.sync

import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json

/**
 * Guided-control client over an injectable [HttpTransport].
 *
 * Same shape as [ReceiverClient]: the transport carries bytes (and
 * owns TLS pinning — there is deliberately no second transport), this
 * class owns the frozen G0 control-wire meaning. Every method performs
 * exactly one exchange; the caller owns retry, polling cadence, and
 * persistence. Source-role endpoints only — session/request creation,
 * claim, lease, finalize and resume belong to adapter roles the phone
 * never holds.
 *
 * Echo rule: every ack must reference the id the phone sent (event,
 * attempt, session, package), or nothing is marked. Unknown enum
 * spellings decode as strings and fail closed at the mapping layer.
 */
sealed interface GuidedOutcome<out T> {
    data class Success<T>(val value: T) : GuidedOutcome<T>
    /** 401: the guided (or legacy, for enroll) Bearer [REDACTED] rejected. */
    data class Unauthorized(val message: String) : GuidedOutcome<Nothing>
    /** 404: unknown session/grant/request/attempt — generic, no oracle. */
    data class NotFound(val message: String) : GuidedOutcome<Nothing>
    /** 400: the phone built an invalid body. Never retried blindly. */
    data class InvalidInput(val message: String) : GuidedOutcome<Nothing>
    /** 409: revision/idempotency conflict or unarmed attempt. */
    data class Conflict(val code: String, val message: String, val currentRevision: Int?) :
        GuidedOutcome<Nothing>
    /** 410: grant/request expired. Terminal for this id. */
    data class Expired(val message: String) : GuidedOutcome<Nothing>
    /** 429: busy; honor `retryAfterSeconds` before retrying. */
    data class RateLimited(val retryAfterSeconds: Long?) : GuidedOutcome<Nothing>
    /** Anything else, including malformed replies and echo mismatches. */
    data class Failed(val message: String) : GuidedOutcome<Nothing>
}

sealed interface EnrollOutcome {
    data class Enrolled(val sourceId: String, val token: String, val expiresAt: String) : EnrollOutcome
    /** 403 `not_enrolled`: the operator has not enrolled this device. */
    data class NotEnrolled(val message: String) : EnrollOutcome
    /** 403 `already_exchanged`: enrollment spent; re-enroll via operator. */
    data class AlreadyExchanged(val message: String) : EnrollOutcome
    data class Unauthorized(val message: String) : EnrollOutcome
    data class Failed(val message: String) : EnrollOutcome
}

private val GuidedJson = Json { ignoreUnknownKeys = true }

class GuidedControlClient(
    private val transport: HttpTransport,
    private val ioDispatcher: kotlinx.coroutines.CoroutineDispatcher = Dispatchers.IO,
) {
    /**
     * Exchanges the legacy pairing token for a scoped source token.
     * The operator must have enrolled this device first; the exchange
     * is single-use server-side.
     */
    suspend fun enroll(legacyToken: String): EnrollOutcome = withContext(ioDispatcher) {
        require(legacyToken.isNotBlank()) { "legacy token must not be blank" }
        val response = transport.exchange(
            HttpRequest(
                method = "POST",
                path = "/v1/guided/enroll",
                headers = mapOf("Authorization" to "Bearer $legacyToken"),
            ),
        )
        when (response.status) {
            201 -> {
                val parsed = try {
                    GuidedJson.decodeFromString(
                        GuidedEnrollReply.serializer(), response.bodyText(),
                    )
                } catch (_: Exception) {
                    return@withContext EnrollOutcome.Failed("enroll reply was not valid JSON")
                }
                if (parsed.sourceId.isBlank() || parsed.token.isBlank() ||
                    parsed.expiresAt.isBlank() || !isUuid(parsed.sourceId)
                ) {
                    EnrollOutcome.Failed("enroll reply missed source_id, token, or expiry")
                } else {
                    EnrollOutcome.Enrolled(parsed.sourceId, parsed.token, parsed.expiresAt)
                }
            }
            403 -> when (guidedError(response).code) {
                "not_enrolled" -> EnrollOutcome.NotEnrolled(guidedError(response).message)
                "already_exchanged" -> EnrollOutcome.AlreadyExchanged(guidedError(response).message)
                else -> EnrollOutcome.Failed("enroll refused: HTTP 403")
            }
            401 -> EnrollOutcome.Unauthorized(errorMessage(response, "unauthorized"))
            else -> EnrollOutcome.Failed("enroll failed: HTTP ${response.status}")
        }
    }

    suspend fun publishCapabilities(
        token: String,
        caps: GuidedCapabilities,
    ): GuidedOutcome<GuidedCapabilities> = withContext(ioDispatcher) {
        require(token.isNotBlank()) { "guided token must not be blank" }
        if (!isUuid(caps.sourceId)) {
            return@withContext GuidedOutcome.Failed("capabilities source_id is not a UUID")
        }
        val response = transport.exchange(
            HttpRequest(
                method = "POST",
                path = "/v1/guided/sources/${caps.sourceId}/capabilities",
                headers = guidedHeaders(token),
                body = GuidedJson.encodeToString(GuidedCapabilities.serializer(), caps)
                    .toByteArray(Charsets.UTF_8),
            ),
        )
        if (response.status != 200) return@withContext guidedFailure(response)
        val parsed = try {
            GuidedJson.decodeFromString(GuidedCapabilities.serializer(), response.bodyText())
        } catch (_: Exception) {
            return@withContext GuidedOutcome.Failed("capabilities reply was not valid JSON")
        }
        if (parsed.sourceId != caps.sourceId) {
            GuidedOutcome.Failed("capabilities reply referenced another source")
        } else {
            GuidedOutcome.Success(parsed)
        }
    }

    suspend fun pendingSessions(
        token: String,
        sourceId: String,
    ): GuidedOutcome<List<GuidedSession>> = withContext(ioDispatcher) {
        require(token.isNotBlank()) { "guided token must not be blank" }
        if (!isUuid(sourceId)) {
            return@withContext GuidedOutcome.Failed("source_id is not a UUID")
        }
        val response = transport.exchange(
            HttpRequest(
                method = "GET",
                path = "/v1/guided/sources/$sourceId/pending-sessions",
                headers = mapOf("Authorization" to "Bearer $token"),
            ),
        )
        if (response.status != 200) return@withContext guidedFailure(response)
        val parsed = try {
            GuidedJson.decodeFromString(GuidedPendingSessions.serializer(), response.bodyText())
        } catch (_: Exception) {
            return@withContext GuidedOutcome.Failed("pending-sessions reply was not valid JSON")
        }
        if (parsed.sessions.any { it.sourceId != sourceId }) {
            GuidedOutcome.Failed("pending-sessions reply mixed source identities")
        } else {
            GuidedOutcome.Success(parsed.sessions)
        }
    }

    suspend fun getSession(
        token: String,
        sessionId: String,
    ): GuidedOutcome<GuidedSessionSnapshot> = withContext(ioDispatcher) {
        require(token.isNotBlank()) { "guided token must not be blank" }
        if (!isUuid(sessionId)) {
            return@withContext GuidedOutcome.Failed("session_id is not a UUID")
        }
        val response = transport.exchange(
            HttpRequest(
                method = "GET",
                path = "/v1/guided/sessions/$sessionId",
                headers = mapOf("Authorization" to "Bearer $token"),
            ),
        )
        if (response.status != 200) return@withContext guidedFailure(response)
        val parsed = try {
            GuidedJson.decodeFromString(GuidedSessionSnapshot.serializer(), response.bodyText())
        } catch (_: Exception) {
            return@withContext GuidedOutcome.Failed("session snapshot was not valid JSON")
        }
        if (parsed.session.sessionId != sessionId) {
            GuidedOutcome.Failed("session snapshot referenced another session")
        } else {
            GuidedOutcome.Success(parsed)
        }
    }

    suspend fun pollChanges(
        token: String,
        sessionId: String,
        afterCursor: String,
    ): GuidedOutcome<GuidedChangesPage> = withContext(ioDispatcher) {
        require(token.isNotBlank()) { "guided token must not be blank" }
        if (!isUuid(sessionId)) {
            return@withContext GuidedOutcome.Failed("session_id is not a UUID")
        }
        val cursor = afterCursor.ifBlank { "0" }
        val response = transport.exchange(
            HttpRequest(
                method = "GET",
                path = "/v1/guided/sessions/$sessionId/changes?after=$cursor",
                headers = mapOf("Authorization" to "Bearer $token"),
            ),
        )
        if (response.status != 200) return@withContext guidedFailure(response)
        val parsed = try {
            GuidedJson.decodeFromString(GuidedChangesPage.serializer(), response.bodyText())
        } catch (_: Exception) {
            return@withContext GuidedOutcome.Failed("changes page was not valid JSON")
        }
        if (parsed.nextCursor.isBlank()) {
            return@withContext GuidedOutcome.Failed("changes page missed its cursor")
        }
        val seqs = parsed.events.map { it.changeSeq }
        if (seqs != seqs.sorted()) {
            return@withContext GuidedOutcome.Failed("changes page was not ordered by change_seq")
        }
        GuidedOutcome.Success(parsed)
    }

    suspend fun postEvent(
        token: String,
        sessionId: String,
        event: GuidedSourceEvent,
    ): GuidedOutcome<GuidedEventAck> = withContext(ioDispatcher) {
        require(token.isNotBlank()) { "guided token must not be blank" }
        if (!isUuid(sessionId) || event.sessionId != sessionId) {
            return@withContext GuidedOutcome.Failed("event session_id must match the URL session")
        }
        val response = transport.exchange(
            HttpRequest(
                method = "POST",
                path = "/v1/guided/sessions/$sessionId/events",
                headers = guidedHeaders(token),
                body = GuidedJson.encodeToString(GuidedSourceEvent.serializer(), event)
                    .toByteArray(Charsets.UTF_8),
            ),
        )
        if (response.status != 200) return@withContext guidedFailure(response)
        val parsed = try {
            GuidedJson.decodeFromString(GuidedEventAck.serializer(), response.bodyText())
        } catch (_: Exception) {
            return@withContext GuidedOutcome.Failed("event ack was not valid JSON")
        }
        if (parsed.acceptedEventId != event.eventId) {
            GuidedOutcome.Failed("event ack referenced another event id")
        } else {
            GuidedOutcome.Success(parsed)
        }
    }

    suspend fun postStatus(
        token: String,
        sessionId: String,
        status: GuidedDeviceStatus,
    ): GuidedOutcome<GuidedStatusAck> = withContext(ioDispatcher) {
        require(token.isNotBlank()) { "guided token must not be blank" }
        if (!isUuid(sessionId) || status.sessionId != sessionId) {
            return@withContext GuidedOutcome.Failed("status session_id must match the URL session")
        }
        val response = transport.exchange(
            HttpRequest(
                method = "POST",
                path = "/v1/guided/sessions/$sessionId/status",
                headers = guidedHeaders(token),
                body = GuidedJson.encodeToString(GuidedDeviceStatus.serializer(), status)
                    .toByteArray(Charsets.UTF_8),
            ),
        )
        if (response.status != 200) return@withContext guidedFailure(response)
        val parsed = try {
            GuidedJson.decodeFromString(GuidedStatusAck.serializer(), response.bodyText())
        } catch (_: Exception) {
            return@withContext GuidedOutcome.Failed("status ack was not valid JSON")
        }
        if (parsed.acceptedSeq != status.eventSeq) {
            GuidedOutcome.Failed("status ack referenced another event seq")
        } else {
            GuidedOutcome.Success(parsed)
        }
    }

    suspend fun consent(
        token: String,
        sessionId: String,
        input: GuidedConsentInput,
    ): GuidedOutcome<GuidedSession> = withContext(ioDispatcher) {
        require(token.isNotBlank()) { "guided token must not be blank" }
        if (!isUuid(sessionId) || input.sessionId != sessionId) {
            return@withContext GuidedOutcome.Failed("consent session_id must match the URL session")
        }
        if (input.action != "arm" && input.action != "disarm") {
            return@withContext GuidedOutcome.Failed("consent action must be arm or disarm")
        }
        val response = transport.exchange(
            HttpRequest(
                method = "POST",
                path = "/v1/guided/sessions/$sessionId/consent",
                headers = guidedHeaders(token),
                body = GuidedJson.encodeToString(GuidedConsentInput.serializer(), input)
                    .toByteArray(Charsets.UTF_8),
            ),
        )
        if (response.status != 200) return@withContext guidedFailure(response)
        val parsed = try {
            GuidedJson.decodeFromString(GuidedSession.serializer(), response.bodyText())
        } catch (_: Exception) {
            return@withContext GuidedOutcome.Failed("consent reply was not valid JSON")
        }
        if (parsed.sessionId != sessionId) {
            GuidedOutcome.Failed("consent reply referenced another session")
        } else {
            GuidedOutcome.Success(parsed)
        }
    }

    suspend fun registerAttempt(
        token: String,
        sessionId: String,
        input: GuidedAttemptInput,
    ): GuidedOutcome<GuidedAttemptAck> = withContext(ioDispatcher) {
        require(token.isNotBlank()) { "guided token must not be blank" }
        if (!isUuid(sessionId) || input.sessionId != sessionId) {
            return@withContext GuidedOutcome.Failed("attempt session_id must match the URL session")
        }
        if (!isUuid(input.attemptId)) {
            return@withContext GuidedOutcome.Failed("attempt_id is not a UUID")
        }
        val response = transport.exchange(
            HttpRequest(
                method = "POST",
                path = "/v1/guided/sessions/$sessionId/attempts",
                headers = guidedHeaders(token),
                body = GuidedJson.encodeToString(GuidedAttemptInput.serializer(), input)
                    .toByteArray(Charsets.UTF_8),
            ),
        )
        if (response.status != 200 && response.status != 201) {
            return@withContext guidedFailure(response)
        }
        val parsed = try {
            GuidedJson.decodeFromString(GuidedAttemptAck.serializer(), response.bodyText())
        } catch (_: Exception) {
            return@withContext GuidedOutcome.Failed("attempt ack was not valid JSON")
        }
        if (parsed.attemptId != input.attemptId) {
            GuidedOutcome.Failed("attempt ack referenced another attempt id")
        } else {
            GuidedOutcome.Success(parsed)
        }
    }

    suspend fun bindAsset(
        token: String,
        attemptId: String,
        binding: GuidedAssetBinding,
    ): GuidedOutcome<GuidedAttemptAck> = withContext(ioDispatcher) {
        require(token.isNotBlank()) { "guided token must not be blank" }
        if (!isUuid(attemptId) || binding.attemptId != attemptId) {
            return@withContext GuidedOutcome.Failed("binding attempt_id must match the URL attempt")
        }
        val response = transport.exchange(
            HttpRequest(
                method = "POST",
                path = "/v1/guided/attempts/$attemptId/asset-binding",
                headers = guidedHeaders(token),
                body = GuidedJson.encodeToString(GuidedAssetBinding.serializer(), binding)
                    .toByteArray(Charsets.UTF_8),
            ),
        )
        if (response.status != 200) return@withContext guidedFailure(response)
        val parsed = try {
            GuidedJson.decodeFromString(GuidedAttemptAck.serializer(), response.bodyText())
        } catch (_: Exception) {
            return@withContext GuidedOutcome.Failed("binding ack was not valid JSON")
        }
        if (parsed.attemptId != attemptId) {
            GuidedOutcome.Failed("binding ack referenced another attempt id")
        } else {
            GuidedOutcome.Success(parsed)
        }
    }

    /**
     * Uploads a spatial sidecar for one attempt. `sidecarJson` is the
     * complete `anastylos-spatial-evidence@1` document; the revision
     * starts at 0 for a first upload.
     */
    suspend fun uploadSidecar(
        token: String,
        attemptId: String,
        operationId: String,
        expectedSidecarRevision: Int,
        sidecarJson: String,
    ): GuidedOutcome<GuidedAttemptDelivery> = withContext(ioDispatcher) {
        require(token.isNotBlank()) { "guided token must not be blank" }
        if (!isUuid(attemptId) || !isUuid(operationId)) {
            return@withContext GuidedOutcome.Failed("attempt/operation ids must be UUIDs")
        }
        if (expectedSidecarRevision < 0) {
            return@withContext GuidedOutcome.Failed("expected_sidecar_revision must be >= 0")
        }
        val body = """{"operation_id":"$operationId","expected_sidecar_revision":$expectedSidecarRevision,"sidecar":$sidecarJson}"""
        val response = transport.exchange(
            HttpRequest(
                method = "POST",
                path = "/v1/guided/attempts/$attemptId/spatial",
                headers = guidedHeaders(token),
                body = body.toByteArray(Charsets.UTF_8),
            ),
        )
        if (response.status != 200) return@withContext guidedFailure(response)
        val parsed = try {
            GuidedJson.decodeFromString(GuidedAttemptDelivery.serializer(), response.bodyText())
        } catch (_: Exception) {
            return@withContext GuidedOutcome.Failed("sidecar reply was not valid JSON")
        }
        if (parsed.attemptId != attemptId) {
            GuidedOutcome.Failed("sidecar reply referenced another attempt id")
        } else {
            GuidedOutcome.Success(parsed)
        }
    }

    suspend fun getCaptureSet(
        token: String,
        sessionId: String,
    ): GuidedOutcome<GuidedCaptureSet> = withContext(ioDispatcher) {
        require(token.isNotBlank()) { "guided token must not be blank" }
        if (!isUuid(sessionId)) {
            return@withContext GuidedOutcome.Failed("session_id is not a UUID")
        }
        val response = transport.exchange(
            HttpRequest(
                method = "GET",
                path = "/v1/guided/sessions/$sessionId/capture-set",
                headers = mapOf("Authorization" to "Bearer $token"),
            ),
        )
        if (response.status != 200) return@withContext guidedFailure(response)
        val parsed = try {
            GuidedJson.decodeFromString(GuidedCaptureSet.serializer(), response.bodyText())
        } catch (_: Exception) {
            return@withContext GuidedOutcome.Failed("capture set was not valid JSON")
        }
        if (parsed.sessionId != sessionId) {
            GuidedOutcome.Failed("capture set referenced another session")
        } else {
            GuidedOutcome.Success(parsed)
        }
    }

    /**
     * VOICE-01 R9: one WAV segment to the receiver's transcriber. Returns
     * the raw exchange; [GuidedVoiceLink] owns the voice body formats.
     */
    fun voiceTranscribe(token: String, sessionId: String, wav: ByteArray): HttpResponse {
        require(isUuid(sessionId)) { "session_id is not a UUID" }
        return transport.exchange(
            HttpRequest(
                method = "POST", path = "/v1/guided/sessions/$sessionId/voice/transcribe",
                headers = mapOf("Authorization" to "Bearer $token", "Content-Type" to "audio/wav"),
                body = wav,
            ),
        )
    }

    /** VOICE-01 R10: one `{kind: input|command}` body; raw exchange as above. */
    fun voiceInput(token: String, sessionId: String, json: String): HttpResponse {
        require(isUuid(sessionId)) { "session_id is not a UUID" }
        return transport.exchange(
            HttpRequest(
                method = "POST", path = "/v1/guided/sessions/$sessionId/voice/input",
                headers = mapOf("Authorization" to "Bearer $token", "Content-Type" to "application/json"),
                body = json.toByteArray(Charsets.UTF_8),
            ),
        )
    }

    private fun guidedHeaders(token: String): Map<String, String> = mapOf(
        "Content-Type" to "application/json",
        "Authorization" to "Bearer $token",
    )

    /** Shared failure mapping for every guided route (see `_journal_errors`). */
    private fun guidedFailure(response: HttpResponse): GuidedOutcome<Nothing> {
        val detail = guidedError(response)
        return when (response.status) {
            400 -> GuidedOutcome.InvalidInput(detail.message.ifBlank { "invalid input" })
            401 -> GuidedOutcome.Unauthorized(detail.message.ifBlank { "unauthorized" })
            404 -> GuidedOutcome.NotFound(detail.message.ifBlank { "not found" })
            409 -> GuidedOutcome.Conflict(
                code = detail.code.ifBlank { "conflict" },
                message = detail.message.ifBlank { "conflict" },
                currentRevision = detail.currentRevision,
            )
            410 -> GuidedOutcome.Expired(detail.message.ifBlank { "expired" })
            413 -> GuidedOutcome.InvalidInput(detail.message.ifBlank { "body too large" })
            429 -> GuidedOutcome.RateLimited(response.retryAfterSeconds())
            else -> GuidedOutcome.Failed("request failed: HTTP ${response.status}")
        }
    }

    private fun guidedError(response: HttpResponse): GuidedErrorDetail {
        return try {
            GuidedJson.decodeFromString(GuidedErrorBody.serializer(), response.bodyText()).error
                ?: GuidedErrorDetail()
        } catch (_: Exception) {
            GuidedErrorDetail()
        }
    }

    private fun errorMessage(response: HttpResponse, fallback: String): String {
        val detail = guidedError(response)
        return if (detail.message.isBlank()) fallback else detail.message
    }

    private fun HttpResponse.retryAfterSeconds(): Long? =
        headers.entries.firstOrNull { it.key.equals("Retry-After", ignoreCase = true) }
            ?.value?.trim()?.toLongOrNull()
}

private fun isUuid(value: String): Boolean =
    try {
        UUID.fromString(value)
        true
    } catch (_: IllegalArgumentException) {
        false
    }
