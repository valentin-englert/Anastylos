package org.anastylos.capture.sync

import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI
import javax.net.ssl.HttpsURLConnection
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/**
 * Receiver protocol client over an injectable [HttpTransport].
 *
 * The transport carries bytes; this class owns the protocol meaning:
 * pairing handshake, upload headers, id echo verification, and the
 * mapping from status codes to typed outcomes. The uploader owns retry;
 * every method here performs exactly one exchange.
 */
data class HttpRequest(
    val method: String,
    /** Path plus optional query, e.g. `/v1/packages?session_id=…`. */
    val path: String,
    val headers: Map<String, String> = emptyMap(),
    val body: ByteArray? = null,
)

data class HttpResponse(
    val status: Int,
    val body: ByteArray,
    val headers: Map<String, String> = emptyMap(),
) {
    fun bodyText(): String = body.toString(Charsets.UTF_8)
}

class TransportException(message: String, cause: Throwable? = null) : IOException(message, cause)

fun interface HttpTransport {
    /** Exactly one exchange. Network failures throw [TransportException]. */
    @Throws(TransportException::class)
    fun exchange(request: HttpRequest): HttpResponse
}

private val ResponseJson = Json { ignoreUnknownKeys = true }

@Serializable
private data class PairResponse(
    @SerialName("workstation_id") val workstationId: String = "",
    val token: String = "",
)

@Serializable
private data class ErrorDetail(
    val code: String = "",
    val message: String = "",
)

@Serializable
private data class ErrorBody(
    val error: ErrorDetail? = null,
)

@Serializable
data class FeedPackage(
    @SerialName("package_id") val packageId: String = "",
    @SerialName("staged_at") val stagedAt: String? = null,
    @SerialName("zip_sha256") val zipSha256: String? = null,
    val status: String? = null,
    @SerialName("session_ids") val sessionIds: List<String> = emptyList(),
    @SerialName("observation_ids") val observationIds: List<String> = emptyList(),
    @SerialName("accepted_for_review") val acceptedForReview: List<String> = emptyList(),
    @SerialName("duplicate_deliveries") val duplicateDeliveries: Int = 0,
)

@Serializable
private data class FeedResponse(
    val packages: List<FeedPackage> = emptyList(),
)

data class Pairing(
    val workstationId: String,
    val token: String,
)

sealed interface PairOutcome {
    data class Paired(val pairing: Pairing) : PairOutcome
    data class Rejected(val message: String) : PairOutcome
    data class RateLimited(val retryAfterSeconds: Long?) : PairOutcome
    data class Failed(val message: String) : PairOutcome
}

/** The bound attempt a guided upload belongs to. */
data class GuidedUploadLink(
    val sessionId: String,
    val requestId: String,
    val requestRevision: Int,
    val attemptId: String,
) {
    fun headers(): Map<String, String> = mapOf(
        "X-Guided-Session-Id" to sessionId,
        "X-Guided-Request-Id" to requestId,
        "X-Guided-Request-Revision" to requestRevision.toString(),
        "X-Guided-Attempt-Id" to attemptId,
    )
}

sealed interface UploadOutcome {
    /**
     * Staged now (`duplicate=false`) or already known (`duplicate=true`).
     * [receiptStatus] is the Core receipt a guided upload joins; null on the legacy path.
     */
    data class Staged(val duplicate: Boolean, val reportJson: String, val receiptStatus: String? = null) :
        UploadOutcome
    /** Validator refused the bytes; retrying identical bytes is pointless. */
    data class Rejected(val reportJson: String) : UploadOutcome
    data class Unauthorized(val message: String) : UploadOutcome
    data class TooLarge(val message: String) : UploadOutcome
    data class RateLimited(val retryAfterSeconds: Long?) : UploadOutcome
    data class Failed(val message: String) : UploadOutcome
}

sealed interface FeedOutcome {
    data class Feed(val packages: List<FeedPackage>) : FeedOutcome
    data class Unauthorized(val message: String) : FeedOutcome
    data class BadFilter(val message: String) : FeedOutcome
    data class Failed(val message: String) : FeedOutcome
}

class ReceiverClient(
    private val transport: HttpTransport,
    private val ioDispatcher: kotlinx.coroutines.CoroutineDispatcher = Dispatchers.IO,
) {
    suspend fun pair(pairingCode: String): PairOutcome = withContext(ioDispatcher) {
        val body = """{"pairing_code":"${jsonEscape(pairingCode)}"}"""
        val response = transport.exchange(
            HttpRequest(
                method = "POST",
                path = "/v1/pair",
                headers = mapOf("Content-Type" to "application/json"),
                body = body.toByteArray(Charsets.UTF_8),
            ),
        )
        when (response.status) {
            200 -> {
                val parsed = try {
                    ResponseJson.decodeFromString(PairResponse.serializer(), response.bodyText())
                } catch (_: Exception) {
                    return@withContext PairOutcome.Failed("pairing reply was not valid JSON")
                }
                if (parsed.workstationId.isBlank() || parsed.token.isBlank()) {
                    PairOutcome.Failed("pairing reply missed workstation_id or token")
                } else {
                    PairOutcome.Paired(Pairing(parsed.workstationId, parsed.token))
                }
            }
            403 -> PairOutcome.Rejected(errorMessage(response, "pairing failed"))
            429 -> PairOutcome.RateLimited(response.retryAfter())
            else -> PairOutcome.Failed("pairing failed: HTTP ${response.status}")
        }
    }

    /**
     * Uploads one package. With [guided] the receiver routes it as the
     * guided upload of a bound attempt (guided token, `X-Guided-*`
     * headers) and joins the attempt's receipt; without, it is the
     * legacy upload.
     */
    suspend fun uploadPackage(token: String, pkg: BuiltFramePackage, guided: GuidedUploadLink? = null): UploadOutcome =
        withContext(ioDispatcher) {
            val headers = mapOf(
                "Content-Type" to "application/zip",
                "Authorization" to "Bearer $token",
                "X-Package-Id" to pkg.packageId,
                "X-Package-Sha256" to pkg.packageSha256,
            ) + (guided?.headers() ?: emptyMap())
            val response = transport.exchange(
                HttpRequest(method = "POST", path = "/v1/packages", headers = headers, body = pkg.bytes),
            )
            when (response.status) {
                201, 200 -> {
                    val text = response.bodyText()
                    val root = try {
                        ResponseJson.parseToJsonElement(text).jsonObject
                    } catch (_: Exception) {
                        return@withContext UploadOutcome.Failed("acceptance reply was not valid JSON")
                    }
                    val duplicate = root["duplicate"]?.jsonPrimitive?.content?.toBooleanStrictOrNull()
                    val report = try {
                        root["report"]?.jsonObject
                    } catch (_: Exception) {
                        null
                    }
                    val echoed = report?.get("package_id")?.jsonPrimitive
                        ?.takeIf { it.isString }?.content
                    if (duplicate == null || report == null) {
                        return@withContext UploadOutcome.Failed("acceptance reply missed its report")
                    }
                    // The ack must reference the uploaded id, or nothing is marked.
                    if (echoed != pkg.packageId) {
                        UploadOutcome.Failed("acceptance report referenced another package id")
                    } else {
                        val receipt = try {
                            root["receipt"]?.jsonObject?.get("status")?.jsonPrimitive?.content
                        } catch (_: Exception) {
                            null
                        }
                        UploadOutcome.Staged(duplicate = duplicate, reportJson = text, receiptStatus = receipt)
                    }
                }
                422 -> UploadOutcome.Rejected(response.bodyText())
                401 -> UploadOutcome.Unauthorized(errorMessage(response, "unauthorized"))
                413 -> UploadOutcome.TooLarge(errorMessage(response, "package too large"))
                429 -> UploadOutcome.RateLimited(response.retryAfter())
                else -> UploadOutcome.Failed("upload failed: HTTP ${response.status}")
            }
        }

    suspend fun sessionFeed(token: String, sessionId: String): FeedOutcome =
        withContext(ioDispatcher) {
            val response = transport.exchange(
                HttpRequest(
                    method = "GET",
                    path = "/v1/packages?session_id=$sessionId",
                    headers = mapOf("Authorization" to "Bearer $token"),
                ),
            )
            when (response.status) {
                200 -> {
                    val parsed = try {
                        ResponseJson.decodeFromString(FeedResponse.serializer(), response.bodyText())
                    } catch (_: Exception) {
                        return@withContext FeedOutcome.Failed("feed reply was not valid JSON")
                    }
                    FeedOutcome.Feed(parsed.packages)
                }
                401 -> FeedOutcome.Unauthorized(errorMessage(response, "unauthorized"))
                400 -> FeedOutcome.BadFilter(errorMessage(response, "bad session filter"))
                else -> FeedOutcome.Failed("feed failed: HTTP ${response.status}")
            }
        }

    private fun errorMessage(response: HttpResponse, fallback: String): String {
        return try {
            val parsed = ResponseJson.decodeFromString(ErrorBody.serializer(), response.bodyText())
            val detail = parsed.error
            if (detail == null || detail.message.isBlank()) fallback else detail.message
        } catch (_: Exception) {
            fallback
        }
    }

    private fun HttpResponse.retryAfter(): Long? =
        headers.entries.firstOrNull { it.key.equals("Retry-After", ignoreCase = true) }
            ?.value?.trim()?.toLongOrNull()
}

/** Minimal JSON string escaper for the pairing body (codes are word lists). */
private fun jsonEscape(value: String): String {
    val out = StringBuilder(value.length + 2)
    for (ch in value) {
        when (ch) {
            '"' -> out.append("\\\"")
            '\\' -> out.append("\\\\")
            '\n' -> out.append("\\n")
            '\r' -> out.append("\\r")
            '\t' -> out.append("\\t")
            else -> if (ch < ' ') out.append("\\u%04x".format(ch.code)) else out.append(ch)
        }
    }
    return out.toString()
}

/**
 * Real HTTPS transport over [HttpsURLConnection].
 *
 * @param baseUrl `https://host:port` of the receiver.
 * @param tls pinned identity from [pinnedSocketFactory], or null to use
 *   the platform trust store (operator-installed workstation CA).
 * @param allowLoopbackHttp explicit opt-in for `http://` development
 *   against loopback only; any non-loopback http URL is refused loudly.
 */
class HttpsTransport(
    baseUrl: String,
    private val tls: PinnedTls? = null,
    private val allowLoopbackHttp: Boolean = false,
    private val connectTimeoutMillis: Int = 10_000,
    private val readTimeoutMillis: Int = 60_000,
) : HttpTransport {
    private val base = URI(baseUrl.trimEnd('/'))

    init {
        require(base.scheme == "https" || base.scheme == "http") {
            "base URL must be http(s): $baseUrl"
        }
        require(!base.host.isNullOrBlank()) { "base URL needs a host: $baseUrl" }
        if (base.scheme == "http") {
            require(allowLoopbackHttp) { "http is only allowed with an explicit loopback opt-in" }
            require(isLoopbackHost(base.host)) { "http is only allowed for loopback hosts" }
        }
    }

    override fun exchange(request: HttpRequest): HttpResponse {
        val url = base.resolve(request.path).toURL()
        val connection = url.openConnection()
        try {
            if (connection is HttpsURLConnection && tls != null) {
                connection.sslSocketFactory = tls.socketFactory
                connection.hostnameVerifier = tls.hostnameVerifier
            }
            connection.connectTimeout = connectTimeoutMillis
            connection.readTimeout = readTimeoutMillis
            if (connection is HttpURLConnection) {
                connection.requestMethod = request.method
                connection.doInput = true
            }
            for ((name, value) in request.headers) connection.setRequestProperty(name, value)
            val body = request.body
            if (body != null && connection is HttpURLConnection) {
                connection.doOutput = true
                connection.setFixedLengthStreamingMode(body.size)
                connection.outputStream.use { it.write(body) }
            }
            val status = (connection as? HttpURLConnection)?.responseCode ?: 0
            val stream = try {
                connection.inputStream
            } catch (_: IOException) {
                (connection as? HttpURLConnection)?.errorStream
            }
            val responseBody = stream?.use { it.readBytes() } ?: ByteArray(0)
            val headers = connection.headerFields
                .filterKeys { it != null }
                .mapValues { it.value.firstOrNull().orEmpty() }
            return HttpResponse(status, responseBody, headers)
        } catch (e: TransportException) {
            throw e
        } catch (e: IOException) {
            throw TransportException("request to ${base.host} failed: ${e.message}", e)
        } finally {
            (connection as? HttpURLConnection)?.disconnect()
        }
    }

    private fun isLoopbackHost(host: String): Boolean {
        val clean = host.trim().lowercase().trim('[', ']')
        return clean == "localhost" || clean == "127.0.0.1" || clean == "::1"
    }
}
