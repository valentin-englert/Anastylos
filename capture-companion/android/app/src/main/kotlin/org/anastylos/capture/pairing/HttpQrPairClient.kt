package org.anastylos.capture.pairing

import java.security.cert.CertificateException
import java.time.Instant
import javax.net.ssl.SSLException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import org.anastylos.capture.sync.HttpRequest
import org.anastylos.capture.sync.HttpResponse
import org.anastylos.capture.sync.HttpTransport
import org.anastylos.capture.sync.QrPairPayload
import org.anastylos.capture.sync.SpkiMismatchException
import org.anastylos.capture.sync.TransportException

@Serializable
private data class RedeemRequest(
    @SerialName("pairing_code") val pairingCode: String,
    @SerialName("device_label") val deviceLabel: String,
)

@Serializable
private data class RedeemReply(
    val status: String = "",
    @SerialName("request_id") val requestId: String = "",
    @SerialName("expires_at") val expiresAt: Double = Double.NaN,
)

@Serializable
private data class PollReply(
    val status: String = "",
    @SerialName("workstation_id") val workstationId: String = "",
    val token: String = "",
)

@Serializable
private data class StatusReply(
    @SerialName("workstation_id") val workstationId: String = "",
)

@Serializable
private data class ErrorDetail(val code: String = "", val message: String = "")

@Serializable
private data class ErrorBody(val error: ErrorDetail? = null)

private val PairingJson = Json { ignoreUnknownKeys = true }
private val RequestIdRe = Regex("^[0-9a-f]{32}$")

/** Transport failure, classified for retry decisions. */
private sealed interface ExchangeFailure {
    /** Peer key differs from the scanned pin. Terminal, never retried. */
    data object PinMismatch : ExchangeFailure

    /** TLS rejected the peer (expired cert, protocol failure). Terminal. */
    data class TlsRejected(val message: String) : ExchangeFailure

    /** Nothing answered (wifi, routing, refused). Possibly transient. */
    data class Down(val message: String) : ExchangeFailure
}

/**
 * [QrPairClient] over an SPKI-pinned [HttpTransport].
 *
 * The transport comes from [transportFactory], which must pin the
 * payload's SPKI (see `pinnedSpkiSocketFactory`) — this class never
 * speaks to an unpinned endpoint. Cancellation aborts a poll
 * immediately; every other outcome is a typed value, never an
 * exception, except for caller bugs (which the ViewModel funnels
 * into its generic failure state).
 */
class HttpQrPairClient(
    private val transportFactory: (QrPairPayload) -> HttpTransport,
    private val ioDispatcher: kotlinx.coroutines.CoroutineDispatcher = Dispatchers.IO,
    private val clock: () -> Instant = Instant::now,
    private val pollIntervalMillis: Long = 2_000,
    private val pollMaxConsecutiveFailures: Int = 5,
    /** Poll grace past the server-stated expiry; absorbs clock skew. The server 404s when truly gone. */
    private val pollGraceMillis: Long = 30_000,
) : QrPairClient {
    override suspend fun redeem(payload: QrPairPayload, deviceLabel: String): RedeemOutcome =
        withContext(ioDispatcher) {
            val transport = transportFactory(payload)
            val body = PairingJson.encodeToString(
                RedeemRequest.serializer(),
                RedeemRequest(payload.pairingCode, deviceLabel),
            )
            val response = try {
                transport.exchange(
                    HttpRequest(
                        method = "POST",
                        path = "/v1/pair/qr",
                        headers = mapOf("Content-Type" to "application/json"),
                        body = body.toByteArray(Charsets.UTF_8),
                    ),
                )
            } catch (e: TransportException) {
                return@withContext when (val failure = classify(e)) {
                    is ExchangeFailure.PinMismatch ->
                        RedeemOutcome.PinMismatch("peer public key differs from the scanned pin")
                    is ExchangeFailure.TlsRejected ->
                        RedeemOutcome.ServerError("secure connection failed: ${failure.message}")
                    is ExchangeFailure.Down ->
                        RedeemOutcome.NetworkError(failure.message)
                }
            }
            when (response.status) {
                202 -> {
                    val reply = try {
                        PairingJson.decodeFromString(RedeemReply.serializer(), response.bodyText())
                    } catch (_: Exception) {
                        return@withContext RedeemOutcome.ServerError("redeem reply was not valid JSON")
                    }
                    if (reply.status != "pending" || !RequestIdRe.matches(reply.requestId) ||
                        !reply.expiresAt.isFinite() || reply.expiresAt <= 0
                    ) {
                        RedeemOutcome.ServerError("redeem reply missed its request")
                    } else {
                        val seconds = reply.expiresAt.toLong()
                        val nanos = ((reply.expiresAt - seconds) * 1_000_000_000)
                            .toLong().coerceIn(0, 999_999_999)
                        RedeemOutcome.Pending(
                            PendingRequest(reply.requestId, Instant.ofEpochSecond(seconds, nanos)),
                        )
                    }
                }
                // One generic server message by design: no oracle for
                // code vs expiry vs reuse. Surfaced verbatim.
                403 -> RedeemOutcome.CodeInvalid(errorMessage(response, "pairing failed"))
                429 -> RedeemOutcome.RateLimited(response.retryAfterSeconds())
                else -> RedeemOutcome.ServerError("redeem failed: HTTP ${response.status}")
            }
        }

    override suspend fun awaitApproval(
        payload: QrPairPayload,
        request: PendingRequest,
    ): ApprovalOutcome = withContext(ioDispatcher) {
        require(RequestIdRe.matches(request.requestId)) { "poll needs a server-issued request id" }
        pollLoop(transportFactory(payload), request)
    }

    override suspend fun verify(payload: QrPairPayload, token: String): VerifyOutcome =
        withContext(ioDispatcher) {
            require(token.isNotBlank()) { "verify needs the approved token" }
            val response = try {
                transportFactory(payload).exchange(
                    HttpRequest(
                        method = "GET",
                        path = "/v1/status",
                        headers = mapOf("Authorization" to "Bearer $token"),
                    ),
                )
            } catch (e: TransportException) {
                return@withContext when (val failure = classify(e)) {
                    is ExchangeFailure.PinMismatch ->
                        VerifyOutcome.PinMismatch("peer public key differs from the scanned pin")
                    is ExchangeFailure.TlsRejected ->
                        VerifyOutcome.ServerError("secure connection failed: ${failure.message}")
                    is ExchangeFailure.Down ->
                        VerifyOutcome.NetworkError(failure.message)
                }
            }
            when (response.status) {
                200 -> {
                    val reply = try {
                        PairingJson.decodeFromString(StatusReply.serializer(), response.bodyText())
                    } catch (_: Exception) {
                        return@withContext VerifyOutcome.ServerError("status reply was not valid JSON")
                    }
                    if (reply.workstationId.isBlank()) {
                        VerifyOutcome.ServerError("status reply missed its workstation id")
                    } else {
                        VerifyOutcome.Verified(reply.workstationId)
                    }
                }
                401 -> VerifyOutcome.Unauthorized
                else -> VerifyOutcome.ServerError("status failed: HTTP ${response.status}")
            }
        }

    private suspend fun pollLoop(transport: HttpTransport, request: PendingRequest): ApprovalOutcome {
        val deadline = request.expiresAt.plusMillis(pollGraceMillis)
        var consecutiveFailures = 0
        while (true) {
            if (!clock().isBefore(deadline)) {
                return ApprovalOutcome.NotApproved("not approved in time")
            }
            val response = try {
                transport.exchange(
                    HttpRequest(method = "GET", path = "/v1/pair/qr/${request.requestId}"),
                )
            } catch (e: CancellationException) {
                throw e
            } catch (e: TransportException) {
                when (val failure = classify(e)) {
                    is ExchangeFailure.PinMismatch -> return ApprovalOutcome.PinMismatch(
                        "peer public key differs from the scanned pin",
                    )
                    is ExchangeFailure.TlsRejected -> return ApprovalOutcome.ServerError(
                        "secure connection failed: ${failure.message}",
                    )
                    is ExchangeFailure.Down -> {
                        consecutiveFailures++
                        if (consecutiveFailures >= pollMaxConsecutiveFailures) {
                            return ApprovalOutcome.NetworkError(failure.message)
                        }
                        delay(pollIntervalMillis)
                        continue
                    }
                }
            }
            consecutiveFailures = 0
            when (response.status) {
                200 -> {
                    val reply = try {
                        PairingJson.decodeFromString(PollReply.serializer(), response.bodyText())
                    } catch (_: Exception) {
                        return ApprovalOutcome.ServerError("poll reply was not valid JSON")
                    }
                    when (reply.status) {
                        "approved" -> {
                            if (reply.token.isBlank() || reply.workstationId.isBlank()) {
                                return ApprovalOutcome.ServerError(
                                    "approval missed its token",
                                )
                            }
                            return ApprovalOutcome.Approved(reply.token, reply.workstationId)
                        }
                        "pending" -> delay(pollIntervalMillis)
                        else -> return ApprovalOutcome.ServerError(
                            "poll reply held an unknown status",
                        )
                    }
                }
                // Unknown, rejected, and expired answer identically by
                // design; the client cannot tell them apart, so it
                // reports the one honest outcome.
                404 -> return ApprovalOutcome.NotApproved("not approved in time")
                429 -> delay(response.pollWaitMillis())
                else -> return ApprovalOutcome.ServerError(
                    "poll failed: HTTP ${response.status}",
                )
            }
        }
    }

    private fun classify(failure: TransportException): ExchangeFailure {
        var cause: Throwable? = failure
        var tlsMessage: String? = null
        while (cause != null) {
            if (cause is SpkiMismatchException) return ExchangeFailure.PinMismatch
            if (cause is CertificateException || cause is SSLException) {
                tlsMessage = cause.message ?: cause.javaClass.simpleName
            }
            cause = cause.cause
        }
        // The TCP path worked far enough to fail inside TLS: a
        // server/config problem, not reachability.
        tlsMessage?.let { return ExchangeFailure.TlsRejected(it.take(160)) }
        return ExchangeFailure.Down(failure.message?.take(160) ?: "workstation unreachable")
    }

    private fun HttpResponse.retryAfterSeconds(): Long? =
        headers.entries.firstOrNull { it.key.equals("Retry-After", ignoreCase = true) }
            ?.value?.trim()?.toLongOrNull()?.takeIf { it >= 0 }

    private fun HttpResponse.pollWaitMillis(): Long {
        val seconds = retryAfterSeconds() ?: (pollIntervalMillis / 1000)
        return seconds.coerceIn(1, 60) * 1000
    }

    private fun errorMessage(response: HttpResponse, fallback: String): String {
        return try {
            val parsed = PairingJson.decodeFromString(ErrorBody.serializer(), response.bodyText())
            val detail = parsed.error?.message.orEmpty()
            if (detail.isBlank()) fallback else detail.take(160)
        } catch (_: Exception) {
            fallback
        }
    }
}
