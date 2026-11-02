package org.anastylos.capture.video

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.anastylos.capture.sync.GuidedControlClient
import org.anastylos.capture.sync.HttpResponse

/** The receiver's view of one uploaded video (additive routes, capture-companion/receiver/README.md). */
data class VideoStatus(
    val videoId: String,
    val status: String,
    val chunkCount: Int,
    val chunkBytes: Long,
    val received: Set<Int>,
) {
    val complete: Boolean get() = status == "complete"
}

sealed interface VideoCall<out T> {
    data class Ok<T>(val value: T) : VideoCall<T>

    /** The server answered and said no; retrying the same bytes will not help. */
    data class Rejected(val code: String, val message: String) : VideoCall<Nothing>

    /** No answer, or an answer that says "try again later". */
    data class Retry(val reason: String) : VideoCall<Nothing>
}

@Serializable
private data class VideoBody(
    @SerialName("video_id") val videoId: String = "",
    val status: String = "",
    @SerialName("chunk_count") val chunkCount: Int = 0,
    @SerialName("chunk_bytes") val chunkBytes: Long = 0,
    val received: List<Int> = emptyList(),
)

@Serializable
private data class VideoErrorBody(val error: VideoErrorDetail? = null)

@Serializable
private data class VideoErrorDetail(val code: String = "", val message: String = "")

private val VideoJson = Json { ignoreUnknownKeys = true }

/**
 * Wire client for the receiver's chunked video routes, through
 * [GuidedControlClient], the one owner of guided endpoint paths, with the guided Bearer token.
 */
class VideoUploadClient(private val client: GuidedControlClient) {
    fun declare(token: String, sessionId: String, sha256: String, size: Long): VideoCall<VideoStatus> =
        call { client.videoDeclare(token, sessionId, buildJsonObject { put("sha256", sha256); put("size", size) }.toString()) }

    fun status(token: String, sessionId: String, videoId: String): VideoCall<VideoStatus> =
        call { client.videoStatus(token, sessionId, videoId) }

    fun putChunk(token: String, sessionId: String, videoId: String, index: Int, bytes: ByteArray): VideoCall<VideoStatus> =
        call { client.videoPutChunk(token, sessionId, videoId, index, bytes) }

    private fun call(exchange: () -> HttpResponse): VideoCall<VideoStatus> {
        val response = try {
            exchange()
        } catch (e: org.anastylos.capture.sync.TransportException) {
            return VideoCall.Retry(e.message ?: "network error")
        }
        return when {
            response.status in 200..201 -> parse(response)
            response.status == 429 || response.status >= 500 -> VideoCall.Retry("receiver busy (${response.status})")
            else -> rejected(response)
        }
    }

    private fun parse(response: HttpResponse): VideoCall<VideoStatus> = try {
        val body = VideoJson.decodeFromString(VideoBody.serializer(), response.body.toString(Charsets.UTF_8))
        VideoCall.Ok(VideoStatus(body.videoId, body.status, body.chunkCount, body.chunkBytes, body.received.toSet()))
    } catch (_: Exception) {
        VideoCall.Retry("unreadable answer from the workstation")
    }

    private fun rejected(response: HttpResponse): VideoCall<VideoStatus> {
        val detail = try {
            VideoJson.decodeFromString(VideoErrorBody.serializer(), response.body.toString(Charsets.UTF_8)).error
        } catch (_: Exception) {
            null
        }
        return VideoCall.Rejected(detail?.code ?: "http_${response.status}", detail?.message ?: "rejected (${response.status})")
    }
}
